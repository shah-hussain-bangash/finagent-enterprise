"""External-operation ledger: at-least-once delivery, idempotent effect, explicit reconciliation
(prompt §20; FR-033; DIR-029; NQ-020; MST:REC-001..003).

execute_once():
  1. fenced txn: insert-or-load the ledger row by (tenant, type, key), lock it.
       CONFIRMED / RECONCILED_APPLIED -> already done, no effect.
       OUTCOME_UNCERTAIN              -> stop; only a human can move it on.
       SENT (an earlier attempt never recorded its outcome) -> PROBE the provider first:
           APPLIED -> CONFIRMED (no second effect); NOT_FOUND -> safe to send again;
           UNKNOWN -> OUTCOME_UNCERTAIN.
     mark SENT, attempts += 1, commit (the intent is durable BEFORE the effect).
  2. call the provider with the operation key as its idempotency key.
  3. fenced txn: CONFIRMED (or FAILED for a definite rejection; an ambiguous error is probed).
We never claim exactly-once execution: the provider's idempotency + the probe make the effect
happen at most once in every scenario we can observe; anything we cannot prove is UNCERTAIN.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, sessionmaker

from finagent.core.correlation import get_correlation_id
from finagent.core.errors import AppError, ErrorCode, not_found
from finagent.database.models import ExternalOperation, OutboxEvent, WorkflowRun
from finagent.database.session import transaction
from finagent.domain.states import RunStatus
from finagent.security.authorization.policy import Permission, PrincipalContext, require
from finagent.services.audit_service import writer as audit
from finagent.worker.leases import LeaseHandle, assert_current


class ProbeResult(StrEnum):
    APPLIED = "APPLIED"
    NOT_FOUND = "NOT_FOUND"
    UNKNOWN = "UNKNOWN"


class EffectRejectedError(Exception):
    """Definite, permanent rejection by the provider: the effect did NOT happen."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class OutcomeUncertainError(Exception):
    def __init__(self, operation_id: uuid.UUID) -> None:
        super().__init__(f"external operation {operation_id} outcome is uncertain")
        self.operation_id = operation_id


class ExternalEffect(Protocol):
    target_system: str

    def apply(self, operation_key: str) -> str | None: ...  # provider reference

    def probe(self, operation_key: str) -> ProbeResult: ...


@dataclass(frozen=True)
class OperationSpec:
    tenant_id: uuid.UUID
    operation_type: str
    operation_key: str
    request_hash: str
    case_id: uuid.UUID | None = None
    run_id: uuid.UUID | None = None


FaultHook = Callable[[str], None]


def _audit(session: Session, op: ExternalOperation, action: str, outcome: str, **details: Any) -> None:
    audit.record(
        session,
        tenant_id=op.tenant_id,
        action=action,
        object_type="external_operation",
        object_id=str(op.id),
        outcome=outcome,
        actor_principal_id=None,
        actor_kind="SYSTEM",
        case_id=op.case_id,
        details={"operation_type": op.operation_type, "status": op.status, **details},
    )


def _mark_uncertain(session: Session, op: ExternalOperation, code: str) -> None:
    op.status, op.last_error_code, op.updated_at = "OUTCOME_UNCERTAIN", code[:64], datetime.now(UTC)
    _audit(session, op, "EXTERNAL_OPERATION_UNCERTAIN", "UNCERTAIN", error_code=code)
    session.add(
        OutboxEvent(
            tenant_id=op.tenant_id,
            event_type="external_operation.uncertain.v1",
            schema_version="1",
            resource_id=str(op.id),
            correlation_id=get_correlation_id(),
            payload={"operation_id": str(op.id), "operation_type": op.operation_type},
        )
    )


def execute_once(
    factory: sessionmaker[Session],
    spec: OperationSpec,
    effect: ExternalEffect,
    *,
    lease: LeaseHandle | None = None,
    fault: FaultHook | None = None,
) -> ExternalOperation:
    def fence(s: Session) -> None:
        if lease is not None:
            assert_current(s, lease)

    with transaction(factory) as s:
        fence(s)
        s.execute(
            pg_insert(ExternalOperation)
            .values(
                id=uuid.uuid4(),
                tenant_id=spec.tenant_id,
                case_id=spec.case_id,
                run_id=spec.run_id,
                operation_type=spec.operation_type,
                operation_key=spec.operation_key,
                target_system=effect.target_system,
                request_hash=spec.request_hash,
                status="PENDING",
                attempts=0,
            )
            .on_conflict_do_nothing(constraint="uq_external_operation_key")
        )
        op = s.scalars(
            select(ExternalOperation)
            .where(
                ExternalOperation.tenant_id == spec.tenant_id,
                ExternalOperation.operation_type == spec.operation_type,
                ExternalOperation.operation_key == spec.operation_key,
            )
            .with_for_update()
        ).one()
        if op.request_hash != spec.request_hash:
            raise AppError(ErrorCode.IDEMPOTENCY_CONFLICT, "Operation key reused with a different request.")
        if op.status in ("CONFIRMED", "RECONCILED_APPLIED"):
            return op  # already applied: never a second effect
        if op.status == "OUTCOME_UNCERTAIN":
            raise OutcomeUncertainError(op.id)
        became_uncertain = False
        if op.status == "SENT":  # a previous attempt never recorded its outcome: ask first
            probe = effect.probe(op.operation_key)
            if probe is ProbeResult.APPLIED:
                op.status, op.updated_at = "CONFIRMED", datetime.now(UTC)
                _audit(s, op, "EXTERNAL_OPERATION_CONFIRMED", "SUCCESS", via="probe")
                return op
            if probe is ProbeResult.UNKNOWN:
                _mark_uncertain(s, op, "PROBE_UNKNOWN")  # committed before we raise below
                became_uncertain = True
        if not became_uncertain:  # PENDING, FAILED, RECONCILED_NOT_APPLIED, or SENT+NOT_FOUND
            op.status, op.attempts, op.updated_at = "SENT", op.attempts + 1, datetime.now(UTC)
            _audit(s, op, "EXTERNAL_OPERATION_SENT", "PENDING", attempt=op.attempts)
    if became_uncertain:
        raise OutcomeUncertainError(op.id)

    if fault:
        fault("before_external_apply")
    error: str | None = None
    rejected: EffectRejectedError | None = None
    reference: str | None = None
    try:
        reference = effect.apply(spec.operation_key)
    except EffectRejectedError as exc:
        rejected = exc
    except Exception as exc:  # timeout / transport error AFTER sending: outcome is ambiguous
        error = type(exc).__name__
    if fault:
        fault("after_external_apply")

    with transaction(factory) as s:
        fence(s)
        op = s.scalars(select(ExternalOperation).where(ExternalOperation.id == op.id).with_for_update()).one()
        now = datetime.now(UTC)
        if rejected is not None:
            op.status, op.last_error_code, op.updated_at = "FAILED", rejected.code[:64], now
            _audit(s, op, "EXTERNAL_OPERATION_REJECTED", "FAILED", error_code=rejected.code)
        elif error is None:
            op.status, op.provider_reference, op.updated_at = "CONFIRMED", reference, now
            _audit(s, op, "EXTERNAL_OPERATION_CONFIRMED", "SUCCESS", via="apply")
        else:
            probe = effect.probe(op.operation_key)
            if probe is ProbeResult.APPLIED:
                op.status, op.updated_at = "CONFIRMED", now
                _audit(s, op, "EXTERNAL_OPERATION_CONFIRMED", "SUCCESS", via="probe_after_error")
            else:
                # NOT_FOUND right after an ambiguous error is still not proof (the effect may land
                # late), so we never blindly resend here: a human decides (DIR-029).
                _mark_uncertain(s, op, f"AMBIGUOUS_{error}")
    if op.status == "OUTCOME_UNCERTAIN":
        raise OutcomeUncertainError(op.id)
    if op.status == "FAILED" and rejected is not None:
        raise rejected
    return op


# ---------------------------------------------------------------- manual reconciliation (REC-003)
class ReconcileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    resolution: Literal["APPLIED", "NOT_APPLIED"]
    reason: str = Field(min_length=10, max_length=500)


def _view(op: ExternalOperation) -> dict[str, Any]:
    return {
        "operation_id": str(op.id),
        "operation_type": op.operation_type,
        "target_system": op.target_system,
        "status": op.status,
        "attempts": op.attempts,
        "last_error_code": op.last_error_code,
        "run_id": str(op.run_id) if op.run_id else None,
        "case_id": str(op.case_id) if op.case_id else None,
        "updated_at": op.updated_at.isoformat(),
        "reconciled_at": op.reconciled_at.isoformat() if op.reconciled_at else None,
    }


def get_operation(session: Session, ctx: PrincipalContext, raw_id: str) -> dict[str, Any]:
    require(ctx, Permission.OPERATION_RECONCILE)
    op = _load(session, ctx, raw_id, for_update=False)
    return _view(op)


def _load(session: Session, ctx: PrincipalContext, raw_id: str, *, for_update: bool) -> ExternalOperation:
    try:
        oid = uuid.UUID(raw_id)
    except ValueError:
        raise not_found() from None
    stmt = select(ExternalOperation).where(
        ExternalOperation.tenant_id == ctx.tenant_id, ExternalOperation.id == oid
    )
    op = session.scalar(stmt.with_for_update() if for_update else stmt)
    if op is None:
        raise not_found()
    return op


def reconcile(session: Session, ctx: PrincipalContext, raw_id: str, req: ReconcileRequest) -> dict[str, Any]:
    """Operator records the verified real-world outcome (MFA step-up via OPERATION_RECONCILE)."""
    from finagent.services.case_service.transitions import Actor
    from finagent.services.workflow_service.runs import change_run_status

    require(ctx, Permission.OPERATION_RECONCILE)
    op = _load(session, ctx, raw_id, for_update=True)
    if op.status != "OUTCOME_UNCERTAIN":
        raise AppError(
            ErrorCode.OPERATION_INVALID_STATE,
            "Only operations with an uncertain outcome can be reconciled.",
            {"current_status": op.status},
        )
    now = datetime.now(UTC)
    op.status = "RECONCILED_APPLIED" if req.resolution == "APPLIED" else "RECONCILED_NOT_APPLIED"
    op.reconciled_by, op.reconciled_at, op.reconciliation_note, op.updated_at = (
        ctx.principal_id,
        now,
        req.reason,
        now,
    )
    audit.record(
        session,
        tenant_id=op.tenant_id,
        action="EXTERNAL_OPERATION_RECONCILED",
        object_type="external_operation",
        object_id=str(op.id),
        outcome="SUCCESS",
        actor_principal_id=ctx.principal_id,
        actor_kind=ctx.kind.value,
        case_id=op.case_id,
        details={"resolution": req.resolution, "reason": req.reason},
    )
    if op.run_id is not None:  # hand the run back to an authorized human, never auto-resume
        run = session.scalar(
            select(WorkflowRun)
            .where(WorkflowRun.tenant_id == op.tenant_id, WorkflowRun.id == op.run_id)
            .with_for_update()
        )
        if run is not None and run.status == RunStatus.OUTCOME_UNCERTAIN.value:
            change_run_status(
                session,
                run,
                RunStatus.HUMAN_REVIEW,
                f"operation reconciled as {req.resolution}",
                Actor.of(ctx),
            )
    session.flush()
    return _view(op)
