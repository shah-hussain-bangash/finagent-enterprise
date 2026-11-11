"""Authorized, tenant-scoped audit reads (FR-036, SRS §12 GET /audit-events). Metadata only."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from finagent.core.errors import AppError, ErrorCode
from finagent.database.models import AuditEvent
from finagent.security.authorization.policy import Permission, PrincipalContext, require


def list_events(
    session: Session,
    ctx: PrincipalContext,
    *,
    limit: int,
    after_seq: int = 0,
    case_id: str | None = None,
) -> dict[str, Any]:
    require(ctx, Permission.AUDIT_READ)
    stmt = select(AuditEvent).where(AuditEvent.tenant_id == ctx.tenant_id, AuditEvent.seq > after_seq)
    if case_id:
        try:
            stmt = stmt.where(AuditEvent.case_id == uuid.UUID(case_id))
        except ValueError as exc:
            raise AppError(
                ErrorCode.SCHEMA_VALIDATION_FAILED, "Invalid case_id.", {"field": "case_id"}
            ) from exc
    rows = session.scalars(stmt.order_by(AuditEvent.seq).limit(limit)).all()
    return {
        "items": [
            {
                "seq": e.seq,
                "occurred_at": e.occurred_at.isoformat(),
                "action": e.action,
                "object_type": e.object_type,
                "object_id": e.object_id,
                "outcome": e.outcome,
                "actor_kind": e.actor_kind,
                "actor_principal_id": str(e.actor_principal_id) if e.actor_principal_id else None,
                "case_id": str(e.case_id) if e.case_id else None,
                "correlation_id": e.correlation_id,
                "hash": e.hash,
            }
            for e in rows
        ],
        "next_after_seq": rows[-1].seq if len(rows) == limit else None,
    }
