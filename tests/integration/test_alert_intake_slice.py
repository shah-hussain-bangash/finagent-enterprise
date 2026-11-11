"""Vertical slice 1 (prompt §52): alert -> case -> assignment -> audit -> outbox, against PostgreSQL."""

from __future__ import annotations

import copy
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from finagent.database.models import (
    Alert,
    AuditEvent,
    Case,
    CaseAssignment,
    IntakeReconciliation,
    OutboxEvent,
)
from finagent.database.seeds import sid
from finagent.database.session import session_factory, transaction
from finagent.services.audit_service.writer import verify_chain

pytestmark = pytest.mark.integration
CONNECTOR_A = "a.tm-connector@synthetic"


def envelope(alert_id: str = "AML-SYN-001", source: str = "tm-synthetic-a", **payload: Any) -> dict[str, Any]:
    body = {
        "schema_version": "1.0",
        "event_id": f"evt-{uuid.uuid4()}",
        "source_system": source,
        "source_event_time": "2026-10-15T07:00:00Z",
        "payload": {
            "source_alert_id": alert_id,
            "alert_type": "TM_STRUCTURING",
            "occurred_at": "2026-10-15T06:59:00Z",
            "subject_refs": ["customer-test-001"],
            "severity": "HIGH",
            "window_start": "2026-09-15T00:00:00Z",
            "window_end": "2026-10-15T00:00:00Z",
        },
    }
    body["payload"].update(payload)
    return body


def post(client, token, body, key=None, subject=CONNECTOR_A):  # type: ignore[no-untyped-def]
    headers = {**token(subject), "Idempotency-Key": key or f"key-{uuid.uuid4()}"}
    return client.post("/api/v1/alerts", json=body, headers=headers)


def count(db_url: str, model: type, **where: Any) -> int:
    with transaction(session_factory(db_url)) as s:
        stmt = select(func.count()).select_from(model)
        for k, v in where.items():
            stmt = stmt.where(getattr(model, k) == v)
        return int(s.scalar(stmt) or 0)


@pytest.mark.req("BR-001", "FR-007", "FR-009", "DIR-010", "MST:ARCH-003", "MST:DB-003", "FR-036")
def test_alert_creates_assigned_case_with_audit_and_outbox_atomically(client, token, db_url) -> None:  # type: ignore[no-untyped-def]
    r = post(client, token, envelope())
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "ASSIGNED" and body["replayed"] is False
    case_id = uuid.UUID(body["case_id"])
    tenant_a = sid("tenant:a")
    assert count(db_url, Case, tenant_id=tenant_a) == 1
    assert count(db_url, CaseAssignment, case_id=case_id, active=True) == 1
    assert count(db_url, OutboxEvent, resource_id=str(case_id), event_type="case.created.v1") == 1
    with transaction(session_factory(db_url)) as s:
        actions = s.scalars(
            select(AuditEvent.action).where(AuditEvent.case_id == case_id).order_by(AuditEvent.seq)
        ).all()
        assert actions == ["ALERT_RECEIVED", "CASE_CREATED", "CASE_ASSIGNED"]
        assert verify_chain(s, tenant_a)


@pytest.mark.release_blocker
@pytest.mark.req("FR-008", "FR-032", "DIR-022", "DIR-026", "DIR-027", "NQ-020", "MST:API-003")
def test_replay_same_key_and_same_alert_new_key_yield_one_case(client, token, db_url) -> None:  # type: ignore[no-untyped-def]
    body = envelope()
    first = post(client, token, body, key="replay-key-0001")
    again = post(client, token, body, key="replay-key-0001")
    new_key_same_alert = post(client, token, {**body, "event_id": "evt-redelivery"})
    assert first.status_code == 201
    assert again.status_code == 201 and again.json() == first.json()  # stored original result
    assert new_key_same_alert.status_code == 200 and new_key_same_alert.json()["replayed"] is True
    assert new_key_same_alert.json()["case_id"] == first.json()["case_id"]
    assert count(db_url, Case) == 1 and count(db_url, OutboxEvent) == 1


@pytest.mark.req("FR-032", "DIR-027")
def test_same_idempotency_key_different_payload_conflicts(client, token, db_url) -> None:  # type: ignore[no-untyped-def]
    assert post(client, token, envelope("AML-1"), key="shared-key-0001").status_code == 201
    r = post(client, token, envelope("AML-2"), key="shared-key-0001")
    assert r.status_code == 409 and r.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    assert count(db_url, Case) == 1


@pytest.mark.req("FR-008", "DIR-025", "DIR-027", "FR-007")
def test_changed_payload_for_existing_alert_is_quarantined_not_duplicated(client, token, db_url) -> None:  # type: ignore[no-untyped-def]
    assert post(client, token, envelope()).status_code == 201
    r = post(client, token, envelope(severity="LOW"))
    assert r.status_code == 409 and r.json()["error"]["code"] == "ALERT_PAYLOAD_CONFLICT"
    assert count(db_url, Case) == 1 and count(db_url, Alert) == 1
    assert count(db_url, IntakeReconciliation, reason_code="CHANGED_PAYLOAD_REPLAY") == 1


@pytest.mark.req("FR-008", "DIR-026", "NQ-020", "FR-034")
def test_concurrent_duplicate_deliveries_create_one_case(client, token, db_url) -> None:  # type: ignore[no-untyped-def]
    body = envelope("AML-CONCURRENT")
    with ThreadPoolExecutor(max_workers=8) as pool:
        codes = sorted(
            pool.map(lambda i: post(client, token, {**body, "event_id": f"evt-{i}"}).status_code, range(8))
        )
    assert codes.count(201) == 1 and codes.count(200) == 7
    assert count(db_url, Case) == 1 and count(db_url, OutboxEvent) == 1


@pytest.mark.req("FR-007", "DIR-002", "DIR-010", "SPR-010")
def test_payload_cannot_carry_tenant_and_invalid_input_creates_nothing(client, token, db_url) -> None:  # type: ignore[no-untyped-def]
    forged = envelope()
    forged["tenant_id"] = str(sid("tenant:b"))
    bad_window = envelope(window_start="2026-10-16T00:00:00Z")
    naive_time = envelope(occurred_at="2026-10-15T06:59:00")
    wrong_type = envelope(alert_type="TM_OTHER")
    for b, status in ((forged, 400), (bad_window, 400), (naive_time, 400), (wrong_type, 422)):
        r = post(client, token, b)
        assert r.status_code == status, (b, r.text)
        assert "2026-10-16" not in r.text  # validation errors never echo input values
    assert count(db_url, Case) == 0 and count(db_url, Alert) == 0


@pytest.mark.release_blocker
@pytest.mark.req("DIR-002", "SPR-010", "FR-024")
def test_connector_cannot_ingest_for_another_source_or_tenant(client, token, db_url) -> None:  # type: ignore[no-untyped-def]
    r = post(client, token, envelope(source="tm-synthetic-b"))  # tenant A connector, tenant B source
    assert r.status_code == 403
    r = post(client, token, envelope(), subject="a.analyst1@synthetic")  # human cannot ingest
    assert r.status_code == 403
    assert count(db_url, Case) == 0


@pytest.mark.req("FR-007", "SPR-017")
def test_missing_idempotency_key_and_oversized_body(client, token) -> None:  # type: ignore[no-untyped-def]
    r = client.post("/api/v1/alerts", json=envelope(), headers=token(CONNECTOR_A))
    assert r.status_code == 400 and r.json()["error"]["code"] == "IDEMPOTENCY_KEY_REQUIRED"
    big = copy.deepcopy(envelope())
    big["payload"]["rule_ref"] = "x" * (1024 * 1024 + 10)
    r = post(client, token, big)
    assert r.status_code == 413 and r.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"


@pytest.mark.release_blocker
@pytest.mark.req("FR-036", "SPR-035", "SPR-036", "DIR-019")
def test_audit_events_are_append_only_in_database(client, token, db_url) -> None:  # type: ignore[no-untyped-def]
    assert post(client, token, envelope()).status_code == 201
    for stmt in ("UPDATE audit_events SET outcome = 'TAMPERED'", "DELETE FROM audit_events"):
        with pytest.raises(DBAPIError, match="append-only"), transaction(session_factory(db_url)) as s:
            s.execute(text(stmt))


@pytest.mark.release_blocker
@pytest.mark.req("MST:DB-001", "SPR-010", "NQ-022", "BR-002")
def test_schema_rejects_cross_tenant_relationships(client, token, db_url) -> None:  # type: ignore[no-untyped-def]
    case_id = uuid.UUID(post(client, token, envelope()).json()["case_id"])
    tenant_b_analyst = sid("principal:b:analyst1")
    with pytest.raises(IntegrityError), transaction(session_factory(db_url)) as s:
        s.add(
            CaseAssignment(
                tenant_id=sid("tenant:a"),
                case_id=case_id,
                principal_id=tenant_b_analyst,
                assignment_role="ANALYST",
                reason="attack",
                assigned_by="test",
            )
        )


@pytest.mark.req("NFR-008", "FR-037")
def test_health_endpoints_and_correlation_id(client) -> None:  # type: ignore[no-untyped-def]
    assert client.get("/health/live").status_code == 200
    r = client.get("/health/ready", headers={"Correlation-Id": "corr-test-0001"})
    assert r.status_code == 200 and r.headers["Correlation-Id"] == "corr-test-0001"
    r = client.get("/health/live", headers={"Correlation-Id": "bad id with spaces"})
    assert r.headers["Correlation-Id"] != "bad id with spaces"
