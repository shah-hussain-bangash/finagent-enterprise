"""Approval invalidation (FR-006, AER-016, DIR-018, SPR-033).

An approval binds an exact report version and evidence snapshot. When reviewed facts change, every
PENDING or APPROVED request for the case becomes INVALIDATED (kept, never deleted), the report
returns to DRAFT and the case moves back to REWORK, so nothing can be finalized on stale content.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from finagent.core.correlation import get_correlation_id
from finagent.database.models import ApprovalRequest, Case, Finalization, OutboxEvent, Report
from finagent.domain.states import CaseStatus
from finagent.services.audit_service import writer as audit
from finagent.services.case_service.transitions import Actor, change_status

OPEN_STATUSES = ("PENDING", "APPROVED")
_REWORK_FROM = frozenset({CaseStatus.AWAITING_REVIEW, CaseStatus.APPROVED, CaseStatus.FINALIZING})


def invalidate_for_case(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    case_id: uuid.UUID,
    reason: str,
    actor_principal_id: uuid.UUID | None,
    actor_kind: str,
) -> int:
    requests = list(
        session.scalars(
            select(ApprovalRequest)
            .where(
                ApprovalRequest.tenant_id == tenant_id,
                ApprovalRequest.case_id == case_id,
                ApprovalRequest.status.in_(OPEN_STATUSES),
            )
            .with_for_update()
        )
    )
    if not requests:
        return 0
    now = datetime.now(UTC)
    actor = Actor(actor_principal_id, actor_kind, f"{actor_kind.lower()}:{actor_principal_id or 'system'}")
    for req in requests:
        previous = req.status
        req.status, req.status_reason, req.status_changed_at = "INVALIDATED", reason[:300], now
        req.record_version += 1
        for fin in session.scalars(
            select(Finalization).where(
                Finalization.tenant_id == tenant_id,
                Finalization.approval_request_id == req.id,
                Finalization.status == "STARTED",
            )
        ):
            fin.status, fin.status_reason, fin.updated_at = "INVALIDATED", reason[:300], now
        audit.record(
            session,
            tenant_id=tenant_id,
            action="APPROVAL_INVALIDATED",
            object_type="approval_request",
            object_id=str(req.id),
            outcome="SUCCESS",
            actor_principal_id=actor_principal_id,
            actor_kind=actor_kind,
            case_id=case_id,
            details={"previous_status": previous, "reason": reason[:300]},
        )
        session.add(
            OutboxEvent(
                tenant_id=tenant_id,
                event_type="approval.invalidated.v1",
                schema_version="1",
                resource_id=str(req.id),
                correlation_id=get_correlation_id(),
                payload={"approval_request_id": str(req.id), "case_id": str(case_id), "reason": reason[:100]},
            )
        )
    report = session.scalar(
        select(Report).where(Report.tenant_id == tenant_id, Report.case_id == case_id).with_for_update()
    )
    if report is not None and report.status in ("SUBMITTED", "APPROVED"):
        report.status, report.updated_at = "DRAFT", now
        report.record_version += 1
    case = session.scalar(
        select(Case).where(Case.tenant_id == tenant_id, Case.id == case_id).with_for_update()
    )
    if case is not None and CaseStatus(case.status) in _REWORK_FROM:
        change_status(session, case, CaseStatus.REWORK, actor, f"approval invalidated: {reason}"[:300])
    session.flush()
    return len(requests)
