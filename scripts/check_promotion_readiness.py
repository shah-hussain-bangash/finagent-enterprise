# ruff: noqa: E402  # bootstrap project path and connection guard before application imports
"""Standalone checks: mocked DB/identity, real evidence validator on temporary fixtures."""

import copy
import importlib
import json
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import UUID

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "src"))
from sqlalchemy.engine import Engine

DB_GUARD = patch.object(Engine, "connect", side_effect=AssertionError("DATABASE_FORBIDDEN"))
DB_GUARD.start()
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from finagent.api import deps
from finagent.core.errors import AppError, ErrorCode
from finagent.database.models import ConfigurationVersion, EvaluationRun
from finagent.domain.fingerprint import fingerprint
from finagent.evaluation.dataset_control import read_dataset, record_for
from finagent.security.authorization.policy import Permission, PrincipalContext, PrincipalKind, Role, require
from finagent.services.governance_service import config as config_service
from finagent.services.governance_service import promotion_readiness as service

router = importlib.import_module("finagent.api.routers.governance")
TENANT, VID, EID = UUID(int=1), UUID(int=2), UUID(int=3)
PATH = "/api/v1/admin/config-versions/" + str(VID) + "/promotion-readiness"


def actor(role=Role.TENANT_ADMIN, mfa=True):
    return PrincipalContext(UUID(int=4), TENANT, PrincipalKind.USER, frozenset({role}), mfa_satisfied=mfa)


class ReadinessChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name) / "datasets"
        self.folder.mkdir()
        entries, history = {}, []
        for index, split in enumerate(("development", "validation", "locked_release"), 1):
            name, sid = f"readiness-{index}", f"scenario-{index}"
            family = f"family-{index}"
            data = {
                "dataset": name,
                "version": "1.0.0",
                "window": {},
                "capability_scenarios": [{"id": sid, "family": family, "input": str(index)}],
                "budget_scenarios": [],
                "retrieval_scenarios": [],
            }
            lineage = {sid: {"family": family, "customer": f"customer-{index}", "story": f"story-{index}"}}
            entry = record_for(name, data, split, lineage, "Temporary validation fixture")
            entries[name] = entry
            history.append(entry)
            (self.folder / (name + ".json")).write_text(json.dumps(data), encoding="utf-8")
        catalog = {"schema_version": 1, "datasets": entries, "history": history}
        (self.folder.parent / "dataset-catalog.json").write_text(json.dumps(catalog), encoding="utf-8")
        patcher = patch.object(config_service, "_EVALUATION_DATASETS", self.folder)
        patcher.start()
        self.addCleanup(patcher.stop)
        data, control = read_dataset(self.folder, "readiness-3", purpose="release")
        self.target = ConfigurationVersion(
            id=VID,
            tenant_id=TENANT,
            config_hash="a" * 64,
            status="CANDIDATE",
            content={"hidden": "PRIVATE_SENTINEL"},
        )
        self.evidence = EvaluationRun(
            id=EID,
            tenant_id=TENANT,
            config_hash="a" * 64,
            dataset="readiness-3",
            dataset_version=data["version"],
            dataset_hash=fingerprint({**data, "_dataset_control": control}),
            gate="PASS_PROPOSED",
            results=[
                {
                    "evaluation_context": {"evaluation_purpose": "release", "dataset_control": control},
                    "hidden": "PRIVATE_SENTINEL",
                }
            ],
            metrics={},
        )
        self.session = MagicMock(spec=Session)
        self.session.scalar.side_effect = [self.target, self.evidence]

    def preview(self, ctx=None):
        return service.get_readiness(self.session, ctx or actor(), VID, EID)

    def blocked(self, reason):
        result = self.preview()
        self.assertEqual(result["readiness"], "BLOCKED")
        self.assertIn(reason, result["blocking_reasons"])

    def test_valid_release_is_not_approval(self):
        result = self.preview()
        self.assertEqual(result["readiness"], "CHECKS_SATISFIED_PROPOSED_ONLY")
        self.assertTrue(result["technical_checks_satisfied"])
        self.assertFalse(result["promotion_executed"])
        self.assertFalse(result["execution_authorized"])
        self.assertEqual(result["production_acceptance"], "NOT_ASSESSED")
        self.assertEqual(result["change_reason"], "NOT_EVALUATED")

    def test_development_evidence_rejected(self):
        self.evidence.results[0]["evaluation_context"]["evaluation_purpose"] = "development"
        self.blocked("RELEASE_EVIDENCE_REJECTED")

    def test_validation_evidence_rejected(self):
        self.evidence.results[0]["evaluation_context"]["evaluation_purpose"] = "validation"
        self.blocked("RELEASE_EVIDENCE_REJECTED")

    def test_dataset_hash_change_rejected(self):
        self.evidence.dataset_hash = "b" * 64
        self.blocked("RELEASE_EVIDENCE_REJECTED")

    def test_dataset_version_change_rejected(self):
        self.evidence.dataset_version = "0.0.1"
        self.blocked("RELEASE_EVIDENCE_REJECTED")

    def test_catalog_snapshot_change_rejected(self):
        self.evidence.results[0]["evaluation_context"]["dataset_control"] = {}
        self.blocked("RELEASE_EVIDENCE_REJECTED")

    def test_dataset_content_change_rejected(self):
        path = self.folder / "readiness-3.json"
        content = json.loads(path.read_text())
        content["version"] = "2.0.0"
        path.write_text(json.dumps(content), encoding="utf-8")
        self.blocked("RELEASE_EVIDENCE_REJECTED")

    def test_contaminated_locked_release_rejected(self):
        path = self.folder.parent / "dataset-catalog.json"
        catalog = json.loads(path.read_text())
        catalog["history"][-1]["contaminated"] = True
        catalog["datasets"]["readiness-3"]["contaminated"] = True
        path.write_text(json.dumps(catalog), encoding="utf-8")
        self.blocked("RELEASE_EVIDENCE_REJECTED")

    def test_configuration_hash_mismatch(self):
        self.evidence.config_hash = "b" * 64
        self.blocked("CONFIGURATION_HASH_MISMATCH")

    def test_nonpassing_gates(self):
        for gate in ("FAIL_PROPOSED", "INCOMPLETE", "UNRECOGNIZED"):
            self.evidence.gate = gate
            self.session.scalar.side_effect = [self.target, self.evidence]
            self.blocked("EVALUATION_GATE_NOT_PASS_PROPOSED")

    def test_already_promoted(self):
        self.target.status = "PROMOTED"
        self.blocked("CONFIGURATION_ALREADY_PROMOTED")

    def test_missing_or_cross_tenant_rows_hidden(self):
        for target, evidence in (
            (None, self.evidence),
            (self.target, None),
            (ConfigurationVersion(id=VID, tenant_id=UUID(int=99)), self.evidence),
            (self.target, EvaluationRun(id=EID, tenant_id=UUID(int=99))),
        ):
            self.session.scalar.side_effect = [target, evidence]
            with self.assertRaises(AppError) as caught:
                self.preview()
            self.assertEqual(caught.exception.status_code, 404)

    def test_all_role_mfa_admission_combinations(self):
        for role in Role:
            for mfa in (False, True):
                ctx = actor(role, mfa)
                permission = (
                    Permission.TENANT_ADMIN
                    if Permission.TENANT_ADMIN in ctx.permissions
                    else Permission.EVALUATION_RUN
                )
                try:
                    require(ctx, permission)
                except AppError as expected:
                    with self.assertRaises(AppError) as actual:
                        service.require_preview_access(ctx)
                    self.assertEqual(actual.exception.code, expected.code)
                else:
                    service.require_preview_access(ctx)

    def test_operator_can_preview_but_cannot_promote(self):
        result = self.preview(actor(Role.PLATFORM_OPERATOR))
        self.assertEqual(result["promotion_permission_gate"], "FORBIDDEN")
        self.assertTrue(result["technical_checks_satisfied"])
        self.assertIn("PROMOTION_FORBIDDEN", result["blocking_reasons"])

    def test_combined_roles_cannot_bypass_admin_mfa(self):
        ctx = replace(actor(mfa=False), roles=frozenset({Role.TENANT_ADMIN, Role.PLATFORM_OPERATOR}))
        with self.assertRaises(AppError) as caught:
            self.preview(ctx)
        self.assertEqual(caught.exception.code, ErrorCode.MFA_REQUIRED)
        self.session.scalar.assert_not_called()

    def test_nonhuman_is_rejected_before_queries(self):
        with self.assertRaises(AppError):
            self.preview(replace(actor(), kind=PrincipalKind.INTEGRATION))
        self.session.scalar.assert_not_called()

    def test_queries_are_tenant_scoped_and_not_locking(self):
        self.preview()
        self.assertEqual(self.session.scalar.call_count, 2)
        for call, identity in zip(self.session.scalar.call_args_list, (VID, EID), strict=True):
            statement = call.args[0]
            sql = str(statement)
            self.assertIn("tenant_id =", sql)
            self.assertIn(TENANT, statement.compile().params.values())
            self.assertIn(identity, statement.compile().params.values())
            self.assertNotIn("FOR UPDATE", sql)
        self.session.no_autoflush.__enter__.assert_called_once()

    def test_response_does_not_mutate_or_expose_raw_content(self):
        original = copy.deepcopy(self.evidence.results)
        result = self.preview()
        self.assertNotIn("PRIVATE_SENTINEL", json.dumps(result))
        self.assertEqual(self.evidence.results, original)
        self.assertEqual(self.target.status, "CANDIDATE")
        for method in ("add", "delete", "flush", "commit"):
            getattr(self.session, method).assert_not_called()

    def test_unexpected_validator_failure_propagates(self):
        with (
            patch.object(config_service, "require_release_evidence", side_effect=RuntimeError("test")),
            self.assertRaises(RuntimeError),
        ):
            self.preview()

    def request(self, ctx, method="GET", query=None):
        app = FastAPI()
        app.include_router(router.governance)
        app.dependency_overrides[deps.get_session_factory] = lambda: object()
        if ctx is not None:
            app.dependency_overrides[deps.get_principal] = lambda: ctx
        else:

            def anonymous():
                raise AppError(ErrorCode.UNAUTHENTICATED)

            app.dependency_overrides[deps.get_principal] = anonymous

        async def error(request, exc):
            return JSONResponse({"error": {"code": exc.code.value}}, status_code=exc.status_code)

        app.add_exception_handler(AppError, error)
        with patch.object(router, "transaction") as tx:
            tx.return_value.__enter__.return_value = self.session
            with TestClient(app) as client:
                response = client.request(
                    method, PATH, params={"evaluation_id": str(EID)} if query is None else query
                )
        return response

    def test_http_route_success_and_read_only_sql(self):
        response = self.request(actor())
        self.assertEqual(response.status_code, 200)
        self.assertIn("no-store", response.headers["cache-control"])
        self.assertEqual(response.headers["pragma"], "no-cache")
        self.assertEqual(
            str(self.session.execute.call_args.args[0]),
            "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY",
        )

    def test_http_anonymous_rejected(self):
        self.assertEqual(self.request(None).status_code, 401)
        self.session.scalar.assert_not_called()

    def test_http_cross_tenant_hidden(self):
        self.evidence.tenant_id = UUID(int=99)
        self.assertEqual(self.request(actor()).status_code, 404)

    def test_http_evaluation_id_required_and_validated(self):
        for query in ({}, {"evaluation_id": "invalid"}):
            self.assertEqual(self.request(actor(), query=query).status_code, 422)
        self.session.scalar.assert_not_called()

    def test_http_write_methods_not_available(self):
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            self.assertEqual(self.request(actor(), method).status_code, 405)
        self.session.scalar.assert_not_called()

    def test_http_operator_advisory_response(self):
        response = self.request(actor(Role.PLATFORM_OPERATOR))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["readiness"], "BLOCKED")
        self.assertFalse(response.json()["execution_authorized"])


if __name__ == "__main__":
    try:
        result = unittest.TextTestRunner(verbosity=1).run(
            unittest.defaultTestLoader.loadTestsFromTestCase(ReadinessChecks)
        )
    finally:
        DB_GUARD.stop()
    print("PROMOTION_READINESS_TESTS_RUN=" + str(result.testsRun))
    print("DATABASE_AND_HTTP_IDENTITY=MOCKED")
    print("RELEASE_EVIDENCE_VALIDATOR=REAL_WITH_TEMPORARY_FIXTURES")
    raise SystemExit(0 if result.wasSuccessful() and result.testsRun == 25 else 2)
