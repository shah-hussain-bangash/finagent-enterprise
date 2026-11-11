"""Durable critical incident response across actual PostgreSQL and gateways."""

import threading
import uuid
from dataclasses import replace
from typing import Any

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from finagent.agents.providers import MockModelProvider
from finagent.agents.registry import CapabilityUnavailableError
from finagent.agents.runner import run_simple
from finagent.core.errors import AppError
from finagent.database.models import (
    CapabilityIncident,
    CapabilityIncidentEvent,
    ModelRequest,
)
from finagent.database.session import session_factory, transaction
from finagent.integrations.factory import ConnectorFactory
from finagent.integrations.notifications.port import (
    LocalNotificationAdapter,
    NotificationError,
)
from finagent.integrations.tool_gateway.gateway import (
    ExecutionContext,
    ToolDeniedError,
    ToolGateway,
)
from finagent.security.authorization.policy import Role
from finagent.services.governance_service import capability_incidents as incidents
from tests.agent_helpers import GATHER_TOOLS, Prepared, prepared_case

pytestmark = [pytest.mark.integration, pytest.mark.req("AER-037")]


@pytest.fixture
def prep(make_client, make_settings, token, bank) -> Prepared:  # type: ignore[no-untyped-def]
    settings = make_settings(bank_base_url=bank.url)
    return prepared_case(make_client(bank_base_url=bank.url), token, settings)


def admin(prep: Prepared) -> Any:
    # Service contract tests use a trusted context; the HTTP test below checks the resolver boundary.
    return replace(
        prep.principal(),
        roles=frozenset({Role.TENANT_ADMIN}),
        assigned_roles=frozenset({Role.TENANT_ADMIN}),
        mfa_satisfied=True,
    )


def body(prep: Prepared, capability: str = "AG-AML") -> incidents.IncidentRequest:
    scope, _ = prep.scope()
    result = run_simple("AG-KYC", prep.facts(), scope)
    assert result.output_id is not None
    return incidents.IncidentRequest(
        capability_id=capability,
        owner_role="AML_SUPERVISOR",
        reason="Synthetic confirmed critical control incident for regression.",
        evidence_refs=[f"agent_output:{result.output_id}"],
    )


def opened(prep: Prepared, capability: str = "AG-AML") -> dict[str, Any]:
    request = body(prep, capability)
    with transaction(prep.factory) as session:
        return incidents.declare(session, admin(prep), request)


def test_stop_model_then_investigate_and_restore_with_history(prep: Prepared) -> None:
    item = opened(prep)
    scope, provider = prep.scope(MockModelProvider())
    result = run_simple("AG-AML", prep.facts(), scope)
    assert result.status == "FAILED" and result.validation["error"] == "CRITICAL_INCIDENT_ACTIVE"
    assert provider.calls == 0
    with transaction(prep.factory) as session:
        assert session.scalar(
            select(ModelRequest.id).where(ModelRequest.error_code == "CRITICAL_INCIDENT_ACTIVE")
        )
        assert session.scalar(select(func.count()).select_from(CapabilityIncidentEvent)) == 1
    with pytest.raises(AppError), transaction(prep.factory) as session:
        incidents.restore(
            session,
            admin(prep),
            uuid.UUID(item["id"]),
            incidents.RestoreRequest(expected_version=1, reason="Attempt before investigation must fail."),
        )
    notifier = LocalNotificationAdapter()
    with transaction(prep.factory) as session:
        assert incidents.deliver_notifications(session, notifier)["sent"] == 1
    with transaction(prep.factory) as session:
        assert incidents.deliver_notifications(session, notifier)["sent"] == 0
    assert len(notifier.delivered) == 1
    with transaction(prep.factory) as session:
        investigated = incidents.investigate(
            session,
            admin(prep),
            uuid.UUID(item["id"]),
            incidents.InvestigationRequest(
                expected_version=1,
                findings="Synthetic root cause and impact assessed.",
                remediation_ref="change-test-123",
                verification_ref="regression-test-456",
            ),
        )
        assert investigated["status"] == "INVESTIGATED"
    with pytest.raises(AppError), transaction(prep.factory) as session:
        incidents.restore(
            session,
            admin(prep),
            uuid.UUID(item["id"]),
            incidents.RestoreRequest(expected_version=1, reason="Stale restoration attempt must fail."),
        )
    with transaction(prep.factory) as session:
        restored = incidents.restore(
            session,
            admin(prep),
            uuid.UUID(item["id"]),
            incidents.RestoreRequest(
                expected_version=2,
                reason="Remediation verified; capability may resume.",
            ),
        )
        assert restored["status"] == "RESTORED" and restored["record_version"] == 3
        assert session.scalar(select(func.count()).select_from(CapabilityIncidentEvent)) == 4
    scope, provider = prep.scope()
    assert run_simple("AG-AML", prep.facts(), scope).status in {"SUCCEEDED", "PARTIAL"}
    assert provider.calls == 1


@pytest.mark.parametrize("capability", ["AG-AML", "SVC-EVIDENCE"])
def test_read_tool_is_stopped_before_connector_work(
    prep: Prepared, capability: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened(prep, capability)
    gateway = ToolGateway(prep.factory, ConnectorFactory(prep.settings, prep.factory), prep.settings)

    def unexpected(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("A blocked tool reached its connector.")

    monkeypatch.setattr(gateway, "_run", unexpected)
    ctx = ExecutionContext(
        admin(prep).tenant_id,
        uuid.UUID(prep.case_id),
        None,
        "incident-test",
        capability,
        prep.principal(),
        GATHER_TOOLS,
        frozenset({"CONFIDENTIAL", "RESTRICTED"}),
    )
    with pytest.raises(ToolDeniedError, match="CRITICAL_INCIDENT_ACTIVE"):
        gateway.invoke(ctx, "get_transactions", {"customer_ref": "customer-test-001"})


def test_report_dependency_blocks_finalization_before_side_effect(
    prep: Prepared,
) -> None:
    opened(prep, "AG-REPORT")
    gateway = ToolGateway(prep.factory, ConnectorFactory(prep.settings, prep.factory), prep.settings)
    ctx = ExecutionContext(
        admin(prep).tenant_id,
        uuid.UUID(prep.case_id),
        None,
        "incident-test",
        "SVC-FINALIZATION",
        prep.principal(),
        frozenset(),
        frozenset(),
    )
    with pytest.raises(ToolDeniedError, match="CRITICAL_INCIDENT_ACTIVE"):
        gateway.invoke_case_status_write(ctx, {}, "incident-test-write")


def test_incident_is_tenant_and_capability_scoped(prep: Prepared) -> None:
    opened(prep)
    with transaction(prep.factory) as session:
        assert incidents.is_blocked(session, admin(prep).tenant_id, "AG-AML")
        assert not incidents.is_blocked(session, admin(prep).tenant_id, "AG-KYC")
        assert not incidents.is_blocked(session, uuid.uuid4(), "AG-AML")
    scope, provider = prep.scope()
    assert run_simple("AG-KYC", prep.facts(), scope).status in {"SUCCEEDED", "PARTIAL"}
    assert provider.calls == 1


def test_missing_evidence_and_cross_tenant_incidents_are_concealed(
    prep: Prepared,
) -> None:
    bad = incidents.IncidentRequest(
        capability_id="AG-AML",
        owner_role="TENANT_ADMIN",
        reason="Synthetic incident with missing evidence reference.",
        evidence_refs=[f"agent_output:{uuid.uuid4()}"],
    )
    with pytest.raises(AppError), transaction(prep.factory) as session:
        incidents.declare(session, admin(prep), bad)
    item = opened(prep)
    other = replace(admin(prep), tenant_id=uuid.uuid4())
    with pytest.raises(AppError), transaction(prep.factory) as session:
        incidents.investigate(
            session,
            other,
            uuid.UUID(item["id"]),
            incidents.InvestigationRequest(
                expected_version=1,
                findings="Synthetic investigation cannot cross tenants.",
                remediation_ref="change-123",
                verification_ref="test-456",
            ),
        )


def test_audit_failure_rolls_back_incident_stop(prep: Prepared, monkeypatch: pytest.MonkeyPatch) -> None:
    request = body(prep)

    def unavailable(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("synthetic audit failure")

    with monkeypatch.context() as patch:
        patch.setattr(incidents.audit, "record", unavailable)
        with pytest.raises(RuntimeError), transaction(prep.factory) as session:
            incidents.declare(session, admin(prep), request)
    with transaction(prep.factory) as session:
        assert not incidents.is_blocked(session, admin(prep).tenant_id, "AG-AML")
        assert session.scalar(select(func.count()).select_from(CapabilityIncident)) == 0


def test_event_evidence_is_immutable_even_for_migrator(prep: Prepared, db_url: str) -> None:
    item = opened(prep)
    for factory in (prep.factory, session_factory(db_url)):
        with pytest.raises(DBAPIError), transaction(factory) as session:
            session.execute(
                text("UPDATE capability_incident_events SET details = '{}'::jsonb WHERE incident_id = :id"),
                {"id": uuid.UUID(item["id"])},
            )


def test_notification_failures_are_bounded_and_visible(prep: Prepared) -> None:
    opened(prep)

    class FailingNotifier(LocalNotificationAdapter):
        def send(self, notification: Any) -> None:
            raise NotificationError("SYNTHETIC_UNAVAILABLE", transient=True)

    for _ in range(5):
        with transaction(prep.factory) as session:
            incidents.deliver_notifications(session, FailingNotifier())
    with transaction(prep.factory) as session:
        row = session.scalar(select(CapabilityIncident))
        assert row is not None and row.notification_status == "GAVE_UP" and row.notification_attempts == 5
        assert incidents.deliver_notifications(session, FailingNotifier()) == {
            "sent": 0,
            "failed": 0,
            "gave_up": 0,
        }
        incidents.requeue_notification(
            session,
            admin(prep),
            row.id,
            incidents.RestoreRequest(
                expected_version=1,
                reason="Notification routing repaired and retry authorized.",
            ),
        )
        assert row.notification_attempts == 5
    with transaction(prep.factory) as session:
        assert incidents.deliver_notifications(session, LocalNotificationAdapter())["sent"] == 1
        row = session.scalar(select(CapabilityIncident))
        assert row is not None and row.notification_attempts == 6


def test_declaration_waits_for_admitted_work_then_blocks_new_admissions(
    prep: Prepared,
) -> None:
    request = body(prep)
    ctx = admin(prep)
    started, finished = threading.Event(), threading.Event()
    errors: list[BaseException] = []

    def declare() -> None:
        started.set()
        try:
            with transaction(prep.factory) as session:
                incidents.declare(session, ctx, request)
        except BaseException as exc:
            errors.append(exc)
        finally:
            finished.set()

    with incidents.admission(prep.factory, ctx.tenant_id, "AG-AML"):
        thread = threading.Thread(target=declare, daemon=True)
        thread.start()
        assert started.wait(5)
        assert not finished.wait(0.2)
    thread.join(timeout=10)
    assert finished.is_set() and not errors
    with (
        pytest.raises(CapabilityUnavailableError),
        incidents.admission(prep.factory, ctx.tenant_id, "AG-AML"),
    ):
        pytest.fail("New work was admitted after declaration committed.")


def test_http_endpoint_does_not_accept_analyst_authority(prep: Prepared, token) -> None:  # type: ignore[no-untyped-def]
    response = prep.client.get("/api/v1/admin/capability-incidents", headers=token(prep.analyst))
    assert response.status_code == 403, response.text


def test_evaluation_cannot_bypass_an_active_stop(prep: Prepared, token) -> None:  # type: ignore[no-untyped-def]
    from tests.agent_helpers import PADMIN

    opened(prep)
    response = prep.client.post(
        "/api/v1/evaluations",
        json={"dataset": "aml-synthetic-v1"},
        headers=token(PADMIN),
    )
    assert response.status_code == 422, response.text
    assert "CRITICAL_INCIDENT_ACTIVE" in response.json()["error"]["message"]


@pytest.mark.parametrize("write_enabled", [False, True])
def test_incident_blocks_local_completion_even_without_downstream_write(
    prep: Prepared, write_enabled: bool
) -> None:
    from finagent.services.review_service.finalization import (
        FinalizationFailedError,
        finalize,
    )

    opened(prep, "AG-REPORT")
    gateway = ToolGateway(prep.factory, ConnectorFactory(prep.settings, prep.factory), prep.settings)
    with pytest.raises(FinalizationFailedError, match="CRITICAL_INCIDENT_ACTIVE"):
        finalize(
            prep.factory,
            tenant_id=admin(prep).tenant_id,
            case_id=uuid.UUID(prep.case_id),
            approval_request_id=uuid.uuid4(),
            run_id=None,
            gateway=gateway,
            settings=prep.settings.model_copy(update={"case_status_write_enabled": write_enabled}),
            lease=None,
        )
