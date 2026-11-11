"""Workflow runs: start, read, cancel, resume (FR-005, FR-031, FR-032, FR-035; prompt §22).

The API only records intent: a start commits the run (QUEUED) + case transition + audit + outbox
`workflow.started.v1` in one transaction and answers 202. Execution belongs to the worker. Run
status (technical) and case status (business) are separate (ADR-0003).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import ClassVar, Literal, cast

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from finagent.core.correlation import get_correlation_id
from finagent.core.errors import AppError, ErrorCode, not_found
from finagent.database.models import (
    Case,
    ExternalOperation,
    OutboxEvent,
    WorkflowRun,
    WorkflowRunHistory,
    WorkflowStep,
)
from finagent.domain.fingerprint import fingerprint
from finagent.domain.states import (
    RUN_TERMINAL,
    CaseStatus,
    InvalidTransitionError,
    RunStatus,
    run_transition,
)
from finagent.orchestration.catalog import graph_version_for, spec_for
from finagent.security.authorization.policy import Permission, PrincipalContext, require
from finagent.services import idempotency
from finagent.services.audit_service import writer as audit
from finagent.services.case_service.queries import load_scoped_case, scope_filter
from finagent.services.case_service.transitions import Actor, change_status

START_OPERATION = "workflows.start"
STARTABLE_CASE_STATUSES = frozenset({CaseStatus.ASSIGNED, CaseStatus.INVESTIGATING, CaseStatus.REWORK})
RESUMABLE = frozenset({RunStatus.WAITING_REVIEW, RunStatus.WAITING_EVIDENCE, RunStatus.HUMAN_REVIEW})
_EVENTS = {
    RunStatus.WAITING_REVIEW: "workflow.awaiting_review.v1",
    RunStatus.SUCCEEDED: "workflow.completed.v1",
    RunStatus.FAILED: "workflow.failed.v1",
    RunStatus.CANCELLED: "workflow.cancelled.v1",
}
_NEXT_ACTION = {
    RunStatus.QUEUED: "Waiting for a worker.",
    RunStatus.RUNNING: "Running; no action needed.",
    RunStatus.RETRYING: "Retrying a transient failure automatically.",
    RunStatus.WAITING_REVIEW: "A reviewer decision is required.",
    RunStatus.WAITING_EVIDENCE: "The analyst must supply or resolve missing evidence.",
    RunStatus.HUMAN_REVIEW: ("An authorized person must review the recovery reason before resuming."),
    RunStatus.OUTCOME_UNCERTAIN: "An operator must reconcile the external operation first.",
}


class StartRequest(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    case_id: uuid.UUID
    workflow_type: Literal["aml_investigation", "durability_probe"] = "aml_investigation"


class CancelRequest(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    reason: str = Field(min_length=3, max_length=300)


def change_run_status(
    session: Session,
    run: WorkflowRun,
    target: RunStatus,
    reason: str,
    actor: Actor,
    *,
    event: str | None = None,
) -> None:
    """Legal run transition + history + audit + outbox (same transaction as the caller)."""
    current = RunStatus(run.status)
    try:
        _ = run_transition(current, target)
    except InvalidTransitionError as exc:
        raise AppError(
            ErrorCode.RUN_INVALID_STATE,
            "This action is not allowed in the run's current status.",
            {"current_status": current.value},
        ) from exc

    now = datetime.now(UTC)
    run.status, run.status_reason = target.value, reason[:300]
    run.updated_at, run.record_version = now, run.record_version + 1

    if target is RunStatus.RUNNING and run.started_at is None:
        run.started_at = now
    if target in RUN_TERMINAL:
        run.finished_at = now

    session.add(
        WorkflowRunHistory(
            tenant_id=run.tenant_id,
            run_id=run.id,
            from_status=current.value,
            to_status=target.value,
            reason=reason[:300],
            actor=actor.label,
        )
    )
    _ = audit.record(
        session,
        tenant_id=run.tenant_id,
        action="WORKFLOW_STATUS_CHANGED",
        object_type="workflow_run",
        object_id=str(run.id),
        outcome="SUCCESS",
        actor_principal_id=actor.principal_id,
        actor_kind=actor.kind,
        case_id=run.case_id,
        details={
            "from": current.value,
            "to": target.value,
            "reason": reason[:300],
        },
    )

    event_type = event or _EVENTS.get(target)
    if event_type:
        session.add(
            OutboxEvent(
                tenant_id=run.tenant_id,
                event_type=event_type,
                schema_version="1",
                resource_id=str(run.id),
                correlation_id=run.correlation_id,
                payload={
                    "run_id": str(run.id),
                    "case_id": str(run.case_id),
                    "status": target.value,
                },
            )
        )


def start_run(
    session: Session,
    ctx: PrincipalContext,
    body: StartRequest,
    key: str,
    retention_days: int,
    max_attempts: int,
    allow_local_only: bool = False,
) -> tuple[int, dict[str, str]]:
    require(ctx, Permission.WORKFLOW_START)
    if spec_for(body.workflow_type).local_only and not allow_local_only:
        raise AppError(
            ErrorCode.FEATURE_DISABLED,
            "This workflow type is available in local/test environments only.",
        )

    idem = idempotency.begin(
        session,
        ctx.tenant_id,
        START_OPERATION,
        key,
        fingerprint(body.model_dump(mode="json")),
        retention_days,
    )
    if idem.replay and idem.record.status_code is not None and idem.record.response is not None:
        return (
            idem.record.status_code,
            cast(dict[str, str], idem.record.response),
        )

    case = load_scoped_case(session, ctx, str(body.case_id), for_update=True)
    if case is None:
        raise not_found()
    if CaseStatus(case.status) not in STARTABLE_CASE_STATUSES:
        raise AppError(
            ErrorCode.CASE_INVALID_STATE,
            "An investigation can only start on an assigned, investigating or rework case.",
            {"current_status": case.status},
        )

    active = session.scalar(
        select(WorkflowRun.id).where(
            WorkflowRun.tenant_id == ctx.tenant_id,
            WorkflowRun.case_id == case.id,
            WorkflowRun.status.not_in([s.value for s in RUN_TERMINAL]),
        )
    )
    if active is not None:
        raise AppError(
            ErrorCode.RUN_INVALID_STATE,
            "This case already has an active run.",
            {"active_run_id": str(active)},
        )

    actor = Actor.of(ctx)
    run = WorkflowRun(
        tenant_id=ctx.tenant_id,
        case_id=case.id,
        workflow_type=body.workflow_type,
        graph_version=graph_version_for(body.workflow_type),
        status=RunStatus.CREATED.value,
        status_reason="requested",
        requested_by=ctx.principal_id,
        max_attempts=max_attempts,
        correlation_id=get_correlation_id(),
    )
    session.add(run)
    session.flush()
    session.add(
        WorkflowRunHistory(
            tenant_id=run.tenant_id,
            run_id=run.id,
            from_status=None,
            to_status="CREATED",
            reason="requested",
            actor=actor.label,
        )
    )
    change_run_status(
        session,
        run,
        RunStatus.QUEUED,
        "queued for execution",
        actor,
        event="workflow.started.v1",
    )
    if case.status == CaseStatus.ASSIGNED.value:
        change_status(
            session,
            case,
            CaseStatus.INVESTIGATING,
            actor,
            "investigation run started",
        )

    session.flush()
    body_out: dict[str, str] = {
        "run_id": str(run.id),
        "case_id": str(case.id),
        "status": run.status,
        "status_url": f"/api/v1/workflows/{run.id}",
    }
    idem.finish(202, body_out, str(run.id))
    return 202, body_out


def _load_visible_run(
    session: Session,
    ctx: PrincipalContext,
    raw_run_id: str,
    *,
    for_update: bool = False,
) -> WorkflowRun | None:
    try:
        run_id = uuid.UUID(raw_run_id)
    except ValueError:
        return None

    stmt = select(WorkflowRun).where(
        WorkflowRun.tenant_id == ctx.tenant_id,
        WorkflowRun.id == run_id,
    )
    if Permission.CASE_READ in ctx.permissions:
        stmt = stmt.join(
            Case,
            and_(
                Case.tenant_id == WorkflowRun.tenant_id,
                Case.id == WorkflowRun.case_id,
            ),
        ).where(scope_filter(ctx))
    elif Permission.PLATFORM_OPERATE not in ctx.permissions:
        return None  # Operators see run metadata for recovery, without case content.

    if for_update:
        stmt = stmt.with_for_update(of=WorkflowRun)
    return session.scalar(stmt)


def run_view(session: Session, run: WorkflowRun) -> dict[str, object]:
    steps = session.scalars(
        select(WorkflowStep)
        .where(
            WorkflowStep.tenant_id == run.tenant_id,
            WorkflowStep.run_id == run.id,
        )
        .order_by(WorkflowStep.started_at, WorkflowStep.step_key)
    ).all()
    history = session.scalars(
        select(WorkflowRunHistory)
        .where(
            WorkflowRunHistory.tenant_id == run.tenant_id,
            WorkflowRunHistory.run_id == run.id,
        )
        .order_by(WorkflowRunHistory.seq)
    ).all()
    uncertain = session.scalars(
        select(ExternalOperation.id).where(
            ExternalOperation.tenant_id == run.tenant_id,
            ExternalOperation.run_id == run.id,
            ExternalOperation.status == "OUTCOME_UNCERTAIN",
        )
    ).all()

    status = RunStatus(run.status)
    blockers: list[dict[str, object]] = []
    if status in (
        RunStatus.HUMAN_REVIEW,
        RunStatus.WAITING_EVIDENCE,
        RunStatus.FAILED,
    ):
        blockers.append(
            {
                "code": run.last_error_code or "SEE_STATUS_REASON",
                "reason": run.status_reason,
            }
        )

    for op in uncertain:
        blockers.append(
            {
                "code": "OUTCOME_UNCERTAIN",
                "operation_id": str(op),
            }
        )

    return {
        "run_id": str(run.id),
        "case_id": str(run.case_id),
        "workflow_type": run.workflow_type,
        "graph_version": run.graph_version,
        "status": run.status,
        "status_reason": run.status_reason,
        "attempt": run.attempt,
        "max_attempts": run.max_attempts,
        "cancel_requested": run.cancel_requested_at is not None,
        "created_at": run.created_at.isoformat(),
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "next_action": _NEXT_ACTION.get(status),
        "blockers": blockers,
        "steps": [
            {
                "node": s.node,
                "status": s.status,
                "attempt": s.attempt,
                "error_code": s.error_code,
                "started_at": s.started_at.isoformat(),
                "finished_at": s.finished_at.isoformat() if s.finished_at else None,
            }
            for s in steps
        ],
        "history": [
            {
                "from": h.from_status,
                "to": h.to_status,
                "reason": h.reason,
                "at": h.occurred_at.isoformat(),
            }
            for h in history
        ],
    }


def get_run(
    session: Session,
    ctx: PrincipalContext,
    raw_run_id: str,
) -> dict[str, object]:
    if not ({Permission.CASE_READ, Permission.PLATFORM_OPERATE} & ctx.permissions):
        raise AppError(ErrorCode.FORBIDDEN)

    run = _load_visible_run(session, ctx, raw_run_id)
    if run is None:
        raise not_found()
    return run_view(session, run)


def cancel_run(
    session: Session,
    ctx: PrincipalContext,
    raw_run_id: str,
    body: CancelRequest,
) -> dict[str, object]:
    """FR-035: stop scheduling new work; committed effects are preserved."""
    require(ctx, Permission.WORKFLOW_CANCEL)
    run = _load_visible_run(session, ctx, raw_run_id, for_update=True)
    if run is None:
        raise not_found()

    status = RunStatus(run.status)
    if status is RunStatus.OUTCOME_UNCERTAIN:
        raise AppError(
            ErrorCode.RUN_INVALID_STATE,
            "Reconcile the uncertain external operation before cancelling this run.",
        )
    if status in RUN_TERMINAL:
        raise AppError(
            ErrorCode.RUN_INVALID_STATE,
            "The run has already finished.",
            {"current_status": run.status},
        )

    actor = Actor.of(ctx)
    run.cancel_requested_at, run.cancel_requested_by = (
        datetime.now(UTC),
        ctx.principal_id,
    )
    if status is RunStatus.RUNNING:
        # The worker stops at its next fenced step boundary.
        # In-flight output is discarded.
        _ = audit.record(
            session,
            tenant_id=run.tenant_id,
            action="WORKFLOW_CANCEL_REQUESTED",
            object_type="workflow_run",
            object_id=str(run.id),
            outcome="SUCCESS",
            actor_principal_id=ctx.principal_id,
            actor_kind=ctx.kind.value,
            case_id=run.case_id,
            details={"reason": body.reason},
        )
    else:
        change_run_status(
            session,
            run,
            RunStatus.CANCELLED,
            f"cancelled: {body.reason}",
            actor,
        )

    session.flush()
    return run_view(session, run)


class ResumeRequest(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    action: Literal["EVIDENCE_SUPPLIED", "REANALYZE", "RETRY"]
    reason: str = Field(min_length=5, max_length=300)


def human_resume(
    session: Session,
    ctx: PrincipalContext,
    raw_run_id: str,
    body: ResumeRequest,
) -> dict[str, object]:
    """Deterministic human resume (FR-031/032).

    EVIDENCE_SUPPLIED: analyst resolved a blocking gap.
    REANALYZE: analyst requests fresh evidence and analysis while no review is open.
    RETRY: an authorized person retries a run held for HUMAN_REVIEW after checking why.
    """
    from finagent.database.models import ApprovalRequest

    run = _load_visible_run(session, ctx, raw_run_id, for_update=True)
    if run is None:
        raise not_found()

    status = RunStatus(run.status)
    actor = Actor.of(ctx)

    if body.action == "RETRY":
        if not ({Permission.OPERATION_RECONCILE, Permission.WORKFLOW_START} & ctx.permissions):
            raise AppError(ErrorCode.FORBIDDEN)

        require(
            ctx,
            Permission.OPERATION_RECONCILE
            if Permission.OPERATION_RECONCILE in ctx.permissions
            else Permission.WORKFLOW_START,
        )
        if status is not RunStatus.HUMAN_REVIEW:
            raise AppError(
                ErrorCode.RUN_INVALID_STATE,
                "Only a run held for human review can be retried.",
                {"current_status": run.status},
            )

        _ = resume_run(
            session,
            ctx.tenant_id,
            run.id,
            {"decision": "RETRY"},
            actor,
            f"retry: {body.reason}",
        )
        return run_view(session, run)

    require(ctx, Permission.WORKFLOW_START)
    case = load_scoped_case(session, ctx, str(run.case_id), for_update=True)
    if case is None:
        raise not_found()

    if body.action == "EVIDENCE_SUPPLIED":
        if status is not RunStatus.WAITING_EVIDENCE:
            raise AppError(
                ErrorCode.RUN_INVALID_STATE,
                "The run is not waiting for evidence.",
                {"current_status": run.status},
            )
        if case.status == CaseStatus.BLOCKED.value:
            change_status(
                session,
                case,
                CaseStatus.INVESTIGATING,
                actor,
                f"evidence supplied: {body.reason}",
            )
    else:
        if status is not RunStatus.WAITING_REVIEW:
            raise AppError(
                ErrorCode.RUN_INVALID_STATE,
                "Re-analysis is possible only while the run awaits review.",
                {"current_status": run.status},
            )

        open_review = session.scalar(
            select(ApprovalRequest.id).where(
                ApprovalRequest.tenant_id == ctx.tenant_id,
                ApprovalRequest.case_id == case.id,
                ApprovalRequest.status.in_(("PENDING", "APPROVED")),
            )
        )
        if open_review is not None or case.status not in (
            CaseStatus.INVESTIGATING.value,
            CaseStatus.REWORK.value,
        ):
            raise AppError(
                ErrorCode.CASE_INVALID_STATE,
                "Withdraw the pending review before re-analysing.",
                {"current_status": case.status},
            )

    _ = resume_run(
        session,
        ctx.tenant_id,
        run.id,
        {"decision": body.action},
        actor,
        f"{body.action.lower()}: {body.reason}",
    )
    return run_view(session, run)


def resume_run(
    session: Session,
    tenant_id: uuid.UUID,
    run_id: uuid.UUID,
    payload: dict[str, object],
    actor: Actor,
    reason: str,
) -> WorkflowRun:
    """Deterministic resume by approval/evidence services, never model output.

    Reject a resume arriving after cancellation or completion.
    """
    run = session.scalar(
        select(WorkflowRun)
        .where(
            WorkflowRun.tenant_id == tenant_id,
            WorkflowRun.id == run_id,
        )
        .with_for_update()
    )
    if run is None:
        raise not_found()

    if RunStatus(run.status) not in RESUMABLE or run.cancel_requested_at is not None:
        raise AppError(
            ErrorCode.RUN_INVALID_STATE,
            "The run is not waiting for input (it may have been cancelled or finished).",
            {"current_status": run.status},
        )

    run.resume_payload = payload
    change_run_status(
        session,
        run,
        RunStatus.QUEUED,
        reason,
        actor,
        event="workflow.resume_requested.v1",
    )
    session.flush()
    return run
