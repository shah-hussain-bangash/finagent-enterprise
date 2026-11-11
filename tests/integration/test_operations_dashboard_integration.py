"""Scoped dashboard queries, immutable observations and real connector instrumentation."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from finagent.database.models import (
    AgentOutput,
    Case,
    CaseStatusHistory,
    ConnectorObservation,
    ModelRequest,
    WorkflowRun,
)
from finagent.database.seeds import sid
from finagent.database.session import session_factory, transaction
from finagent.integrations.factory import ConnectorFactory, reset_breakers
from finagent.services.reporting_service.dashboard import dashboard
from tests.agent_helpers import Prepared, prepared_case
from tests.helpers import principal, subject

pytestmark = pytest.mark.integration


@pytest.fixture
def prep(make_client, make_settings, token, bank) -> Prepared:  # type: ignore[no-untyped-def]
    settings = make_settings(bank_base_url=bank.url)
    return prepared_case(make_client(bank_base_url=bank.url), token, settings)


def observation(
    tenant: uuid.UUID, *, attempts: int = 1, outcome: str = "SUCCEEDED", created_at: datetime | None = None
) -> ConnectorObservation:
    return ConnectorObservation(
        tenant_id=tenant,
        source="core-banking",
        operation="JSON",
        outcome=outcome,
        attempts=attempts,
        latency_ms=12,
        error_code="TIMEOUT" if outcome == "FAILED" else None,
        created_at=created_at or datetime.now(UTC),
    )


def test_empty_operator_dashboard_has_no_invented_measurements(client, token) -> None:  # type: ignore[no-untyped-def]
    response = client.get("/api/v1/reporting/dashboard", headers=token(subject("a", "operator1")))
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["scope"] == "tenant-metadata"
    assert data["connector_health"][0]["state"] == "NO_OBSERVATIONS"
    assert data["connector_health"][0]["failure_rate"]["value"] is None
    assert data["review_turnaround"]["mean_seconds"] is None
    assert data["output_gap_rate"]["value"] is None
    assert data["estimated_model_cost_by_currency"] == {}
    assert "tool_fees" in data["unavailable"]


def test_dashboard_rejects_unprivileged_roles_and_invalid_windows(client, token) -> None:  # type: ignore[no-untyped-def]
    for who in ("tenantadmin1", "policyadmin1"):
        assert client.get("/api/v1/reporting/dashboard", headers=token(subject("a", who))).status_code == 403
    for days in (0, 29):
        response = client.get(
            f"/api/v1/reporting/dashboard?days={days}",
            headers=token(subject("a", "operator1")),
        )
        assert response.status_code == 400, response.text
        assert response.json()["error"]["code"] == "SCHEMA_VALIDATION_FAILED"


def test_connector_observations_are_tenant_scoped_and_future_samples_excluded(
    client, token, app_db_url
) -> None:  # type: ignore[no-untyped-def]
    factory = session_factory(app_db_url)
    now = datetime.now(UTC)
    with transaction(factory) as session:
        session.add_all(
            [
                observation(sid("tenant:a"), attempts=3, outcome="FAILED"),
                observation(sid("tenant:b"), attempts=99),
                observation(sid("tenant:a"), attempts=100, created_at=now + timedelta(days=1)),
            ]
        )
    data = client.get("/api/v1/reporting/dashboard", headers=token(subject("a", "operator1"))).json()
    connector = data["connector_health"][0]
    assert connector["logical_calls"] == 1 and connector["retry_attempts"] == 2
    assert connector["state"] == "LAST_CALL_FAILED"
    other = client.get("/api/v1/reporting/dashboard", headers=token(subject("b", "operator1"))).json()
    assert other["connector_health"][0]["logical_calls"] == 1


def test_assigned_case_scope_and_case_links_are_preserved(prep: Prepared, token) -> None:  # type: ignore[no-untyped-def]
    ctx = prep.principal()
    with transaction(prep.factory) as session:
        session.add(
            WorkflowRun(
                tenant_id=ctx.tenant_id,
                case_id=uuid.UUID(prep.case_id),
                workflow_type="AML",
                graph_version="synthetic-test",
                status="SUCCEEDED",
                status_reason="secret-not-returned",
                requested_by=ctx.principal_id,
                correlation_id="synthetic-dashboard",
            )
        )
    assigned = prep.client.get("/api/v1/reporting/dashboard", headers=token(prep.analyst)).json()
    assert assigned["connector_health"] is None and assigned["scope"] == "assigned-cases"
    assert assigned["recent_runs"][0]["case_href"] == f"/cases/{prep.case_id}"
    other = "a.analyst2@synthetic" if prep.analyst == "a.analyst1@synthetic" else "a.analyst1@synthetic"
    hidden = prep.client.get("/api/v1/reporting/dashboard", headers=token(other)).json()
    assert hidden["cases_by_status"] == {} and hidden["recent_runs"] == []
    operator = prep.client.get("/api/v1/reporting/dashboard", headers=token(subject("a", "operator1"))).json()
    assert operator["recent_runs"][0]["case_href"] is None
    assert "secret-not-returned" not in json.dumps(operator)


def test_first_completion_elapsed_time_does_not_double_count_reopening(prep: Prepared) -> None:
    now = datetime.now(UTC)
    with transaction(prep.factory) as session:
        case = session.get(Case, uuid.UUID(prep.case_id))
        assert case is not None
        case.created_at = now - timedelta(hours=2)
        for elapsed in (60, 30):
            session.add(
                CaseStatusHistory(
                    tenant_id=case.tenant_id,
                    case_id=case.id,
                    from_status="FINALIZING",
                    to_status="COMPLETED",
                    actor="SYNTHETIC",
                    reason="Synthetic timing fixture",
                    correlation_id="synthetic-timing",
                    occurred_at=now - timedelta(minutes=elapsed),
                )
            )
    with transaction(prep.factory) as session:
        data = dashboard(session, prep.principal(), now=now + timedelta(seconds=1))
        assert data["time_to_first_completion"]["sample_count"] == 1
        assert data["time_to_first_completion"]["mean_seconds"] == 3600


def test_quality_proxies_do_not_expose_claims_or_treat_absent_arrays_as_rejections(prep: Prepared) -> None:
    ctx = prep.principal()
    with transaction(prep.factory) as session:
        for validation, output, status in [
            (
                {"rejected": [{"claim_id": "private-claim", "reasons": ["private-reason"]}]},
                {"findings": [{"claim_type": "GAP", "claim": "private-customer-content"}]},
                "PARTIAL",
            ),
            ({"error": "INVALID_OUTPUT"}, {"findings": []}, "ESCALATED"),
        ]:
            session.add(
                AgentOutput(
                    tenant_id=ctx.tenant_id,
                    case_id=uuid.UUID(prep.case_id),
                    snapshot_id=prep.snapshot_id,
                    capability_id="AG-AML",
                    capability_version="synthetic",
                    status=status,
                    output=output,
                    validation=validation,
                    lineage={},
                )
            )
    with transaction(prep.factory) as session:
        data = dashboard(session, ctx)
        assert data["output_gap_rate"] == {"numerator": 1, "denominator": 2, "value": 0.5}
        assert data["output_rejection_rate"]["value"] == 0.5
        assert data["output_escalation_rate"]["value"] == 0.5
        assert all(
            secret not in json.dumps(data)
            for secret in ["private-claim", "private-reason", "private-customer-content"]
        )


def test_model_cost_is_separated_by_currency_and_config(prep: Prepared, token) -> None:  # type: ignore[no-untyped-def]
    with transaction(prep.factory) as session:
        for tenant, case_id, config, currency, cost in [
            (sid("tenant:a"), uuid.UUID(prep.case_id), "cfg-one", "USD", "1.25"),
            (sid("tenant:a"), None, "cfg-two", "EUR", "2.50"),
            (sid("tenant:b"), None, "private-foreign-config", "USD", "999"),
        ]:
            session.add(
                ModelRequest(
                    tenant_id=tenant,
                    case_id=case_id,
                    capability_id="AG-AML",
                    capability_version="synthetic",
                    provider="synthetic",
                    model="synthetic",
                    prompt_version="synthetic",
                    prompt_hash="0" * 64,
                    config_version=config,
                    purpose="INITIAL",
                    attempt=1,
                    status="SUCCEEDED",
                    input_tokens=10,
                    output_tokens=5,
                    estimated_cost=Decimal(cost),
                    cost_currency=currency,
                    latency_ms=25,
                    correlation_id="synthetic-cost",
                )
            )
    response = prep.client.get("/api/v1/reporting/dashboard", headers=token(subject("a", "operator1")))
    assert response.status_code == 200, response.text
    data = response.json()
    assert Decimal(data["estimated_model_cost_by_currency"]["USD"]) == Decimal("1.25")
    assert Decimal(data["estimated_model_cost_by_currency"]["EUR"]) == Decimal("2.50")
    assert {row["config_version"] for row in data["model_slices"]} == {"cfg-one", "cfg-two"}
    assert "private-foreign-config" not in response.text


def test_factory_records_calls_for_each_tenant_without_urls_or_credentials(make_settings, app_db_url) -> None:  # type: ignore[no-untyped-def]
    reset_breakers()
    factory = session_factory(app_db_url)
    settings = make_settings(connector_max_attempts=1)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "schema_version": "1.0",
                "as_of": datetime.now(UTC).isoformat(),
                "private": "sensitive-customer",
            },
        )

    with httpx.Client(base_url="https://synthetic.invalid", transport=httpx.MockTransport(handler)) as http:
        connector = ConnectorFactory(settings, factory, http=http)
        for name in ("a", "b"):
            connector.client(sid(f"tenant:{name}")).request_json("GET", "/private-customer")
    with transaction(factory) as session:
        rows = session.scalars(select(ConnectorObservation)).all()
        assert {row.tenant_id for row in rows} == {sid("tenant:a"), sid("tenant:b")}
        assert len(rows) == 2 and all(row.outcome == "SUCCEEDED" and row.attempts == 1 for row in rows)
        columns = set(ConnectorObservation.__table__.columns.keys())
        assert not {"url", "path", "headers", "payload", "customer_ref", "api_key"} & columns
    reset_breakers()


def test_observations_cannot_be_modified_by_app_or_migrator(client, app_db_url, db_url) -> None:  # type: ignore[no-untyped-def]
    with transaction(session_factory(app_db_url)) as session:
        sample = observation(sid("tenant:a"))
        session.add(sample)
        session.flush()
        identifier = sample.id
    for url in (app_db_url, db_url):
        with pytest.raises(DBAPIError), transaction(session_factory(url)) as session:
            session.execute(
                text("UPDATE connector_observations SET attempts = 99 WHERE id = :id"), {"id": identifier}
            )


def test_review_turnaround_is_calendar_time_for_decided_requests(prep: Prepared) -> None:
    from finagent.database.models import (
        ApprovalDecision,
        ApprovalRequest,
        Report,
        ReportVersion,
    )

    ctx = prep.principal()
    now = datetime.now(UTC)
    with transaction(prep.factory) as session:
        case = session.get(Case, uuid.UUID(prep.case_id))
        assert case is not None
        case.created_at = now - timedelta(minutes=10)
        report = Report(
            tenant_id=ctx.tenant_id, case_id=uuid.UUID(prep.case_id), status="DRAFT", current_version_no=1
        )
        session.add(report)
        session.flush()
        version = ReportVersion(
            tenant_id=ctx.tenant_id,
            report_id=report.id,
            version_no=1,
            snapshot_id=prep.snapshot_id,
            content={},
            content_hash="0" * 64,
            origin="AI_DRAFT",
            author_label="Synthetic metric fixture",
            lineage={},
            validation={},
        )
        session.add(version)
        session.flush()
        request = ApprovalRequest(
            tenant_id=ctx.tenant_id,
            case_id=uuid.UUID(prep.case_id),
            report_id=report.id,
            report_version_id=version.id,
            snapshot_id=prep.snapshot_id,
            requested_by=ctx.principal_id,
            matrix_version="synthetic",
            status="RETURNED",
            created_at=now - timedelta(minutes=5),
        )
        session.add(request)
        session.flush()
        session.add(
            ApprovalDecision(
                tenant_id=ctx.tenant_id,
                request_id=request.id,
                decision="RETURN_FOR_REWORK",
                reviewer_id=principal("a", "reviewer1"),
                reviewer_role="REVIEWER",
                rationale="Synthetic timing fixture only",
                report_version_id=version.id,
                snapshot_id=prep.snapshot_id,
                config_version="synthetic",
                mfa_satisfied=True,
                effect="REWORK",
                idempotency_key="synthetic-review-timing",
                decided_at=now - timedelta(minutes=3),
            )
        )
    with transaction(prep.factory) as session:
        value = dashboard(session, ctx, now=now)["review_turnaround"]
        assert value["sample_count"] == 1 and value["mean_seconds"] == 120
