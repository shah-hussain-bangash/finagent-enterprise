"""Permission-filtered case reads (FR-002, FR-010, SPR-009, prompt §18).

Scoping is applied IN the SQL query (tenant + row scope) so a concealed case and a non-existent
case follow exactly the same code path and produce the same 404 body. Lists never return counts
of inaccessible rows.
"""

from __future__ import annotations

import base64
import binascii
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ColumnElement, Select, and_, exists, false, literal, or_, select, true, tuple_
from sqlalchemy.orm import Session

from finagent.core.errors import AppError, ErrorCode
from finagent.database.models import Alert, Case, CaseAssignment, CaseStatusHistory
from finagent.domain.states import CaseStatus
from finagent.security.authorization.policy import (
    CaseScope,
    Permission,
    PrincipalContext,
    Role,
    case_scope,
    require,
)
from finagent.services.audit_service import writer as audit
from finagent.services.case_service import escalation


def scope_filter(ctx: PrincipalContext) -> ColumnElement[bool]:
    """Row-scope predicate on `Case` (tenant filter applied separately and unconditionally).

    Analysts/reviewers see cases actively assigned to themselves or to a principal whose role
    they hold through an active delegation (FR-003); supervisors and active break-glass see the
    tenant. Anything else sees nothing (deny by default).
    """
    scope = case_scope(ctx)
    if scope is CaseScope.TENANT:
        return true()
    if scope is CaseScope.NONE:
        return false()
    clauses = []
    for role in (Role.ANALYST, Role.REVIEWER):
        ids = ctx.acting_for(role)
        if ids:
            clauses.append(
                exists().where(
                    CaseAssignment.tenant_id == Case.tenant_id,
                    CaseAssignment.case_id == Case.id,
                    CaseAssignment.principal_id.in_(ids),
                    CaseAssignment.assignment_role == role.value,
                    CaseAssignment.active.is_(True),
                )
            )
    return or_(*clauses) if clauses else false()


def _scoped(ctx: PrincipalContext) -> Select[tuple[Case, Alert]]:
    return (
        select(Case, Alert)
        .join(Alert, and_(Alert.tenant_id == Case.tenant_id, Alert.id == Case.alert_id))
        .where(Case.tenant_id == ctx.tenant_id)  # tenant filter is unconditional
        .where(scope_filter(ctx))
    )


def load_scoped_case(
    session: Session, ctx: PrincipalContext, raw_case_id: str, *, for_update: bool = False
) -> Case | None:
    """Return the case if it exists AND is in the principal's scope; None otherwise (-> 404)."""
    try:
        case_id = uuid.UUID(raw_case_id)
    except ValueError:
        return None
    stmt = select(Case).where(Case.tenant_id == ctx.tenant_id, Case.id == case_id, scope_filter(ctx))
    if for_update:
        stmt = stmt.with_for_update()
    return session.scalar(stmt)


def _access_details(ctx: PrincipalContext) -> dict[str, Any]:
    """How the principal reached the data: break-glass grant or delegation (SPR-008, FR-003)."""
    details: dict[str, Any] = {}
    if ctx.break_glass_grant_id:
        details["break_glass_grant_id"] = str(ctx.break_glass_grant_id)
    if ctx.delegations:
        details["delegation_ids"] = [str(d.delegation_id) for d in ctx.delegations]
    return details


def _summary(case: Case, alert: Alert) -> dict[str, Any]:
    return {
        "case_id": str(case.id),
        "status": case.status,
        "priority": case.priority,
        "queue": case.queue,
        "alert_type": alert.alert_type,
        "source_alert_id": alert.source_alert_id,
        "source_system": alert.source_system,
        "created_at": case.created_at.isoformat(),
        "updated_at": case.updated_at.isoformat(),
        "record_version": case.record_version,
    }


def get_case(session: Session, ctx: PrincipalContext, raw_case_id: str) -> dict[str, Any] | None:
    """Return the case detail, or None when absent OR concealed (caller responds 404)."""
    require(ctx, Permission.CASE_READ)
    try:
        case_id: uuid.UUID | None = uuid.UUID(raw_case_id)
    except ValueError:
        case_id = None
    row = session.execute(_scoped(ctx).where(Case.id == case_id)).first() if case_id else None
    if row is None:
        # Recorded in the REQUESTER's tenant only: feeds the cross-tenant deny-spike alert (§36)
        # without revealing anything about other tenants.
        audit.record(
            session,
            tenant_id=ctx.tenant_id,
            action="CASE_READ_DENIED_OR_ABSENT",
            object_type="case",
            object_id=None,
            outcome="NOT_FOUND",
            actor_principal_id=ctx.principal_id,
            actor_kind=ctx.kind.value,
        )
        return None
    case, alert = row
    history = session.scalars(
        select(CaseStatusHistory)
        .where(CaseStatusHistory.tenant_id == ctx.tenant_id, CaseStatusHistory.case_id == case.id)
        .order_by(CaseStatusHistory.seq)
    ).all()
    assignments = {
        a.assignment_role: a
        for a in session.scalars(
            select(CaseAssignment).where(
                CaseAssignment.tenant_id == ctx.tenant_id,
                CaseAssignment.case_id == case.id,
                CaseAssignment.active.is_(True),
            )
        )
    }
    assignment, reviewer = assignments.get("ANALYST"), assignments.get("REVIEWER")
    audit.record(
        session,
        tenant_id=ctx.tenant_id,
        action="BREAK_GLASS_CASE_VIEWED" if ctx.break_glass_grant_id else "CASE_VIEWED",
        object_type="case",
        object_id=str(case.id),
        outcome="SUCCESS",
        actor_principal_id=ctx.principal_id,
        actor_kind=ctx.kind.value,
        case_id=case.id,
        details=_access_details(ctx),
    )
    detail = _summary(case, alert)
    due_at = escalation.due_at_for(session, case)
    detail.update(
        {
            "classification": case.classification,
            "status_changed_at": case.status_changed_at.isoformat(),
            "due_at": due_at.isoformat() if due_at else None,
            "blocked_reason": case.blocked_reason,
            "blocked_owner_role": case.blocked_owner_role,
            "assigned_reviewer_id": str(reviewer.principal_id) if reviewer else None,
            "alert": {
                "alert_id": str(alert.id),
                "occurred_at": alert.occurred_at.isoformat(),
                "received_at": alert.received_at.isoformat(),
                "severity": alert.severity,
                "rule_ref": alert.rule_ref,
                "subject_refs": list(alert.subject_refs),
                "window_start": alert.window_start.isoformat(),
                "window_end": alert.window_end.isoformat(),
            },
            "assigned_analyst_id": str(assignment.principal_id) if assignment else None,
            "timeline": [
                {
                    "from": h.from_status,
                    "to": h.to_status,
                    "actor": h.actor,
                    "reason": h.reason,
                    "at": h.occurred_at.isoformat(),
                }
                for h in history
            ],
        }
    )
    return detail


def _encode_cursor(created_at: datetime, case_id: uuid.UUID) -> str:
    return base64.urlsafe_b64encode(f"{created_at.isoformat()}|{case_id}".encode()).decode()


def _decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        ts, cid = base64.urlsafe_b64decode(cursor.encode()).decode().split("|")
        return datetime.fromisoformat(ts), uuid.UUID(cid)
    except (ValueError, binascii.Error) as exc:
        raise AppError(ErrorCode.SCHEMA_VALIDATION_FAILED, "Invalid cursor.", {"field": "cursor"}) from exc


def list_cases(
    session: Session,
    ctx: PrincipalContext,
    *,
    limit: int,
    status: str | None = None,
    priority: str | None = None,
    cursor: str | None = None,
) -> dict[str, Any]:
    require(ctx, Permission.CASE_READ)
    stmt = _scoped(ctx)
    if status is not None:
        if status not in CaseStatus.__members__:
            raise AppError(ErrorCode.SCHEMA_VALIDATION_FAILED, "Unknown status filter.", {"field": "status"})
        stmt = stmt.where(Case.status == status)
    if priority is not None:
        if priority not in {"LOW", "NORMAL", "HIGH", "URGENT"}:
            raise AppError(ErrorCode.SCHEMA_VALIDATION_FAILED, "Unknown priority filter.", {"field": "priority"})
        stmt = stmt.where(Case.priority == priority)
    if cursor:
        ts, cid = _decode_cursor(cursor)
        stmt = stmt.where(tuple_(Case.created_at, Case.id) < tuple_(literal(ts), literal(cid)))
    rows = session.execute(stmt.order_by(Case.created_at.desc(), Case.id.desc()).limit(limit + 1)).all()
    items = [_summary(c, a) for c, a in rows[:limit]]
    if ctx.break_glass_grant_id:  # every emergency read is attributable (SPR-008)
        audit.record(
            session,
            tenant_id=ctx.tenant_id,
            action="BREAK_GLASS_CASES_LISTED",
            object_type="case",
            object_id=None,
            outcome="SUCCESS",
            actor_principal_id=ctx.principal_id,
            actor_kind=ctx.kind.value,
            details={**_access_details(ctx), "returned": len(items)},
        )
    next_cursor = (
        _encode_cursor(rows[limit - 1][0].created_at, rows[limit - 1][0].id) if len(rows) > limit else None
    )
    return {"items": items, "next_cursor": next_cursor}
