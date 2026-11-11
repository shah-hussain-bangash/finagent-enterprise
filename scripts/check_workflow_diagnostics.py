"""Isolated diagnostics tests. Workflow reads and route identity are mocked."""

# ruff: noqa: E402

import copy
import importlib
import json
import sys
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import UUID

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "src"))

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from finagent.api import deps
from finagent.core.config import AuthMode
from finagent.core.errors import AppError, ErrorCode, not_found
from finagent.domain.states import RUN_TERMINAL, RunStatus
from finagent.security.authorization.policy import (
    Permission,
    PrincipalContext,
    PrincipalKind,
    Role,
    require,
)
from finagent.services.workflow_service import diagnostics as d

ops_module = importlib.import_module("finagent.api.routers.ops")

RID = str(UUID(int=3))
PATH = "/api/v1/workflows/" + RID + "/diagnostics"


def actor(
    role=Role.ANALYST,
    mfa=True,
):
    return PrincipalContext(
        principal_id=UUID(int=1),
        tenant_id=UUID(int=2),
        kind=PrincipalKind.USER,
        roles=frozenset({role}),
        mfa_satisfied=mfa,
    )


def facts(
    status="RUNNING",
    **extra,
):
    return dict(
        run_id=RID,
        status=status,
        attempt=1,
        max_attempts=3,
        cancel_requested=False,
        blockers=[],
        **extra,
    )


class DiagnosticChecks(unittest.TestCase):
    def test_all_states_have_guidance(self):
        for status in RunStatus:
            with self.subTest(status=status.value):
                body = d.summarize_run(
                    facts(status.value),
                    actor(),
                )

                self.assertEqual(
                    body["status"],
                    status.value,
                )

                self.assertEqual(
                    body["terminal"],
                    status in RUN_TERMINAL,
                )

                self.assertTrue(body["guidance"])
                self.assertTrue(body["read_only"])

                self.assertEqual(
                    body["worker_liveness"],
                    "NOT_ASSESSED",
                )

                self.assertEqual(
                    body["case_completion"],
                    "NOT_ASSESSED",
                )

    def test_uncertainty_takes_precedence_for_all_states(self):
        for status in RunStatus:
            view = facts(status.value)

            view["cancel_requested"] = True

            view["blockers"] = [
                {
                    "code": "OUTCOME_UNCERTAIN",
                    "operation_id": str(UUID(int=5)),
                }
            ]

            body = d.summarize_run(
                view,
                actor(),
            )

            self.assertEqual(
                body["attention"],
                "RECONCILIATION_REQUIRED",
            )

            self.assertEqual(
                body["uncertain_operation_count"],
                1,
            )

            self.assertEqual(
                [h["action"] for h in body["recovery_hints"]],
                ["RECONCILE_EXTERNAL_OPERATION"],
            )

    def test_uncertain_status_without_link_still_requires_reconciliation(self):
        body = d.summarize_run(
            facts("OUTCOME_UNCERTAIN"),
            actor(),
        )

        self.assertEqual(
            body["uncertain_operation_count"],
            0,
        )

        self.assertEqual(
            body["attention"],
            "RECONCILIATION_REQUIRED",
        )

    def test_terminal_runs_have_no_retry_hint(self):
        for status in RUN_TERMINAL:
            self.assertEqual(
                d.summarize_run(
                    facts(status.value),
                    actor(),
                )["recovery_hints"],
                [],
            )

    def test_cancellation_request_is_not_completion(self):
        view = facts()
        view["cancel_requested"] = True

        body = d.summarize_run(
            view,
            actor(),
        )

        self.assertEqual(
            body["attention"],
            "CANCELLATION_REQUESTED",
        )

        self.assertFalse(
            body["terminal"],
        )

        self.assertEqual(
            body["recovery_hints"],
            [],
        )

    def test_state_specific_hints(self):
        for status, action in (
            ("WAITING_EVIDENCE", "EVIDENCE_SUPPLIED"),
            ("WAITING_REVIEW", "REANALYZE"),
            ("HUMAN_REVIEW", "RETRY"),
        ):
            hints = d.summarize_run(
                facts(status),
                actor(),
            )["recovery_hints"]

            self.assertEqual(
                [h["action"] for h in hints],
                [
                    action,
                    "CANCEL",
                ],
            )

            self.assertTrue(all(h["business_preconditions"] == "NOT_EVALUATED" for h in hints))

            self.assertTrue(all(h["execution_authorized"] is False for h in hints))

        self.assertEqual(
            [
                h["action"]
                for h in d.summarize_run(
                    facts("RETRYING"),
                    actor(),
                )["recovery_hints"]
            ],
            ["CANCEL"],
        )

    def test_permission_hints_match_policy(self):
        for role in Role:
            for mfa in (False, True):
                ctx = actor(
                    role,
                    mfa,
                )

                for status in (
                    "HUMAN_REVIEW",
                    "WAITING_EVIDENCE",
                    "OUTCOME_UNCERTAIN",
                ):
                    for hint in d.summarize_run(
                        facts(status),
                        ctx,
                    )["recovery_hints"]:
                        expected = "SATISFIED"

                        try:
                            require(
                                ctx,
                                Permission[hint["permission"]],
                            )

                        except AppError as exc:
                            expected = exc.code.value

                        self.assertEqual(
                            hint["permission_gate"],
                            expected,
                        )

    def test_retry_permission_precedence(self):
        ctx = replace(
            actor(mfa=False),
            roles=frozenset(
                {
                    Role.ANALYST,
                    Role.PLATFORM_OPERATOR,
                }
            ),
            step_up=frozenset(
                {
                    Permission.OPERATION_RECONCILE,
                }
            ),
        )

        hint = d.summarize_run(
            facts("HUMAN_REVIEW"),
            ctx,
        )["recovery_hints"][0]

        self.assertEqual(
            hint["permission"],
            "OPERATION_RECONCILE",
        )

        self.assertEqual(
            hint["permission_gate"],
            "MFA_REQUIRED",
        )

    def test_attempt_limit_is_observation_only(self):
        view = facts("HUMAN_REVIEW")
        view["attempt"] = 3

        body = d.summarize_run(
            view,
            actor(),
        )

        self.assertTrue(body["attempts"]["at_or_above_configured_max"])

        self.assertTrue(all(not h["execution_authorized"] for h in body["recovery_hints"]))

    def test_input_not_changed_or_raw_details_exposed(self):
        view = facts(
            status_reason="PRIVATE_SENTINEL",
            case_id="PRIVATE_SENTINEL",
            steps=[
                {
                    "output_ref": "PRIVATE_SENTINEL",
                }
            ],
            history=[
                {
                    "reason": "PRIVATE_SENTINEL",
                }
            ],
        )

        view["blockers"] = [
            {
                "code": "OTHER",
                "reason": "PRIVATE_SENTINEL",
            }
        ]

        before = copy.deepcopy(view)

        body = d.summarize_run(
            view,
            actor(),
        )

        self.assertNotIn(
            "PRIVATE_SENTINEL",
            json.dumps(body),
        )

        self.assertEqual(
            view,
            before,
        )

        self.assertNotIn(
            "case_id",
            body,
        )

    def test_invalid_facts_not_silently_accepted(self):
        for key, value in (
            ("status", "UNKNOWN"),
            ("attempt", -1),
            ("max_attempts", 0),
            ("cancel_requested", "false"),
        ):
            view = facts()
            view[key] = value

            with self.assertRaises(ValidationError):
                d.summarize_run(
                    view,
                    actor(),
                )

    def test_uses_existing_scoped_reader(self):
        ctx = actor()

        with (
            Session() as session,
            patch.object(
                d.runs,
                "get_run",
                return_value=facts(),
            ) as read,
        ):
            body = d.get_diagnostics(
                session,
                ctx,
                RID,
            )

            read.assert_called_once_with(
                session,
                ctx,
                RID,
            )

            self.assertEqual(
                body["run_id"],
                RID,
            )

    def test_invisible_run_error_preserved(self):
        with (
            Session() as session,
            patch.object(
                d.runs,
                "get_run",
                side_effect=not_found(),
            ),
        ):
            with self.assertRaises(AppError) as caught:
                d.get_diagnostics(
                    session,
                    actor(),
                    RID,
                )

            self.assertEqual(
                caught.exception.status_code,
                404,
            )

    def test_denied_principal_never_loads_run(self):
        with (
            Session() as session,
            patch.object(
                d.runs,
                "get_run",
            ) as read,
        ):
            for ctx in (
                actor(Role.TENANT_ADMIN),
                replace(
                    actor(),
                    kind=PrincipalKind.INTEGRATION,
                ),
            ):
                with self.assertRaises(AppError):
                    d.get_diagnostics(
                        session,
                        ctx,
                        RID,
                    )

            read.assert_not_called()

    def test_combined_role_read_mfa_has_no_operator_fallback(self):
        ctx = replace(
            actor(mfa=False),
            roles=frozenset(
                {
                    Role.ANALYST,
                    Role.PLATFORM_OPERATOR,
                }
            ),
            step_up=frozenset(
                {
                    Permission.CASE_READ,
                }
            ),
        )

        with (
            Session() as session,
            patch.object(
                d.runs,
                "get_run",
            ) as read,
        ):
            with self.assertRaises(AppError) as caught:
                d.get_diagnostics(
                    session,
                    ctx,
                    RID,
                )

            self.assertEqual(
                caught.exception.code,
                ErrorCode.MFA_REQUIRED,
            )

            read.assert_not_called()

    def setUp(self):
        self.app = FastAPI()
        self.app.include_router(ops_module.ops)

        async def errors(
            request,
            exc,
        ):
            return JSONResponse(
                {"error": {"code": exc.code.value}},
                status_code=exc.status_code,
            )

        self.app.add_exception_handler(
            AppError,
            errors,
        )

        self.app.dependency_overrides[deps.get_session_factory] = lambda: object()

        self.app.dependency_overrides[deps.get_settings_dep] = lambda: SimpleNamespace(
            auth_mode=AuthMode.LOCAL
        )

    def test_route_cache_headers_and_readonly_transaction(self):
        ctx = actor()

        self.app.dependency_overrides[deps.get_principal] = lambda: ctx

        session = MagicMock()

        with (
            patch.object(
                ops_module,
                "transaction",
            ) as tx,
            patch.object(
                ops_module,
                "get_diagnostics",
                return_value={"read_only": True},
            ) as service,
        ):
            tx.return_value.__enter__.return_value = session

            with TestClient(self.app) as client:
                response = client.get(PATH)

            service.assert_called_once_with(
                session,
                ctx,
                RID,
            )

            self.assertEqual(
                str(session.execute.call_args.args[0]),
                ("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"),
            )

            self.assertEqual(
                response.status_code,
                200,
            )

            self.assertIn(
                "no-store",
                response.headers["cache-control"],
            )

            self.assertEqual(
                response.headers["pragma"],
                "no-cache",
            )

    def test_route_anonymous_rejected_before_service(self):
        with patch.object(
            ops_module,
            "get_diagnostics",
        ) as service:
            with TestClient(self.app) as client:
                self.assertEqual(
                    client.get(PATH).status_code,
                    401,
                )

            service.assert_not_called()

    def test_route_write_methods_unavailable(self):
        self.app.dependency_overrides[deps.get_principal] = lambda: actor()

        with TestClient(self.app) as client:
            for method in (
                "POST",
                "PUT",
                "PATCH",
                "DELETE",
            ):
                self.assertEqual(
                    client.request(
                        method,
                        PATH,
                    ).status_code,
                    405,
                )

        schema = self.app.openapi()["paths"]["/api/v1/workflows/{run_id}/diagnostics"]

        self.assertEqual(
            set(schema),
            {"get"},
        )


if __name__ == "__main__":
    with patch.object(
        Engine,
        "connect",
        side_effect=AssertionError("DATABASE_NOT_ALLOWED"),
    ):
        result = unittest.TextTestRunner(verbosity=1).run(
            unittest.defaultTestLoader.loadTestsFromTestCase(DiagnosticChecks)
        )

    print("WORKFLOW_DIAGNOSTIC_TESTS_RUN=" + str(result.testsRun))

    print("WORKFLOW_LOOKUP_AND_ROUTE_IDENTITY=MOCKED")

    print("DATABASE_INTEGRATION=NOT_TESTED")

    raise SystemExit(0 if result.wasSuccessful() else 2)
