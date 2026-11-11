"""Append-only, hash-chained audit events (FR-036, SPR-035/036, DIR-019).

Written in the SAME transaction as the business change: if the audit write fails, the action fails.
A per-tenant transaction-scoped advisory lock serialises the chain. Details must be metadata and
safe references only - redaction is applied as defence in depth.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from finagent.core.correlation import get_correlation_id
from finagent.database.models import AuditEvent
from finagent.domain.fingerprint import canonical_json
from finagent.security.redaction import redact

GENESIS = "0" * 64


def _chain_hash(prev_hash: str, body: dict[str, Any]) -> str:
    return hashlib.sha256((prev_hash + canonical_json(body)).encode()).hexdigest()


def record(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    action: str,
    object_type: str,
    object_id: str | None,
    outcome: str,
    actor_principal_id: uuid.UUID | None,
    actor_kind: str,
    case_id: uuid.UUID | None = None,
    details: dict[str, Any] | None = None,
) -> AuditEvent:
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": f"audit:{tenant_id}"}
    )
    last = session.execute(
        select(AuditEvent.seq, AuditEvent.hash)
        .where(AuditEvent.tenant_id == tenant_id)
        .order_by(AuditEvent.seq.desc())
        .limit(1)
    ).first()
    seq, prev_hash = (last[0] + 1, last[1]) if last else (1, GENESIS)
    occurred_at = datetime.now(UTC)
    safe_details = redact(details or {})
    body = {
        "tenant_id": str(tenant_id),
        "seq": seq,
        "occurred_at": occurred_at.isoformat(),
        "actor_principal_id": str(actor_principal_id) if actor_principal_id else None,
        "actor_kind": actor_kind,
        "action": action,
        "object_type": object_type,
        "object_id": object_id,
        "outcome": outcome,
        "case_id": str(case_id) if case_id else None,
        "correlation_id": get_correlation_id(),
        "details": safe_details,
    }
    event = AuditEvent(
        tenant_id=tenant_id,
        seq=seq,
        occurred_at=occurred_at,
        actor_principal_id=actor_principal_id,
        actor_kind=actor_kind,
        action=action,
        object_type=object_type,
        object_id=object_id,
        outcome=outcome,
        case_id=case_id,
        correlation_id=body["correlation_id"],
        details=safe_details,
        prev_hash=prev_hash,
        hash=_chain_hash(prev_hash, body),
    )
    session.add(event)
    session.flush()
    return event


def verify_chain(session: Session, tenant_id: uuid.UUID) -> bool:
    """Recompute the tenant chain; False means tampering or corruption."""
    prev = GENESIS
    expected_seq = 1
    rows = session.scalars(
        select(AuditEvent).where(AuditEvent.tenant_id == tenant_id).order_by(AuditEvent.seq)
    )
    for ev in rows:
        body = {
            "tenant_id": str(ev.tenant_id),
            "seq": ev.seq,
            "occurred_at": ev.occurred_at.astimezone(UTC).isoformat(),
            "actor_principal_id": str(ev.actor_principal_id) if ev.actor_principal_id else None,
            "actor_kind": ev.actor_kind,
            "action": ev.action,
            "object_type": ev.object_type,
            "object_id": ev.object_id,
            "outcome": ev.outcome,
            "case_id": str(ev.case_id) if ev.case_id else None,
            "correlation_id": ev.correlation_id,
            "details": ev.details,
        }
        if ev.seq != expected_seq or ev.prev_hash != prev or ev.hash != _chain_hash(prev, body):
            return False
        prev, expected_seq = ev.hash, expected_seq + 1
    return True


def count(session: Session, tenant_id: uuid.UUID, action: str) -> int:
    return int(
        session.scalar(
            select(func.count()).where(AuditEvent.tenant_id == tenant_id, AuditEvent.action == action)
        )
        or 0
    )
