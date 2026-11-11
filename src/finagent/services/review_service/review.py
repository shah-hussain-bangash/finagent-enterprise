"""Submission, withdrawal and independent reviewer decisions (FR-027, FR-028, FR-003, SPR-005, SPR-033,
UI-002, DIR-018; prompt §19).

* Submit freezes the exact report version and evidence snapshot into an ApprovalRequest, assigns an
  eligible reviewer (never the submitter) and moves the case to AWAITING_REVIEW.
* Decide: APPROVE / RETURN_FOR_REWORK / REQUEST_INFORMATION (SRS FR-028) and REJECT (BRD; effect per
  `approval_reject_effect`, DEC-11 default REWORK). Rationale mandatory; MFA step-up; the decider must
  be the assigned reviewer (directly or by an explicit delegation) and must not be the submitter, an
  author of the report or the case analyst - including through a delegation (FR-003 conflicts).
  The caller states the report version and snapshot it reviewed; anything superseded is a 409.
  Decisions are idempotent (key) and append-only (one per request).
* Only deterministic decisions resume the waiting workflow run; model output never does.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from finagent.core.config import Settings
from finagent.core.correlation import get_correlation_id
from finagent.core.errors import AppError, ErrorCode, error_body, not_found
from finagent.database.models import (
    ApprovalDecision,
    ApprovalRequest,
    Case,
    CaseAssignment,
    EvidenceConflict,
    EvidenceSnapshot,
    OutboxEvent,
    Principal,
    Report,
    ReportVersion,
    RoleAssignment,
    WorkflowRun,
)
from finagent.domain.fingerprint import fingerprint
from finagent.domain.states import CaseStatus, RunStatus
from finagent.security.authorization.policy import Permission, PrincipalContext, Role, require
from finagent.services import idempotency
from finagent.services.audit_service import writer as audit
from finagent.services.case_service.queries import load_scoped_case, scope_filter
from finagent.services.case_service.transitions import Actor, change_status
from finagent.services.evidence_service.items import current_items
from finagent.services.evidence_service.snapshots import latest_snapshot, snapshot_items
from finagent.services.review_service.approvals import invalidate_for_case
from finagent.services.review_service.reports import current_version, load_report, version_view
from finagent.services.workflow_service.runs import resume_run

MATRIX_VERSION = "reviewer-matrix-0.1-SYNTHETIC"  # DEC-06 OPEN: default matrix
Rationale = Annotated[str, StringConstraints(strip_whitespace=True, min_length=10, max_length=2000)]


class SubmitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version_no: int


class WithdrawRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=5, max_length=300)]


class DecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    decision: Literal["APPROVE", "RETURN_FOR_REWORK", "REQUEST_INFORMATION", "REJECT"]
    rationale: Rationale
    report_version_id: uuid.UUID  # what the reviewer saw (UI-002 stale-content protection)
    snapshot_id: uuid.UUID


def snapshot_drift(
    session: Session, tenant_id: uuid.UUID, case_id: uuid.UUID, snapshot_id: uuid.UUID
) -> list[str]:
    """Reviewed evidence vs current evidence: content changes or new sources are drift (FR-006)."""
    reviewed = {
        (i.source_type, i.source_ref): i.content_hash for i in snapshot_items(session, tenant_id, snapshot_id)
    }
    now = {(i.source_type, i.source_ref): i.content_hash for i in current_items(session, tenant_id, case_id)}
    drift = [f"CHANGED:{k[0]}" for k, h in now.items() if k in reviewed and reviewed[k] != h]
    drift += [f"NEW:{k[0]}" for k in now if k not in reviewed]
    return sorted(set(drift))


def _waiting_run(session: Session, tenant_id: uuid.UUID, case_id: uuid.UUID) -> WorkflowRun | None:
    return session.scalar(
        select(WorkflowRun)
        .where(
            WorkflowRun.tenant_id == tenant_id,
            WorkflowRun.case_id == case_id,
            WorkflowRun.status.in_([RunStatus.WAITING_REVIEW.value, RunStatus.WAITING_EVIDENCE.value]),
        )
        .with_for_update()
    )


def _pick_reviewer(session: Session, case: Case, exclude: set[uuid.UUID]) -> uuid.UUID | None:
    active = session.scalar(
        select(CaseAssignment.principal_id).where(
            CaseAssignment.tenant_id == case.tenant_id,
            CaseAssignment.case_id == case.id,
            CaseAssignment.assignment_role == "REVIEWER",
            CaseAssignment.active.is_(True),
        )
    )
    if active is not None and active not in exclude:
        return active
    now = datetime.now(UTC)
    load = (
        select(func.count(CaseAssignment.id))
        .where(
            CaseAssignment.tenant_id == Principal.tenant_id,
            CaseAssignment.principal_id == Principal.id,
            CaseAssignment.assignment_role == "REVIEWER",
            CaseAssignment.active.is_(True),
        )
        .scalar_subquery()
    )
    candidates = session.execute(
        select(Principal.id, load)
        .join(
            RoleAssignment,
            and_(
                RoleAssignment.tenant_id == Principal.tenant_id, RoleAssignment.principal_id == Principal.id
            ),
        )
        .where(
            Principal.tenant_id == case.tenant_id,
            Principal.status == "ACTIVE",
            Principal.kind == "USER",
            RoleAssignment.role == Role.REVIEWER.value,
            RoleAssignment.valid_from <= now,
            or_(RoleAssignment.valid_to.is_(None), RoleAssignment.valid_to > now),
        )
        .order_by(load, Principal.id)
    ).all()
    return next((pid for pid, _ in candidates if pid not in exclude), None)


def _authors(session: Session, report: Report) -> set[uuid.UUID]:
    return {
        a
        for a in session.scalars(
            select(ReportVersion.author_principal_id).where(
                ReportVersion.tenant_id == report.tenant_id, ReportVersion.report_id == report.id
            )
        )
        if a is not None
    }


def _analyst(session: Session, case: Case) -> uuid.UUID | None:
    return session.scalar(
        select(CaseAssignment.principal_id).where(
            CaseAssignment.tenant_id == case.tenant_id,
            CaseAssignment.case_id == case.id,
            CaseAssignment.assignment_role == "ANALYST",
            CaseAssignment.active.is_(True),
        )
    )


def submit(
    session: Session,
    ctx: PrincipalContext,
    raw_case_id: str,
    body: SubmitRequest,
    key: str,
    retention_days: int,
) -> tuple[int, dict[str, Any]]:
    require(ctx, Permission.REPORT_SUBMIT)
    idem = idempotency.begin(
        session,
        ctx.tenant_id,
        "reports.submit",
        key,
        fingerprint({"case": raw_case_id, **body.model_dump()}),
        retention_days,
    )
    if idem.replay and idem.record.status_code is not None and idem.record.response is not None:
        return idem.record.status_code, idem.record.response
    case, report = load_report(session, ctx, raw_case_id, for_update=True)
    if CaseStatus(case.status) not in (CaseStatus.INVESTIGATING, CaseStatus.REWORK):
        raise AppError(
            ErrorCode.CASE_INVALID_STATE,
            "Only an investigating or rework case can be submitted.",
            {"current_status": case.status},
        )
    if report.status != "DRAFT":
        raise AppError(
            ErrorCode.CASE_INVALID_STATE,
            "The report is already under review.",
            {"report_status": report.status},
        )
    if body.version_no != report.current_version_no:
        raise AppError(
            ErrorCode.VERSION_CONFLICT,
            "Submit the current report version.",
            {"current_version_no": report.current_version_no},
        )
    version = current_version(session, report)
    problems = list(version.validation.get("problems", [])) if not version.validation.get("valid") else []
    latest = latest_snapshot(session, ctx.tenant_id, case.id)
    if latest is None or latest.id != version.snapshot_id:
        problems.append({"code": "REPORT_NOT_BASED_ON_LATEST_SNAPSHOT"})
    drift = snapshot_drift(session, ctx.tenant_id, case.id, version.snapshot_id)
    if drift:
        problems.append({"code": "EVIDENCE_CHANGED_SINCE_SNAPSHOT", "detail": drift})
    open_conflicts = session.scalar(
        select(func.count(EvidenceConflict.id)).where(
            EvidenceConflict.tenant_id == ctx.tenant_id,
            EvidenceConflict.case_id == case.id,
            EvidenceConflict.status == "OPEN",
        )
    )
    if open_conflicts:
        problems.append({"code": "OPEN_EVIDENCE_CONFLICTS", "detail": int(open_conflicts)})
    if problems:
        raise AppError(
            ErrorCode.SEMANTIC_VALIDATION_FAILED,
            "The report cannot be submitted yet.",
            {"problems": problems},
        )
    exclude = {ctx.principal_id} | _authors(session, report)
    reviewer = _pick_reviewer(session, case, exclude)
    if reviewer is None:
        raise AppError(ErrorCode.SEMANTIC_VALIDATION_FAILED, "No eligible independent reviewer is available.")
    existing = session.scalar(
        select(CaseAssignment).where(
            CaseAssignment.tenant_id == case.tenant_id,
            CaseAssignment.case_id == case.id,
            CaseAssignment.assignment_role == "REVIEWER",
            CaseAssignment.active.is_(True),
        )
    )
    if existing is None or existing.principal_id != reviewer:
        if existing is not None:
            existing.active, existing.ended_at = False, datetime.now(UTC)
            session.flush()
        session.add(
            CaseAssignment(
                tenant_id=case.tenant_id,
                case_id=case.id,
                principal_id=reviewer,
                assignment_role="REVIEWER",
                reason="assigned at submission (default matrix, DEC-06)",
                assigned_by=Actor.of(ctx).label,
            )
        )
    run = _waiting_run(session, ctx.tenant_id, case.id)
    request = ApprovalRequest(
        tenant_id=ctx.tenant_id,
        case_id=case.id,
        report_id=report.id,
        report_version_id=version.id,
        snapshot_id=version.snapshot_id,
        requested_by=ctx.principal_id,
        reviewer_id=reviewer,
        matrix_version=MATRIX_VERSION,
        status="PENDING",
        run_id=run.id if run else None,
    )
    session.add(request)
    report.status, report.updated_at = "SUBMITTED", datetime.now(UTC)
    report.record_version += 1
    change_status(
        session, case, CaseStatus.AWAITING_REVIEW, Actor.of(ctx), "report submitted for independent review"
    )
    session.flush()
    audit.record(
        session,
        tenant_id=ctx.tenant_id,
        action="REPORT_SUBMITTED",
        object_type="approval_request",
        object_id=str(request.id),
        outcome="SUCCESS",
        actor_principal_id=ctx.principal_id,
        actor_kind=ctx.kind.value,
        case_id=case.id,
        details={
            "report_version": version.version_no,
            "snapshot_id": str(version.snapshot_id),
            "reviewer_id": str(reviewer),
            "matrix_version": MATRIX_VERSION,
        },
    )
    for event in ("report.submitted.v1", "approval.requested.v1"):
        session.add(
            OutboxEvent(
                tenant_id=ctx.tenant_id,
                event_type=event,
                schema_version="1",
                resource_id=str(request.id),
                correlation_id=get_correlation_id(),
                payload={"approval_request_id": str(request.id), "case_id": str(case.id)},
            )
        )
    body_out = request_view(session, request)
    idem.finish(201, body_out, str(request.id))
    return 201, body_out


def withdraw(
    session: Session, ctx: PrincipalContext, raw_case_id: str, body: WithdrawRequest
) -> dict[str, Any]:
    require(ctx, Permission.REPORT_SUBMIT)
    case, report = load_report(session, ctx, raw_case_id, for_update=True)
    request = session.scalar(
        select(ApprovalRequest)
        .where(
            ApprovalRequest.tenant_id == ctx.tenant_id,
            ApprovalRequest.case_id == case.id,
            ApprovalRequest.status == "PENDING",
        )
        .with_for_update()
    )
    if request is None:
        raise AppError(ErrorCode.CASE_INVALID_STATE, "There is no pending review to withdraw.")
    request.status, request.status_reason, request.status_changed_at = (
        "WITHDRAWN",
        body.reason,
        datetime.now(UTC),
    )
    request.record_version += 1
    report.status = "DRAFT"
    report.record_version += 1
    change_status(session, case, CaseStatus.REWORK, Actor.of(ctx), f"submission withdrawn: {body.reason}")
    audit.record(
        session,
        tenant_id=ctx.tenant_id,
        action="REPORT_WITHDRAWN",
        object_type="approval_request",
        object_id=str(request.id),
        outcome="SUCCESS",
        actor_principal_id=ctx.principal_id,
        actor_kind=ctx.kind.value,
        case_id=case.id,
        details={"reason": body.reason},
    )
    session.add(
        OutboxEvent(
            tenant_id=ctx.tenant_id,
            event_type="report.withdrawn.v1",
            schema_version="1",
            resource_id=str(request.id),
            correlation_id=get_correlation_id(),
            payload={"approval_request_id": str(request.id), "case_id": str(case.id)},
        )
    )
    session.flush()
    return request_view(session, request)


def _load_request(
    session: Session, ctx: PrincipalContext, raw_id: str, *, for_update: bool
) -> tuple[ApprovalRequest, Case]:
    try:
        rid = uuid.UUID(raw_id)
    except ValueError as exc:
        raise not_found() from exc
    stmt = select(ApprovalRequest).where(
        ApprovalRequest.tenant_id == ctx.tenant_id, ApprovalRequest.id == rid
    )
    request = session.scalar(stmt.with_for_update() if for_update else stmt)
    if request is None:
        raise not_found()
    case = load_scoped_case(session, ctx, str(request.case_id), for_update=for_update)
    if case is None:
        raise not_found()  # outside the caller's case scope: indistinguishable from absent
    return request, case


def decide(
    session: Session, ctx: PrincipalContext, raw_id: str, body: DecisionRequest, key: str, settings: Settings
) -> tuple[int, dict[str, Any]]:
    require(ctx, Permission.APPROVAL_DECIDE)  # includes the MFA step-up (SPR-005)
    idem = idempotency.begin(
        session,
        ctx.tenant_id,
        "approvals.decide",
        key,
        fingerprint({"request": raw_id, **body.model_dump(mode="json")}),
        settings.idempotency_retention_days,
    )
    if idem.replay and idem.record.status_code is not None and idem.record.response is not None:
        return idem.record.status_code, idem.record.response
    request, case = _load_request(session, ctx, raw_id, for_update=True)
    if request.status != "PENDING":
        raise AppError(
            ErrorCode.CASE_INVALID_STATE,
            "This review request is no longer pending.",
            {"current_status": request.status},
        )
    # Who may decide: the assigned reviewer, directly or through an active explicit delegation.
    acting_for = ctx.acting_for(Role.REVIEWER)
    if request.reviewer_id not in acting_for:
        raise AppError(
            ErrorCode.FORBIDDEN, "Only the assigned reviewer (or their delegate) can decide this review."
        )
    delegation = ctx.delegation_for(Role.REVIEWER)
    on_behalf = request.reviewer_id if request.reviewer_id != ctx.principal_id else None
    report = session.scalar(
        select(Report)
        .where(Report.tenant_id == ctx.tenant_id, Report.id == request.report_id)
        .with_for_update()
    )
    if report is None:
        raise not_found()
    conflicted = {request.requested_by, _analyst(session, case)} | _authors(session, report)
    if ctx.principal_id in conflicted or (on_behalf is not None and on_behalf in conflicted):
        raise AppError(
            ErrorCode.FORBIDDEN, "Separation of duties: you cannot review work you submitted or authored."
        )
    if body.report_version_id != request.report_version_id or body.snapshot_id != request.snapshot_id:
        raise AppError(
            ErrorCode.VERSION_CONFLICT,
            "You reviewed a superseded version. Reload the current submission.",
            {"report_version_id": str(request.report_version_id), "snapshot_id": str(request.snapshot_id)},
        )
    drift = snapshot_drift(session, ctx.tenant_id, case.id, request.snapshot_id)
    if drift or report.current_version_no != session.scalar(
        select(ReportVersion.version_no).where(ReportVersion.id == request.report_version_id)
    ):
        # The invalidation must COMMIT, so this returns the 409 instead of raising (which would roll back).
        invalidate_for_case(
            session,
            tenant_id=ctx.tenant_id,
            case_id=case.id,
            reason="CONTENT_CHANGED_BEFORE_DECISION",
            actor_principal_id=ctx.principal_id,
            actor_kind=ctx.kind.value,
        )
        session.flush()
        stale = error_body(
            ErrorCode.VERSION_CONFLICT,
            "The evidence or report changed after submission; the review was invalidated.",
            get_correlation_id(),
            {"drift": drift},
        )
        idem.finish(409, stale, None)
        return 409, stale
    decision = body.decision
    effect = {"APPROVE": "APPROVED", "RETURN_FOR_REWORK": "REWORK", "REQUEST_INFORMATION": "BLOCKED"}.get(
        decision
    )
    if decision == "REJECT":
        effect = "CANCELLED" if settings.approval_reject_effect == "CLOSE" else "REWORK"
    actor = Actor.of(ctx)
    now = datetime.now(UTC)
    request.status = {
        "APPROVE": "APPROVED",
        "RETURN_FOR_REWORK": "RETURNED",
        "REQUEST_INFORMATION": "INFO_REQUESTED",
        "REJECT": "REJECTED",
    }[decision]
    request.status_reason, request.status_changed_at = body.rationale[:300], now
    request.record_version += 1
    report.status = "APPROVED" if decision == "APPROVE" else "DRAFT"
    report.record_version += 1
    report.updated_at = now
    if effect == "APPROVED":
        change_status(session, case, CaseStatus.APPROVED, actor, "independent reviewer approved")
    elif effect == "REWORK":
        change_status(session, case, CaseStatus.REWORK, actor, f"reviewer: {decision.lower()}")
    elif effect == "BLOCKED":
        change_status(session, case, CaseStatus.BLOCKED, actor, "reviewer requested information")
        case.blocked_reason, case.blocked_owner_role = body.rationale[:300], "ANALYST"
    else:
        change_status(
            session, case, CaseStatus.CANCELLED, actor, "reviewer rejected; closed with reason (DEC-11)"
        )
    row = ApprovalDecision(
        tenant_id=ctx.tenant_id,
        request_id=request.id,
        decision=decision,
        rationale=body.rationale,
        reviewer_id=ctx.principal_id,
        reviewer_role=Role.REVIEWER.value,
        delegation_id=delegation.delegation_id if delegation else None,
        on_behalf_of=on_behalf,
        mfa_satisfied=ctx.mfa_satisfied,
        report_version_id=request.report_version_id,
        snapshot_id=request.snapshot_id,
        config_version=f"{MATRIX_VERSION}|reject={settings.approval_reject_effect}",
        effect=str(effect),
        idempotency_key=key,
    )
    session.add(row)
    session.flush()
    audit.record(
        session,
        tenant_id=ctx.tenant_id,
        action="APPROVAL_DECIDED",
        object_type="approval_request",
        object_id=str(request.id),
        outcome="SUCCESS",
        actor_principal_id=ctx.principal_id,
        actor_kind=ctx.kind.value,
        case_id=case.id,
        details={
            "decision": decision,
            "effect": effect,
            "decision_id": str(row.id),
            "delegation_id": str(delegation.delegation_id) if delegation else None,
            "on_behalf_of": str(on_behalf) if on_behalf else None,
            "mfa": ctx.mfa_satisfied,
        },
    )
    session.add(
        OutboxEvent(
            tenant_id=ctx.tenant_id,
            event_type="approval.decided.v1",
            schema_version="1",
            resource_id=str(request.id),
            correlation_id=get_correlation_id(),
            payload={"approval_request_id": str(request.id), "case_id": str(case.id), "decision": decision},
        )
    )
    run = _waiting_run(session, ctx.tenant_id, case.id)
    resume = {"APPROVED": "APPROVE", "BLOCKED": "INFO_REQUESTED", "CANCELLED": "CLOSE"}.get(str(effect))
    if run is not None and resume is not None:
        resume_run(
            session,
            ctx.tenant_id,
            run.id,
            {"decision": resume, "approval_request_id": str(request.id)},
            actor,
            f"reviewer decision {decision}",
        )
    out = request_view(session, request)
    idem.finish(200, out, str(request.id))
    return 200, out


def request_view(session: Session, r: ApprovalRequest, *, detail: bool = False) -> dict[str, Any]:
    decision = session.scalar(
        select(ApprovalDecision).where(
            ApprovalDecision.tenant_id == r.tenant_id, ApprovalDecision.request_id == r.id
        )
    )
    out: dict[str, Any] = {
        "approval_request_id": str(r.id),
        "case_id": str(r.case_id),
        "status": r.status,
        "status_reason": r.status_reason,
        "report_version_id": str(r.report_version_id),
        "snapshot_id": str(r.snapshot_id),
        "requested_by": str(r.requested_by),
        "reviewer_id": str(r.reviewer_id) if r.reviewer_id else None,
        "matrix_version": r.matrix_version,
        "created_at": r.created_at.isoformat(),
        "record_version": r.record_version,
        "decision": None
        if decision is None
        else {
            "decision": decision.decision,
            "rationale": decision.rationale,
            "reviewer_id": str(decision.reviewer_id),
            "on_behalf_of": str(decision.on_behalf_of) if decision.on_behalf_of else None,
            "delegation_id": str(decision.delegation_id) if decision.delegation_id else None,
            "mfa_satisfied": decision.mfa_satisfied,
            "effect": decision.effect,
            "decided_at": decision.decided_at.isoformat(),
        },
    }
    if detail:
        report = session.scalar(
            select(Report).where(Report.tenant_id == r.tenant_id, Report.id == r.report_id)
        )
        version = session.scalar(
            select(ReportVersion).where(
                ReportVersion.tenant_id == r.tenant_id, ReportVersion.id == r.report_version_id
            )
        )
        snap = session.scalar(
            select(EvidenceSnapshot).where(
                EvidenceSnapshot.tenant_id == r.tenant_id, EvidenceSnapshot.id == r.snapshot_id
            )
        )
        if report is not None and version is not None:
            out["report"] = version_view(session, report, version)
        if snap is not None:
            out["snapshot"] = {
                "version_no": snap.version_no,
                "completeness_status": snap.completeness_status,
                "gaps": snap.gaps,
                "open_conflicts": snap.open_conflicts,
            }
        out["evidence_drift"] = snapshot_drift(session, r.tenant_id, r.case_id, r.snapshot_id)
    return out


def get_request(session: Session, ctx: PrincipalContext, raw_id: str) -> dict[str, Any]:
    require(ctx, Permission.CASE_READ)
    request, _ = _load_request(session, ctx, raw_id, for_update=False)
    return request_view(session, request, detail=True)


def list_requests(session: Session, ctx: PrincipalContext, status: str | None) -> dict[str, Any]:
    require(ctx, Permission.CASE_READ)
    stmt = (
        select(ApprovalRequest)
        .join(Case, and_(Case.tenant_id == ApprovalRequest.tenant_id, Case.id == ApprovalRequest.case_id))
        .where(ApprovalRequest.tenant_id == ctx.tenant_id, scope_filter(ctx))
        .order_by(ApprovalRequest.created_at.desc())
        .limit(100)
    )
    if status:
        stmt = stmt.where(ApprovalRequest.status == status)
    return {"items": [request_view(session, r) for r in session.scalars(stmt)]}
