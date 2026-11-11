# ruff: noqa: E402  # bootstrap project path and connection guard before application imports
"""Isolated catalog checks; identity is mocked and database connections are blocked."""

import ast
import hashlib
import importlib
import json
import sys
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "src"))

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine

from finagent.api import deps
from finagent.core.config import AuthMode
from finagent.core.errors import AppError, ErrorCode
from finagent.security.authorization.policy import (
    DEFAULT_STEP_UP,
    RESOLVER_ONLY_ROLES,
    ROLE_PERMISSIONS,
    Permission,
    PrincipalContext,
    PrincipalKind,
    Role,
    require,
)
from finagent.security.authorization.role_catalog import build_role_catalog

module = importlib.import_module("finagent.api.routers.access_catalog")
URL = "/api/v1/admin/role-permission-catalog"


def actor(role=Role.TENANT_ADMIN, mfa=True):
    return PrincipalContext(
        principal_id=UUID(int=1),
        tenant_id=UUID(int=2),
        kind=PrincipalKind.USER,
        roles=frozenset({role}),
        mfa_satisfied=mfa,
    )


class CatalogChecks(unittest.TestCase):
    def test_all_role_mfa_gates_match_policy(self):
        for role in Role:
            for mfa in (False, True):
                ctx = actor(role, mfa)
                with self.subTest(role=role.name, mfa=mfa):
                    try:
                        require(ctx, Permission.TENANT_ADMIN)
                    except AppError as expected:
                        with self.assertRaises(AppError) as caught:
                            build_role_catalog(ctx)
                        self.assertEqual(caught.exception.code, expected.code)
                    else:
                        self.assertEqual(build_role_catalog(ctx)["schema_version"], 1)

    def test_nonhuman_with_admin_role_is_rejected(self):
        with self.assertRaises(AppError) as caught:
            build_role_catalog(replace(actor(), kind=PrincipalKind.INTEGRATION))
        self.assertEqual(caught.exception.code, ErrorCode.UNAUTHENTICATED)

    def test_exact_role_definitions(self):
        rows = build_role_catalog(actor())["roles"]
        self.assertEqual(len(rows), len(Role))
        self.assertEqual({r["name"] for r in rows}, {r.name for r in Role})
        for row in rows:
            role = Role[row["name"]]
            self.assertEqual(row["value"], role.value)
            self.assertEqual(row["resolver_only"], role in RESOLVER_ONLY_ROLES)
            self.assertEqual(
                row["permission_names"], sorted(p.name for p in ROLE_PERMISSIONS.get(role, frozenset()))
            )

    def test_exact_permission_definitions(self):
        rows = build_role_catalog(actor())["permissions"]
        self.assertEqual(len(rows), len(Permission))
        self.assertEqual({r["name"] for r in rows}, {p.name for p in Permission})
        for row in rows:
            p = Permission[row["name"]]
            self.assertEqual(row["value"], p.value)
            self.assertEqual(row["default_mfa_required"], p in DEFAULT_STEP_UP)
            self.assertEqual(row["current_policy_mfa_required"], p in actor().step_up)

    def test_current_mfa_policy_not_confused_with_default(self):
        ctx = replace(actor(), step_up=frozenset(Permission) - DEFAULT_STEP_UP)
        for row in build_role_catalog(ctx)["permissions"]:
            p = Permission[row["name"]]
            self.assertEqual(row["current_policy_mfa_required"], p in ctx.step_up)
            self.assertEqual(row["default_mfa_required"], p in DEFAULT_STEP_UP)
        self.assertNotEqual(
            build_role_catalog(ctx)["catalog_sha256"],
            build_role_catalog(actor())["catalog_sha256"],
        )

    def test_no_member_or_assignment_payload(self):
        body = build_role_catalog(actor())
        self.assertEqual(
            set(body),
            {
                "schema_version",
                "scope",
                "roles",
                "permissions",
                "notice",
                "catalog_supports_role_assignment",
                "resource_authorization_required",
                "catalog_sha256",
            },
        )
        self.assertFalse(body["catalog_supports_role_assignment"])
        self.assertTrue(body["resource_authorization_required"])

    def test_stable_hash_and_order(self):
        body = build_role_catalog(actor())
        self.assertEqual(body, build_role_catalog(actor()))
        for key in ("roles", "permissions"):
            values = [r["value"] for r in body[key]]
            self.assertEqual(values, sorted(values))
        saved = body.pop("catalog_sha256")
        canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
        self.assertEqual(saved, hashlib.sha256(canonical.encode()).hexdigest())

    def test_mutating_response_cannot_change_policy(self):
        ctx = actor()
        policy_before = dict(ROLE_PERMISSIONS)
        expected = build_role_catalog(ctx)
        changed = build_role_catalog(ctx)
        changed["roles"][0]["permission_names"].clear()
        self.assertEqual(ROLE_PERMISSIONS, policy_before)
        self.assertEqual(build_role_catalog(ctx), expected)

    def setUp(self):
        self.app = FastAPI()
        self.app.include_router(module.access_catalog)

        async def errors(request, exc):
            return JSONResponse(
                {"error": {"code": exc.code.value}},
                status_code=exc.status_code,
            )

        self.app.add_exception_handler(AppError, errors)
        self.app.dependency_overrides[deps.get_session_factory] = lambda: object()
        self.app.dependency_overrides[deps.get_settings_dep] = lambda: SimpleNamespace(
            auth_mode=AuthMode.LOCAL,
        )

    def request(self, ctx, method="GET", suffix=""):
        self.app.dependency_overrides[deps.get_principal] = lambda: ctx
        with (
            patch.object(module, "sign_in_identity", return_value={}) as identity,
            TestClient(self.app) as client,
        ):
            response = client.request(method, URL + suffix)
        if method == "GET":
            identity.assert_called_once()
            self.assertIs(identity.call_args.kwargs["ctx"], ctx)
        return response

    def test_http_admin_and_cache_headers(self):
        r = self.request(actor())
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), build_role_catalog(actor()))
        self.assertIn("no-store", r.headers["cache-control"])
        self.assertEqual(r.headers["pragma"], "no-cache")

    def test_http_operator_denied_and_admin_mfa_required(self):
        for ctx, code, status in (
            (actor(Role.PLATFORM_OPERATOR), "FORBIDDEN", 403),
            (actor(mfa=False), "MFA_REQUIRED", 401),
        ):
            r = self.request(ctx, suffix="?role=TENANT_ADMIN&mfa=true")
            self.assertEqual(r.status_code, status)
            self.assertEqual(r.json()["error"]["code"], code)
            self.assertNotIn("roles", r.json())

    def test_http_anonymous_rejected(self):
        with TestClient(self.app) as client:
            self.assertEqual(client.get(URL).status_code, 401)

    def test_identity_admission_failure_preserved(self):
        self.app.dependency_overrides[deps.get_principal] = lambda: actor()
        with (
            patch.object(module, "sign_in_identity", side_effect=AppError(ErrorCode.UNAUTHENTICATED)),
            TestClient(self.app) as client,
        ):
            self.assertEqual(client.get(URL).status_code, 401)

    def test_write_methods_not_available(self):
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            self.assertEqual(self.request(actor(), method).status_code, 405)

    def test_main_registration_and_openapi(self):
        tree = ast.parse((ROOT / "src/finagent/api/main.py").read_text(encoding="utf-8-sig"))
        calls = [
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "include_router"
            and n.args
            and isinstance(n.args[0], ast.Name)
            and n.args[0].id == "access_catalog"
        ]
        self.assertEqual(len(calls), 1)
        self.assertEqual(set(self.app.openapi()["paths"][URL]), {"get"})


if __name__ == "__main__":
    with patch.object(Engine, "connect", side_effect=AssertionError("DATABASE_NOT_ALLOWED")):
        result = unittest.TextTestRunner(verbosity=1).run(
            unittest.defaultTestLoader.loadTestsFromTestCase(CatalogChecks)
        )
    print("ROLE_CATALOG_CHECKS_RUN=" + str(result.testsRun))
    print("ROLE_MFA_COMBINATIONS=" + str(len(Role) * 2))
    print("IDENTITY_IN_ROUTE_TESTS=MOCKED")
    raise SystemExit(0 if result.wasSuccessful() else 2)
