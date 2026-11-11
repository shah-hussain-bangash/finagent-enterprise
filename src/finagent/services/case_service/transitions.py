"""The single way to change a case's status (SRS §3, FR-004, ADR-0003).

Every transition: domain-legal check -> optimistic version check -> status history -> audit ->
outbox, in the caller's transaction. Illegal transitions raise CASE_INVALID_STATE (409); stale
versions raise VERSION_CONFLICT (409). Workflow, approval and finalization services reuse this.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from finagent.core.correlation import get_correlation_id
from finagent.core.errors import AppError, ErrorCode
from finagent.database.models import Case, CaseStatusHistory, OutboxEvent
from finagent.domain.states import (
    CaseStatus,
    InvalidTransitionError,
    allowed_case_targets,
    case_transition,
    reopening_transition,
)
from finagent.security.authorization.policy import PrincipalContext
from finagent.services.audit_service import writer as audit

_EVENT_FOR_TARGET = {
    CaseStatus.BLOCKED: "case.blocked.v1",
    CaseStatus.CANCELLED: "case.cancelled.v1",
    CaseStatus.AWAITING_REVIEW: "case.awaiting_review.v1",
    CaseStatus.COMPLETED: "case.completed.v1",
}


@dataclass(frozen=True)
class Actor:
    """Who caused a change. SYSTEM is used by the worker/scheduler, never by an API caller."""

    principal_id: uuid.UUID | None
    kind: str  # USER | INTEGRATION | SYSTEM
    label: str

    @classmethod
    def of(cls, ctx: PrincipalContext) -> Actor:
        return cls(ctx.principal_id, ctx.kind.value, f"{ctx.kind.value.lower()}:{ctx.principal_id}")

    @classmethod
    def system(cls, component: str) -> Actor:
        return cls(None, "SYSTEM", f"system:{component}")


def check_version(case: Case, expected: int | None) -> None:
    if expected is not None and case.record_version != expected:
        raise AppError(
            ErrorCode.VERSION_CONFLICT,
            "The case was changed by someone else. Reload it and re-apply your change.",
            {"current_version": case.record_version},
        )


def bump(case: Case, now: datetime | None = None) -> None:
    case.record_version += 1
    case.updated_at = now or datetime.now(UTC)


def change_status(
    session: Session,
    case: Case,
    target: CaseStatus,
    actor: Actor,
    reason: str,
    *,
    details: dict[str, Any] | None = None,
    reopening: bool = False,
) -> None:
    """Apply a legal status change to a row the caller has already locked and version-checked."""
    current = CaseStatus(case.status)
    try:
        if reopening:
            if target is not CaseStatus.REWORK:
                raise InvalidTransitionError("case reopening", current, target)
            reopening_transition(current)
        else:
            case_transition(current, target)
    except InvalidTransitionError as exc:
        raise AppError(
            ErrorCode.CASE_INVALID_STATE,
            "This status change is not allowed from the case's current status.",
            {
                "current_status": current.value,
                "allowed": sorted(t.value for t in allowed_case_targets(current)),
            },
        ) from exc
    now = datetime.now(UTC)
    case.status = target.value
    case.status_changed_at = now
    if target is not CaseStatus.BLOCKED:
        case.blocked_reason, case.blocked_owner_role = None, None
    bump(case, now)
    corr = get_correlation_id()
    session.add(
        CaseStatusHistory(
            tenant_id=case.tenant_id,
            case_id=case.id,
            from_status=current.value,
            to_status=target.value,
            actor=actor.label,
            reason=reason[:300],
            correlation_id=corr,
        )
    )
    audit.record(
        session,
        tenant_id=case.tenant_id,
        action="CASE_STATUS_CHANGED",
        object_type="case",
        object_id=str(case.id),
        outcome="SUCCESS",
        actor_principal_id=actor.principal_id,
        actor_kind=actor.kind,
        case_id=case.id,
        details={"from": current.value, "to": target.value, **(details or {})},
    )
    session.add(
        OutboxEvent(
            tenant_id=case.tenant_id,
            event_type=_EVENT_FOR_TARGET.get(target, "case.status_changed.v1"),
            schema_version="1",
            resource_id=str(case.id),
            correlation_id=corr,
            payload={"case_id": str(case.id), "from": current.value, "to": target.value},
        )
    )
