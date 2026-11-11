"""Shared test helpers (synthetic data only)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select

from finagent.database.seeds import sid
from finagent.database.session import session_factory, transaction

CONNECTOR = {"a": "a.tm-connector@synthetic", "b": "b.tm-connector@synthetic"}


def alert_envelope(ref: str = "AML-SYN-001", tenant: str = "a", **payload: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "schema_version": "1.0",
        "event_id": f"evt-{uuid.uuid4()}",
        "source_system": f"tm-synthetic-{tenant}",
        "source_event_time": "2026-10-15T07:00:00Z",
        "payload": {
            "source_alert_id": ref,
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


def ingest(client: Any, token: Any, ref: str = "AML-SYN-001", tenant: str = "a") -> dict[str, Any]:
    r = client.post(
        "/api/v1/alerts",
        json=alert_envelope(ref, tenant),
        headers={**token(CONNECTOR[tenant]), "Idempotency-Key": f"key-{uuid.uuid4()}"},
    )
    assert r.status_code == 201, r.text
    body: dict[str, Any] = r.json()
    return body


def start_run(
    client: Any,
    token: Any,
    case_id: str,
    analyst: str,
    key: str | None = None,
    workflow_type: str = "durability_probe",
) -> Any:
    """P4 durability tests use the agent-free probe graph; the AML graph has its own tests."""
    return client.post(
        "/api/v1/workflows",
        json={"case_id": case_id, "workflow_type": workflow_type},
        headers={**token(analyst), "Idempotency-Key": key or f"wf-{uuid.uuid4()}"},
    )


def run_status(db_url: str, run_id: str) -> tuple[str, str | None]:
    from finagent.database.models import WorkflowRun

    with transaction(session_factory(db_url)) as s:
        run = s.get(WorkflowRun, uuid.UUID(run_id))
        assert run is not None
        return run.status, run.last_error_code


def steps(db_url: str, run_id: str) -> dict[str, tuple[str, int]]:
    from finagent.database.models import WorkflowStep

    with transaction(session_factory(db_url)) as s:
        rows = s.scalars(select(WorkflowStep).where(WorkflowStep.run_id == uuid.UUID(run_id))).all()
        return {r.node: (r.status, r.attempt) for r in rows}


def principal(tenant: str, key: str) -> uuid.UUID:
    return sid(f"principal:{tenant}:{key}")


def subject(tenant: str, key: str) -> str:
    return f"{tenant}.{key}@synthetic"


def assigned_analyst(client: Any, token: Any, case_id: str, tenant: str = "a") -> str:
    """Subject of the analyst the intake auto-assigned (DEC-13 least-loaded rule)."""
    detail = client.get(f"/api/v1/cases/{case_id}", headers=token(subject(tenant, "supervisor1"))).json()
    for key in ("analyst1", "analyst2"):
        if detail["assigned_analyst_id"] == str(principal(tenant, key)):
            return subject(tenant, key)
    raise AssertionError("case is not assigned to a seeded analyst")


def scalar_count(db_url: str, model: type, **where: Any) -> int:
    with transaction(session_factory(db_url)) as s:
        stmt = select(func.count()).select_from(model)
        for k, v in where.items():
            stmt = stmt.where(getattr(model, k) == v)
        return int(s.scalar(stmt) or 0)


def audit_actions(db_url: str, tenant: str = "a", case_id: str | None = None) -> list[str]:
    from finagent.database.models import AuditEvent

    with transaction(session_factory(db_url)) as s:
        stmt = select(AuditEvent.action).where(AuditEvent.tenant_id == sid(f"tenant:{tenant}"))
        if case_id:
            stmt = stmt.where(AuditEvent.case_id == uuid.UUID(case_id))
        return list(s.scalars(stmt.order_by(AuditEvent.seq)))
