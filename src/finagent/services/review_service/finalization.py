"""Finalization gate (FR-029, AER-023, DATA-D06, prompt §6 steps 42-45).

Before the case can complete, everything is re-checked NOW, deterministically:
  approval still APPROVED and bound to the CURRENT report version; reviewed evidence unchanged;
  the approving reviewer still active and still holding the authority to decide; case APPROVED.
Any failure invalidates the approval (case -> REWORK) instead of finalizing stale content.

Write mode: the single permitted business write (downstream case status) runs only when the
deployment setting AND the tenant flag are on, through the tool gateway and the external-operation
ledger. Otherwise the write is explicitly NOT performed by configuration and recorded as such.
An uncertain write leaves the case FINALIZING - never a false COMPLETED (FR-033, NQ-019).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from finagent.agents.registry import CapabilityUnavailableError
from finagent.core.config import Settings
from finagent.core.errors import AppError
from finagent.database.models import (
    ApprovalDecision,
    ApprovalRequest,
    Case,
    Finalization,
    Report,
    ReportVersion,
)
from finagent.database.session import transaction
from finagent.domain.states import CaseStatus
from finagent.integrations.tool_gateway.gateway import (
    FINALIZATION_CAPABILITY,
    ExecutionContext,
    ToolDeniedError,
    ToolGateway,
)
from finagent.security.authentication.resolver import resolve_principal_by_id
from finagent.security.authorization.policy import Permission, PrincipalContext, Role
from finagent.services.admin_service.feature_flags import CASE_STATUS_WRITE, is_enabled
from finagent.services.audit_service import writer as audit
from finagent.services.case_service.queries import load_scoped_case
from finagent.services.case_service.transitions import Actor, change_status
from finagent.services.governance_service.capability_incidents import admission
from finagent.services.review_service.approvals import invalidate_for_case
from finagent.services.review_service.review import snapshot_drift
from finagent.services.workflow_service.external_ops import (
    EffectRejectedError,
    OutcomeUncertainError,
)
from finagent.worker import leases
from finagent.worker.leases import LeaseHandle

DONE = ("COMPLETED", "WRITE_NOT_PERFORMED")


class FinalizationFailedError(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _approver(s: Session, req: ApprovalRequest) -> tuple[PrincipalContext | None, ApprovalDecision | None]:
    decision = s.scalar(
        select(ApprovalDecision).where(
            ApprovalDecision.tenant_id == req.tenant_id,
            ApprovalDecision.request_id == req.id,
        )
    )
    if decision is None:
        return None, None
    try:
        ctx = resolve_principal_by_id(s, req.tenant_id, decision.reviewer_id)
    except AppError:
        return None, decision
    return ctx, decision


def revalidate(s: Session, req: ApprovalRequest, case: Case) -> tuple[list[str], PrincipalContext | None]:
    problems: list[str] = []
    if req.status != "APPROVED":
        problems.append(f"APPROVAL_STATUS_{req.status}")
    report = s.scalar(select(Report).where(Report.tenant_id == req.tenant_id, Report.id == req.report_id))
    bound = s.scalar(
        select(ReportVersion.version_no).where(
            ReportVersion.tenant_id == req.tenant_id,
            ReportVersion.id == req.report_version_id,
        )
    )
    if report is None or bound != report.current_version_no:
        problems.append("REPORT_CHANGED_AFTER_APPROVAL")
    if snapshot_drift(s, req.tenant_id, req.case_id, req.snapshot_id):
        problems.append("EVIDENCE_CHANGED_AFTER_APPROVAL")
    ctx, decision = _approver(s, req)
    if decision is None or decision.decision != "APPROVE":
        problems.append("NO_APPROVE_DECISION")
    acting = (decision.on_behalf_of or decision.reviewer_id) if decision else None
    if (
        ctx is None
        or Permission.APPROVAL_DECIDE not in ctx.permissions
        or (acting is not None and acting not in ctx.acting_for(Role.REVIEWER))
    ):
        problems.append("APPROVER_NO_LONGER_AUTHORIZED")
    elif load_scoped_case(s, ctx, str(case.id)) is None:
        problems.append("APPROVER_LOST_CASE_ACCESS")
    if CaseStatus(case.status) not in (CaseStatus.APPROVED, CaseStatus.FINALIZING):
        problems.append(f"CASE_STATUS_{case.status}")
    return problems, ctx


def finalize(
    factory: sessionmaker[Session],
    *,
    tenant_id: uuid.UUID,
    case_id: uuid.UUID,
    approval_request_id: uuid.UUID,
    run_id: uuid.UUID | None,
    gateway: ToolGateway,
    settings: Settings,
    lease: LeaseHandle | None,
    fault: Any = None,  # LOCAL/TEST fault-injection hook for recovery tests
) -> dict[str, Any]:
    try:
        with admission(factory, tenant_id, FINALIZATION_CAPABILITY):
            return _finalize_admitted(
                factory,
                tenant_id=tenant_id,
                case_id=case_id,
                approval_request_id=approval_request_id,
                run_id=run_id,
                gateway=gateway,
                settings=settings,
                lease=lease,
                fault=fault,
            )
    except CapabilityUnavailableError as exc:
        raise FinalizationFailedError(exc.reason) from exc


def _finalize_admitted(
    factory: sessionmaker[Session],
    *,
    tenant_id: uuid.UUID,
    case_id: uuid.UUID,
    approval_request_id: uuid.UUID,
    run_id: uuid.UUID | None,
    gateway: ToolGateway,
    settings: Settings,
    lease: LeaseHandle | None,
    fault: Any = None,  # LOCAL/TEST fault-injection hook for recovery tests
) -> dict[str, Any]:
    actor = Actor.system(f"finalization:{run_id}")

    def fence(s: Session) -> None:
        if lease is not None:
            leases.assert_current(s, lease)

    with transaction(factory) as s:
        fence(s)
        case = s.scalar(select(Case).where(Case.tenant_id == tenant_id, Case.id == case_id).with_for_update())
        req = s.scalar(
            select(ApprovalRequest)
            .where(
                ApprovalRequest.tenant_id == tenant_id,
                ApprovalRequest.id == approval_request_id,
            )
            .with_for_update()
        )
        if case is None or req is None:
            raise FinalizationFailedError("CASE_OR_APPROVAL_MISSING")
        fin = s.scalar(
            select(Finalization)
            .where(
                Finalization.tenant_id == tenant_id,
                Finalization.approval_request_id == req.id,
            )
            .with_for_update()
        )
        if fin is not None and fin.status in DONE:
            return {"status": fin.status, "case_status": case.status, "replayed": True}
        problems, approver = revalidate(s, req, case)
        if problems:
            if fin is not None:
                fin.status, fin.status_reason, fin.updated_at = (
                    "FAILED",
                    ",".join(problems)[:300],
                    datetime.now(UTC),
                )
            invalidate_for_case(
                s,
                tenant_id=tenant_id,
                case_id=case_id,
                reason="FINALIZATION_REVALIDATION_FAILED:" + ",".join(problems),
                actor_principal_id=None,
                actor_kind="SYSTEM",
            )
            audit.record(
                s,
                tenant_id=tenant_id,
                action="FINALIZATION_REFUSED",
                object_type="approval_request",
                object_id=str(req.id),
                outcome="DENIED",
                actor_principal_id=None,
                actor_kind="SYSTEM",
                case_id=case_id,
                details={"problems": problems},
            )
            return {
                "status": "REVALIDATION_FAILED",
                "problems": problems,
                "case_status": case.status,
            }
        write = settings.case_status_write_enabled and is_enabled(s, tenant_id, CASE_STATUS_WRITE)
        if fin is None:
            fin = Finalization(
                tenant_id=tenant_id,
                case_id=case_id,
                approval_request_id=req.id,
                status="STARTED",
                write_mode="STATUS_WRITE" if write else "WRITE_DISABLED_BY_CONFIGURATION",
                requested_by=req.reviewer_id or req.requested_by,
            )
            s.add(fin)
        else:
            fin.status, fin.updated_at = "STARTED", datetime.now(UTC)
        if case.status == CaseStatus.APPROVED.value:
            change_status(
                s,
                case,
                CaseStatus.FINALIZING,
                actor,
                "approval revalidated; finalizing",
            )
        s.flush()
        fin_id, write_mode = fin.id, fin.write_mode
    operation_id = None
    if write_mode == "STATUS_WRITE" and approver is not None:
        ctx = ExecutionContext(
            tenant_id=tenant_id,
            case_id=case_id,
            run_id=run_id,
            step_id="finalize",
            capability_id=FINALIZATION_CAPABILITY,
            principal=approver,
            allowed_tools=frozenset(),
            data_classes=frozenset(),
        )
        try:
            op = gateway.invoke_case_status_write(
                ctx,
                {
                    "status": "UNDER_REVIEW_COMPLETE",
                    "reason_code": "REVIEWED_AND_APPROVED",
                },
                f"finalize:{approval_request_id}",
                lease=lease,
                fault=fault,
            )
            operation_id = op.id
        except OutcomeUncertainError as exc:
            _mark(
                factory,
                fence,
                fin_id,
                "OUTCOME_UNCERTAIN",
                "downstream write outcome unknown",
                exc.operation_id,
            )
            raise  # the run becomes OUTCOME_UNCERTAIN; the case stays FINALIZING
        except (EffectRejectedError, ToolDeniedError) as exc:
            _mark(
                factory,
                fence,
                fin_id,
                "FAILED",
                f"downstream write not applied: {type(exc).__name__}",
                None,
            )
            raise FinalizationFailedError("STATUS_WRITE_REJECTED") from exc
    with transaction(factory) as s:
        fence(s)
        fin = s.get(Finalization, fin_id)
        case = s.scalar(select(Case).where(Case.tenant_id == tenant_id, Case.id == case_id).with_for_update())
        req = s.scalar(
            select(ApprovalRequest).where(ApprovalRequest.id == approval_request_id).with_for_update()
        )
        report = s.scalar(
            select(Report).where(Report.tenant_id == tenant_id, Report.case_id == case_id).with_for_update()
        )
        if fin is None or case is None or req is None or report is None:
            raise FinalizationFailedError("STATE_DISAPPEARED")
        if fin.status != "STARTED" or case.status != CaseStatus.FINALIZING.value:
            raise FinalizationFailedError(
                f"CONCURRENT_CHANGE_{fin.status}_{case.status}"
            )  # e.g. invalidated meanwhile
        final = "COMPLETED" if write_mode == "STATUS_WRITE" else "WRITE_NOT_PERFORMED"
        fin.status, fin.external_operation_id, fin.updated_at = (
            final,
            operation_id,
            datetime.now(UTC),
        )
        fin.status_reason = (
            "downstream case status confirmed"
            if final == "COMPLETED"
            else "downstream case-status write disabled by configuration (DATA-D06); not required"
        )
        req.status, req.status_changed_at = "CONSUMED", datetime.now(UTC)
        report.status = "FINALIZED"
        report.record_version += 1
        change_status(s, case, CaseStatus.COMPLETED, actor, fin.status_reason[:300])
        audit.record(
            s,
            tenant_id=tenant_id,
            action="CASE_FINALIZED",
            object_type="finalization",
            object_id=str(fin.id),
            outcome="SUCCESS",
            actor_principal_id=None,
            actor_kind="SYSTEM",
            case_id=case_id,
            details={
                "write_mode": write_mode,
                "external_operation_id": str(operation_id) if operation_id else None,
                "approval_request_id": str(approval_request_id),
            },
        )
        return {
            "status": final,
            "case_status": case.status,
            "external_operation_id": str(operation_id) if operation_id else None,
        }


def _mark(
    factory: sessionmaker[Session],
    fence: Any,
    fin_id: uuid.UUID,
    status: str,
    reason: str,
    op_id: uuid.UUID | None,
) -> None:
    with transaction(factory) as s:
        fence(s)
        fin = s.get(Finalization, fin_id)
        if fin is not None:
            fin.status, fin.status_reason, fin.updated_at = (
                status,
                reason,
                datetime.now(UTC),
            )
            fin.external_operation_id = op_id or fin.external_operation_id
