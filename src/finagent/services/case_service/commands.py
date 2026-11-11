"""Case commands: guarded PATCH and supervisor reassignment (FR-004, FR-009, UI-002, SPR-005).

Optimistic concurrency: every update names the record_version it was based on (If-Match header or
body field). The row is locked FOR UPDATE inside the scoped query, so a stale writer gets a 409
and never silently overwrites accepted work. Concealed and absent cases both yield the same 404.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from finagent.core.correlation import get_correlation_id
from finagent.core.errors import AppError, ErrorCode, not_found
from finagent.database.models import CaseAssignment, OutboxEvent, Principal, RoleAssignment
from finagent.domain.states import CASE_TERMINAL, CaseStatus
from finagent.security.authorization.policy import Permission, PrincipalContext, require
from finagent.services.audit_service import writer as audit
from finagent.services.case_service import queries
from finagent.services.case_service.transitions import Actor, bump, change_status, check_version

Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=300)]
OwnerRole = Literal["ANALYST", "AML_SUPERVISOR", "REVIEWER", "POLICY_ADMIN", "TENANT_ADMIN"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CasePatch(_Strict):
    """Fields a user may change directly. Review/approval/finalization statuses are NOT here: they
    are reachable only through their own deterministic services (FR-028, FR-029)."""

    record_version: int | None = Field(default=None, ge=1)
    status: Literal["INVESTIGATING", "BLOCKED", "CANCELLED"] | None = None
    priority: Literal["LOW", "NORMAL", "HIGH", "URGENT"] | None = None
    reason: Reason | None = None
    blocked_owner_role: OwnerRole | None = None

    @model_validator(mode="after")
    def _shape(self) -> CasePatch:
        if self.status is None and self.priority is None:
            raise ValueError("nothing to change: provide status and/or priority")
        if self.reason is None:
            raise ValueError("reason is required for every case change")
        if (self.status == "BLOCKED") != (self.blocked_owner_role is not None):
            raise ValueError("blocked_owner_role is required for, and only for, status BLOCKED")
        return self


class AssignmentRequest(_Strict):
    principal_id: uuid.UUID
    role: Literal["ANALYST", "REVIEWER"]
    reason: Reason
    record_version: int | None = Field(default=None, ge=1)


def _expected_version(if_match: int | None, body_version: int | None) -> int:
    if if_match is not None and body_version is not None and if_match != body_version:
        raise AppError(ErrorCode.MALFORMED_REQUEST, "If-Match and record_version disagree.")
    expected = if_match if if_match is not None else body_version
    if expected is None:
        raise AppError(
            ErrorCode.PRECONDITION_REQUIRED,
            "Send the case's record_version (If-Match header or body) so concurrent edits are detected.",
        )
    return expected


def update_case(
    session: Session, ctx: PrincipalContext, raw_case_id: str, patch: CasePatch, if_match: int | None
) -> dict[str, Any]:
    require(ctx, Permission.CASE_READ)
    expected = _expected_version(if_match, patch.record_version)
    case = queries.load_scoped_case(session, ctx, raw_case_id, for_update=True)
    if case is None:
        raise not_found()
    # Field-level permissions: analysts work their case; supervisors cancel and re-prioritise.
    if patch.status in ("INVESTIGATING", "BLOCKED"):
        require(ctx, Permission.CASE_UPDATE)
    if patch.status == "CANCELLED" or patch.priority is not None:
        require(ctx, Permission.CASE_ASSIGN)
    check_version(case, expected)
    if CaseStatus(case.status) in CASE_TERMINAL:
        raise AppError(ErrorCode.CASE_INVALID_STATE, "Completed or cancelled cases cannot be changed.")

    actor = Actor.of(ctx)
    reason = patch.reason or ""
    if patch.status is not None:
        target = CaseStatus(patch.status)
        if target is CaseStatus.INVESTIGATING and case.status != CaseStatus.BLOCKED.value:
            # ASSIGNED -> INVESTIGATING happens only when an authorized analyst starts a run.
            raise AppError(
                ErrorCode.CASE_INVALID_STATE,
                "Investigation starts with a workflow run; PATCH can only resume a blocked case.",
                {"current_status": case.status},
            )
        change_status(session, case, target, actor, reason, details={"reason": reason})
        if target is CaseStatus.BLOCKED:
            case.blocked_reason, case.blocked_owner_role = reason, patch.blocked_owner_role
    if patch.priority is not None and patch.priority != case.priority:
        previous = case.priority
        case.priority = patch.priority
        if patch.status is None:
            bump(case)
        audit.record(
            session,
            tenant_id=ctx.tenant_id,
            action="CASE_PRIORITY_CHANGED",
            object_type="case",
            object_id=str(case.id),
            outcome="SUCCESS",
            actor_principal_id=ctx.principal_id,
            actor_kind=ctx.kind.value,
            case_id=case.id,
            details={"from": previous, "to": patch.priority, "reason": reason},
        )
    session.flush()
    return {
        "case_id": str(case.id),
        "status": case.status,
        "priority": case.priority,
        "record_version": case.record_version,
        "blocked_reason": case.blocked_reason,
        "blocked_owner_role": case.blocked_owner_role,
        "updated_at": case.updated_at.isoformat(),
    }


def _eligible_assignee(session: Session, ctx: PrincipalContext, principal_id: uuid.UUID, role: str) -> bool:
    """Same tenant, active human, holding the role DIRECTLY now (delegated roles do not count)."""
    now = datetime.now(UTC)
    found = session.scalar(
        select(Principal.id)
        .join(
            RoleAssignment,
            and_(
                RoleAssignment.tenant_id == Principal.tenant_id, RoleAssignment.principal_id == Principal.id
            ),
        )
        .where(
            Principal.tenant_id == ctx.tenant_id,
            Principal.id == principal_id,
            Principal.status == "ACTIVE",
            Principal.kind == "USER",
            RoleAssignment.role == role,
            RoleAssignment.valid_from <= now,
            or_(RoleAssignment.valid_to.is_(None), RoleAssignment.valid_to > now),
        )
    )
    return found is not None


def reassign(
    session: Session, ctx: PrincipalContext, raw_case_id: str, body: AssignmentRequest, if_match: int | None
) -> dict[str, Any]:
    """Supervisor-only reassignment with a mandatory reason; full ownership history kept (FR-009)."""
    require(ctx, Permission.CASE_ASSIGN)  # includes the MFA step-up (SPR-005)
    case = queries.load_scoped_case(session, ctx, raw_case_id, for_update=True)
    if case is None:
        raise not_found()
    if if_match is not None or body.record_version is not None:
        check_version(case, _expected_version(if_match, body.record_version))
    if CaseStatus(case.status) in CASE_TERMINAL:
        raise AppError(ErrorCode.CASE_INVALID_STATE, "Completed or cancelled cases cannot be reassigned.")
    if not _eligible_assignee(session, ctx, body.principal_id, body.role):
        # Same answer for "other tenant", "unknown", "inactive" and "lacks role": no enumeration.
        raise AppError(
            ErrorCode.SEMANTIC_VALIDATION_FAILED,
            "The selected person cannot take this assignment.",
            {"field": "principal_id"},
        )
    active = {
        a.assignment_role: a
        for a in session.scalars(
            select(CaseAssignment).where(
                CaseAssignment.tenant_id == ctx.tenant_id,
                CaseAssignment.case_id == case.id,
                CaseAssignment.active.is_(True),
            )
        )
    }
    other_role = "REVIEWER" if body.role == "ANALYST" else "ANALYST"
    if other_role in active and active[other_role].principal_id == body.principal_id:
        raise AppError(
            ErrorCode.FORBIDDEN, "Separation of duties: the analyst and reviewer of a case must differ."
        )
    previous = active.get(body.role)
    if previous is not None and previous.principal_id == body.principal_id:
        raise AppError(ErrorCode.CASE_INVALID_STATE, "The case is already assigned to this person.")
    now = datetime.now(UTC)
    if previous is not None:
        previous.active, previous.ended_at = False, now
        session.flush()  # release the partial unique index before inserting the new row
    session.add(
        CaseAssignment(
            tenant_id=ctx.tenant_id,
            case_id=case.id,
            principal_id=body.principal_id,
            assignment_role=body.role,
            reason=body.reason,
            assigned_by=Actor.of(ctx).label,
        )
    )
    actor = Actor.of(ctx)
    if body.role == "ANALYST" and case.status == CaseStatus.RECEIVED.value:
        change_status(session, case, CaseStatus.ASSIGNED, actor, body.reason)
    else:
        bump(case, now)
    audit.record(
        session,
        tenant_id=ctx.tenant_id,
        action="CASE_REASSIGNED",
        object_type="case",
        object_id=str(case.id),
        outcome="SUCCESS",
        actor_principal_id=ctx.principal_id,
        actor_kind=ctx.kind.value,
        case_id=case.id,
        details={
            "role": body.role,
            "from_principal_id": str(previous.principal_id) if previous else None,
            "to_principal_id": str(body.principal_id),
            "reason": body.reason,
        },
    )
    session.add(
        OutboxEvent(
            tenant_id=ctx.tenant_id,
            event_type="case.assigned.v1",
            schema_version="1",
            resource_id=str(case.id),
            correlation_id=get_correlation_id(),
            payload={"case_id": str(case.id), "role": body.role, "principal_id": str(body.principal_id)},
        )
    )
    session.flush()
    return {
        "case_id": str(case.id),
        "status": case.status,
        "record_version": case.record_version,
        "assignment": {"role": body.role, "principal_id": str(body.principal_id), "reason": body.reason},
    }
