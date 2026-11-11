"""Break-glass emergency access (SPR-008; SEC-D03 scope OPEN -> disabled by default).

Process implemented: eligible principal (BREAK_GLASS role) + MFA + written justification ->
time-limited READ-ONLY tenant case access (EMERGENCY_READ) that expires automatically. Activation
raises a security alert (outbox `security.break_glass_activated.v1` + warning log) and every case
read under the grant is audited with the grant id. A different Tenant Administrator records the
post-event review. Break-glass never grants approval, submission, update or export, and audit
history cannot be altered (append-only table).
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy import select
from sqlalchemy.orm import Session

from finagent.core.correlation import get_correlation_id
from finagent.core.errors import AppError, ErrorCode, not_found
from finagent.database.models import BreakGlassGrant, OutboxEvent
from finagent.security.authorization.policy import Permission, PrincipalContext, require
from finagent.services.audit_service import writer as audit

log = logging.getLogger("finagent.security")


class ActivationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    justification: Annotated[str, StringConstraints(strip_whitespace=True, min_length=20, max_length=1000)]
    duration_minutes: int = Field(default=30, ge=5)


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    outcome: Literal["JUSTIFIED", "NOT_JUSTIFIED"]
    note: Annotated[str, StringConstraints(strip_whitespace=True, min_length=10, max_length=1000)]


def _view(g: BreakGlassGrant, now: datetime) -> dict[str, Any]:
    active = g.revoked_at is None and g.activated_at <= now < g.expires_at
    return {
        "grant_id": str(g.id),
        "principal_id": str(g.principal_id),
        "activated_at": g.activated_at.isoformat(),
        "expires_at": g.expires_at.isoformat(),
        "revoked_at": g.revoked_at.isoformat() if g.revoked_at else None,
        "active": active,
        "review_outcome": g.review_outcome,
        "reviewed_by": str(g.reviewed_by) if g.reviewed_by else None,
    }


def record_denied(session: Session, ctx: PrincipalContext, reason: str) -> None:
    """Committed by the caller BEFORE it raises, so attempted emergency access is never lost."""
    audit.record(
        session,
        tenant_id=ctx.tenant_id,
        action="BREAK_GLASS_DENIED",
        object_type="break_glass_grant",
        object_id=None,
        outcome="DENIED",
        actor_principal_id=ctx.principal_id,
        actor_kind=ctx.kind.value,
        details={"reason": reason},
    )
    log.warning("security_alert", extra={"alert": "break_glass_denied", "reason": reason})


def activate(
    session: Session, ctx: PrincipalContext, req: ActivationRequest, *, max_minutes: int
) -> dict[str, Any]:
    """Callers must check the feature flag first (see record_denied)."""
    require(ctx, Permission.BREAK_GLASS_ACTIVATE)  # eligibility + MFA step-up
    if req.duration_minutes > max_minutes:
        raise AppError(
            ErrorCode.SEMANTIC_VALIDATION_FAILED,
            f"Emergency access may last at most {max_minutes} minutes.",
            {"field": "duration_minutes"},
        )
    now = datetime.now(UTC)
    existing = session.scalar(
        select(BreakGlassGrant.id).where(
            BreakGlassGrant.tenant_id == ctx.tenant_id,
            BreakGlassGrant.principal_id == ctx.principal_id,
            BreakGlassGrant.revoked_at.is_(None),
            BreakGlassGrant.expires_at > now,
        )
    )
    if existing is not None:
        raise AppError(ErrorCode.CASE_INVALID_STATE, "An emergency access grant is already active.")
    grant = BreakGlassGrant(
        tenant_id=ctx.tenant_id,
        principal_id=ctx.principal_id,
        justification=req.justification,
        activated_at=now,
        expires_at=now + timedelta(minutes=req.duration_minutes),
    )
    session.add(grant)
    session.flush()
    audit.record(
        session,
        tenant_id=ctx.tenant_id,
        action="BREAK_GLASS_ACTIVATED",
        object_type="break_glass_grant",
        object_id=str(grant.id),
        outcome="SUCCESS",
        actor_principal_id=ctx.principal_id,
        actor_kind=ctx.kind.value,
        details={"expires_at": grant.expires_at.isoformat(), "justification": req.justification},
    )
    session.add(
        OutboxEvent(
            tenant_id=ctx.tenant_id,
            event_type="security.break_glass_activated.v1",
            schema_version="1",
            resource_id=str(grant.id),
            correlation_id=get_correlation_id(),
            payload={"grant_id": str(grant.id), "expires_at": grant.expires_at.isoformat()},
        )
    )
    # Monitoring alert source (prompt §36): structured security event, no personal data.
    log.warning("security_alert", extra={"alert": "break_glass_activated", "grant_id": str(grant.id)})
    return _view(grant, now)


def _load(session: Session, ctx: PrincipalContext, raw_id: str) -> BreakGlassGrant:
    try:
        gid = uuid.UUID(raw_id)
    except ValueError:
        raise not_found() from None
    grant = session.scalar(
        select(BreakGlassGrant)
        .where(BreakGlassGrant.tenant_id == ctx.tenant_id, BreakGlassGrant.id == gid)
        .with_for_update()
    )
    if grant is None:
        raise not_found()
    return grant


def revoke(session: Session, ctx: PrincipalContext, raw_id: str) -> dict[str, Any]:
    """The holder or a Tenant Administrator may end a grant early."""
    grant = _load(session, ctx, raw_id)
    if grant.principal_id != ctx.principal_id:
        require(ctx, Permission.TENANT_ADMIN)
    now = datetime.now(UTC)
    if grant.revoked_at is None and grant.expires_at > now:
        grant.revoked_at = now
        audit.record(
            session,
            tenant_id=ctx.tenant_id,
            action="BREAK_GLASS_REVOKED",
            object_type="break_glass_grant",
            object_id=str(grant.id),
            outcome="SUCCESS",
            actor_principal_id=ctx.principal_id,
            actor_kind=ctx.kind.value,
        )
    session.flush()
    return _view(grant, now)


def review(session: Session, ctx: PrincipalContext, raw_id: str, req: ReviewRequest) -> dict[str, Any]:
    """Post-event review by a DIFFERENT Tenant Administrator; recorded once."""
    require(ctx, Permission.TENANT_ADMIN)
    grant = _load(session, ctx, raw_id)
    if grant.principal_id == ctx.principal_id:
        raise AppError(ErrorCode.FORBIDDEN, "Emergency access must be reviewed by someone else.")
    if grant.reviewed_at is not None:
        raise AppError(ErrorCode.CASE_INVALID_STATE, "This grant has already been reviewed.")
    now = datetime.now(UTC)
    grant.reviewed_at, grant.reviewed_by = now, ctx.principal_id
    grant.review_outcome, grant.review_note = req.outcome, req.note
    audit.record(
        session,
        tenant_id=ctx.tenant_id,
        action="BREAK_GLASS_REVIEWED",
        object_type="break_glass_grant",
        object_id=str(grant.id),
        outcome=req.outcome,
        actor_principal_id=ctx.principal_id,
        actor_kind=ctx.kind.value,
    )
    session.flush()
    return _view(grant, now)


def list_all(session: Session, ctx: PrincipalContext, *, limit: int) -> dict[str, Any]:
    require(ctx, Permission.TENANT_ADMIN)
    now = datetime.now(UTC)
    rows = session.scalars(
        select(BreakGlassGrant)
        .where(BreakGlassGrant.tenant_id == ctx.tenant_id)
        .order_by(BreakGlassGrant.activated_at.desc())
        .limit(limit)
    ).all()
    return {"items": [_view(g, now) for g in rows]}
