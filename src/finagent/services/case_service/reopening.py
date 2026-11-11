"""Explicit supervisor reopening, preserving the completed investigation (FR-006).

The caller owns the transaction. Case, report, draft copy, history, audit and outbox
commit together. Existing completion records and committed external effects stay intact.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from finagent.core.correlation import get_correlation_id
from finagent.core.errors import AppError, ErrorCode, not_found
from finagent.database.models import (
    ApprovalRequest,
    CaseStatusHistory,
    ExternalOperation,
    Finalization,
    OutboxEvent,
    Report,
    ReportClaim,
    ReportVersion,
    WorkflowRun,
)
from finagent.domain.states import RUN_TERMINAL, CaseStatus
from finagent.security.authorization.policy import (
    Permission,
    PrincipalContext,
    Role,
    require,
)
from finagent.services.audit_service import writer as audit
from finagent.services.case_service import queries
from finagent.services.case_service.commands import Reason
from finagent.services.case_service.transitions import (
    Actor,
    change_status,
    check_version,
)
from finagent.services.review_service.reports import current_version


class ReopenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    reason: Reason
    record_version: int | None = Field(default=None, ge=1)


def reopen_case(
    session: Session,
    ctx: PrincipalContext,
    raw_case_id: str,
    body: ReopenRequest,
    if_match: int | None,
) -> dict[str, Any]:
    require(ctx, Permission.CASE_READ)
    if if_match is not None and body.record_version is not None and if_match != body.record_version:
        raise AppError(ErrorCode.MALFORMED_REQUEST, "If-Match and record_version disagree.")
    expected = if_match if if_match is not None else body.record_version
    if expected is None:
        raise AppError(
            ErrorCode.PRECONDITION_REQUIRED,
            "Send the case's record_version (If-Match header or body).",
        )
    case = queries.load_scoped_case(session, ctx, raw_case_id, for_update=True)
    if case is None:
        raise not_found()
    # CASE_ASSIGN belongs to AML supervisors and uses the existing MFA step-up policy.
    require(ctx, Permission.CASE_ASSIGN)
    check_version(case, expected)
    if case.status != CaseStatus.COMPLETED.value:
        raise AppError(ErrorCode.CASE_INVALID_STATE, "Only a completed case can be reopened.")
    active = session.scalar(
        select(WorkflowRun.id).where(
            WorkflowRun.tenant_id == ctx.tenant_id,
            WorkflowRun.case_id == case.id,
            WorkflowRun.status.not_in([status.value for status in RUN_TERMINAL]),
        )
    )
    unresolved_effect = session.scalar(
        select(ExternalOperation.id).where(
            ExternalOperation.tenant_id == ctx.tenant_id,
            ExternalOperation.case_id == case.id,
            ExternalOperation.status.in_(("PENDING", "SENT", "OUTCOME_UNCERTAIN")),
        )
    )
    if active is not None or unresolved_effect is not None:
        raise AppError(
            ErrorCode.RUN_INVALID_STATE,
            "The case still has an active or unresolved run.",
        )
    completion = session.scalar(
        select(CaseStatusHistory)
        .where(
            CaseStatusHistory.tenant_id == ctx.tenant_id,
            CaseStatusHistory.case_id == case.id,
            CaseStatusHistory.to_status == CaseStatus.COMPLETED.value,
        )
        .order_by(CaseStatusHistory.seq.desc())
        .limit(1)
    )
    report = session.scalar(
        select(Report).where(Report.tenant_id == ctx.tenant_id, Report.case_id == case.id).with_for_update()
    )
    if completion is None or report is None or report.status != "FINALIZED":
        raise AppError(
            ErrorCode.CASE_INVALID_STATE,
            "The completed case lacks finalized report history.",
        )
    previous = current_version(session, report)
    consumed = session.scalar(
        select(ApprovalRequest).where(
            ApprovalRequest.tenant_id == ctx.tenant_id,
            ApprovalRequest.case_id == case.id,
            ApprovalRequest.report_id == report.id,
            ApprovalRequest.report_version_id == previous.id,
            ApprovalRequest.snapshot_id == previous.snapshot_id,
            ApprovalRequest.status == "CONSUMED",
        )
    )
    finalization = (
        session.scalar(
            select(Finalization).where(
                Finalization.tenant_id == ctx.tenant_id,
                Finalization.case_id == case.id,
                Finalization.approval_request_id == consumed.id,
                Finalization.status.in_(("COMPLETED", "WRITE_NOT_PERFORMED")),
            )
        )
        if consumed is not None
        else None
    )
    open_request = session.scalar(
        select(ApprovalRequest.id).where(
            ApprovalRequest.tenant_id == ctx.tenant_id,
            ApprovalRequest.case_id == case.id,
            ApprovalRequest.status.in_(("PENDING", "APPROVED")),
        )
    )
    if consumed is None or finalization is None or open_request is not None:
        raise AppError(
            ErrorCode.CASE_INVALID_STATE,
            "The prior completion evidence is inconsistent.",
        )

    now = datetime.now(UTC)
    lineage = deepcopy(previous.lineage)
    lineage["reopening"] = {
        "previous_report_version_id": str(previous.id),
        "completion_history_id": str(completion.id),
        "consumed_approval_request_id": str(consumed.id),
        "finalization_id": str(finalization.id),
        "reopened_by": str(ctx.principal_id),
        "recorded_at": now.isoformat(),
        "reason": body.reason,
    }
    # Copy unchanged content into a new, explicitly unapproved revision. The old
    # consumed approval remains bound to the old immutable version only.
    draft = ReportVersion(
        tenant_id=ctx.tenant_id,
        report_id=report.id,
        version_no=report.current_version_no + 1,
        snapshot_id=previous.snapshot_id,
        content=deepcopy(previous.content),
        content_hash=previous.content_hash,
        origin="ANALYST_EDIT",
        author_principal_id=ctx.principal_id,
        author_label=f"user:{ctx.principal_id} (reopening copy; no new analysis)",
        base_version_no=previous.version_no,
        edit_note=f"Reopening copy; no content change: {body.reason}",
        lineage=lineage,
        validation=deepcopy(previous.validation),
    )
    session.add(draft)
    session.flush()
    claims = session.scalars(
        select(ReportClaim).where(
            ReportClaim.tenant_id == ctx.tenant_id,
            ReportClaim.report_version_id == previous.id,
        )
    ).all()
    for claim in claims:
        session.add(
            ReportClaim(
                tenant_id=ctx.tenant_id,
                report_version_id=draft.id,
                claim_key=claim.claim_key,
                section=claim.section,
                claim_type=claim.claim_type,
                text=claim.text,
                support_status=claim.support_status,
                evidence_refs=deepcopy(claim.evidence_refs),
                citation_refs=deepcopy(claim.citation_refs),
            )
        )
    report.status, report.updated_at = "DRAFT", now
    report.current_version_no = draft.version_no
    report.record_version += 1
    delegation = ctx.delegation_for(Role.AML_SUPERVISOR)
    details = {
        **lineage["reopening"],
        "new_report_version_id": str(draft.id),
        "delegation_id": str(delegation.delegation_id) if delegation else None,
        "on_behalf_of": str(delegation.delegator_id) if delegation else None,
    }
    change_status(
        session,
        case,
        CaseStatus.REWORK,
        Actor.of(ctx),
        body.reason,
        details=details,
        reopening=True,
    )
    for action, object_type, object_id in (
        ("CASE_REOPENED", "case", str(case.id)),
        ("REPORT_VERSION_CREATED", "report_version", str(draft.id)),
    ):
        audit.record(
            session,
            tenant_id=ctx.tenant_id,
            action=action,
            object_type=object_type,
            object_id=object_id,
            outcome="SUCCESS",
            actor_principal_id=ctx.principal_id,
            actor_kind=ctx.kind.value,
            case_id=case.id,
            details=details,
        )
    session.add(
        OutboxEvent(
            tenant_id=ctx.tenant_id,
            event_type="report.version_created.v1",
            schema_version="1",
            resource_id=str(draft.id),
            correlation_id=get_correlation_id(),
            payload={
                "report_id": str(report.id),
                "case_id": str(case.id),
                "version_no": draft.version_no,
            },
        )
    )
    session.flush()
    return {
        "case_id": str(case.id),
        "status": case.status,
        "record_version": case.record_version,
        "report_status": report.status,
        "report_version_id": str(draft.id),
        "report_version_no": draft.version_no,
        "previous_report_version_id": str(previous.id),
    }
