"""Standalone unit/route checks. No pytest fixtures or live database are used."""

# ruff: noqa: E402
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
from finagent.security.authorization.access_explanation import explain_access
from finagent.security.authorization.policy import (
    DelegatedRole,
    Permission,
    PrincipalContext,
    PrincipalKind,
    Role,
    require,
)

auth_module = importlib.import_module("finagent.api.routers.auth")
UID, TID = UUID(int=1), UUID(int=2)


def principal(role=None, mfa=False):
    return PrincipalContext(
        principal_id=UID,
        tenant_id=TID,
        kind=PrincipalKind.USER,
        roles=frozenset() if role is None else frozenset({role}),
        mfa_satisfied=mfa,
    )


def gate(snapshot, permission):
    return next(row["permission_gate"] for row in snapshot["permissions"] if row["name"] == permission.name)


class AccessChecks(unittest.TestCase):
    def setUp(self):
        self.app = FastAPI()
        self.app.include_router(auth_module.auth)

        async def handle_error(request, error):
            return JSONResponse(
                {"error": {"code": error.code.value}},
                status_code=error.status_code,
            )

        self.app.add_exception_handler(AppError, handle_error)
        self.app.dependency_overrides[deps.get_session_factory] = lambda: object()
        self.app.dependency_overrides[deps.get_settings_dep] = lambda: SimpleNamespace(
            auth_mode=AuthMode.LOCAL
        )

    def test_every_role_permission_and_mfa_matches_existing_policy(self):
        for role in Role:
            for mfa in (False, True):
                ctx = principal(role, mfa)
                snapshot = explain_access(ctx)

                for permission in Permission:
                    with self.subTest(
                        role=role.name,
                        mfa=mfa,
                        permission=permission.name,
                    ):
                        expected = "SATISFIED"
                        try:
                            require(ctx, permission)
                        except AppError as exc:
                            expected = exc.code.value

                        self.assertEqual(gate(snapshot, permission), expected)

    def test_no_roles_deny(self):
        self.assertTrue(
            all(row["permission_gate"] == "FORBIDDEN" for row in explain_access(principal())["permissions"])
        )

    def test_operator_has_no_case_access(self):
        result = explain_access(principal(Role.PLATFORM_OPERATOR, True))
        self.assertEqual(gate(result, Permission.CASE_READ), "FORBIDDEN")
        self.assertEqual(result["case_row_scope"], "NONE")

    def test_mfa_distinguished_from_missing_permission(self):
        self.assertEqual(
            gate(explain_access(principal(Role.TENANT_ADMIN)), Permission.TENANT_ADMIN),
            "MFA_REQUIRED",
        )
        self.assertEqual(
            gate(explain_access(principal(Role.ANALYST)), Permission.TENANT_ADMIN),
            "FORBIDDEN",
        )

    def test_nonhuman_refused(self):
        ctx = replace(principal(), kind=PrincipalKind.INTEGRATION)
        with self.assertRaises(AppError) as error:
            explain_access(ctx)
        self.assertEqual(error.exception.code, ErrorCode.UNAUTHENTICATED)

    def test_delegated_role_not_reported_as_direct(self):
        ctx = replace(
            principal(Role.REVIEWER, True),
            assigned_roles=frozenset(),
            delegations=(DelegatedRole(Role.REVIEWER, UUID(int=3), UUID(int=4)),),
        )
        result = explain_access(ctx)
        self.assertEqual(result["direct_roles"], [])
        self.assertEqual(result["effective_roles"], ["REVIEWER"])
        self.assertTrue(result["resource_authorization_required"])

    def test_snapshot_is_serializable_and_does_not_modify_context(self):
        ctx = principal(Role.ANALYST)
        original = repr(ctx)
        json.dumps(explain_access(ctx))
        self.assertEqual(repr(ctx), original)

    def test_anonymous_http_rejected_by_existing_dependency(self):
        with TestClient(self.app) as client:
            response = client.get("/api/v1/auth/access")
        self.assertEqual(response.status_code, 401)

    def test_http_uses_current_principal_and_no_store(self):
        ctx = principal(Role.PLATFORM_OPERATOR, True)
        self.app.dependency_overrides[deps.get_principal] = lambda: ctx

        with (
            patch.object(auth_module, "sign_in_identity", return_value={}) as identity,
            TestClient(self.app) as client,
        ):
            response = client.get("/api/v1/auth/access?principal_id=other&tenant_id=other")

        identity.assert_called_once()
        self.assertIs(identity.call_args.kwargs["ctx"], ctx)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["principal_id"], str(UID))
        self.assertEqual(response.json()["tenant_id"], str(TID))
        self.assertIn("no-store", response.headers["cache-control"])

    def test_existing_identity_rejection_is_not_bypassed(self):
        self.app.dependency_overrides[deps.get_principal] = lambda: principal(Role.ANALYST)

        with (
            patch.object(
                auth_module,
                "sign_in_identity",
                side_effect=AppError(ErrorCode.UNAUTHENTICATED),
            ),
            TestClient(self.app) as client,
        ):
            response = client.get("/api/v1/auth/access")

        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    with patch.object(
        Engine,
        "connect",
        side_effect=AssertionError("DATABASE_NOT_ALLOWED_IN_UNIT_CHECKS"),
    ):
        result = unittest.TextTestRunner(verbosity=1).run(
            unittest.defaultTestLoader.loadTestsFromTestCase(AccessChecks)
        )

    print("ACCESS_POLICY_CASES=" + str(len(Role) * 2 * len(Permission)))
    print("LIVE_AUTH_DATABASE_INTEGRATION=NOT_TESTED")
    raise SystemExit(0 if result.wasSuccessful() else 2)
