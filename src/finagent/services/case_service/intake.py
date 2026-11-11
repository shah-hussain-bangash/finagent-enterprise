"""Alert intake -> case -> assignment -> audit -> outbox, in ONE transaction.

Workflow steps 1-11 (prompt §6). Requirements: FR-007, FR-008, FR-009, FR-032, DIR-010, DIR-022,
DIR-025..027, MST:ARCH-003, MST:DB-001/003. Prompt §52 demonstration 1.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from finagent.core.correlation import get_correlation_id
from finagent.core.errors import AppError, ErrorCode, error_body
from finagent.database.models import (
    Alert,
    Case,
    CaseAssignment,
    CaseStatusHistory,
    IdempotencyRecord,
    IntakeReconciliation,
    OutboxEvent,
    Principal,
    RoleAssignment,
)
from finagent.domain.fingerprint import fingerprint
from finagent.domain.states import CaseStatus, case_transition
from finagent.security.authorization.policy import Permission, PrincipalContext, PrincipalKind, Role, require
from finagent.services.audit_service import writer as audit
from finagent.services.case_service.contracts import AlertEnvelope

OPERATION = "alerts.ingest"
DEFAULT_QUEUE = "aml-default"
_PRIORITY = {"CRITICAL": "URGENT", "HIGH": "HIGH", "MEDIUM": "NORMAL", "LOW": "LOW", None: "NORMAL"}


@dataclass(frozen=True)
class IntakeResult:
    status_code: int
    body: dict[str, Any]


def ingest_alert(
    session: Session,
    ctx: PrincipalContext,
    envelope: AlertEnvelope,
    idempotency_key: str,
    retention_days: int = 30,
) -> IntakeResult:
    require(ctx, Permission.ALERT_INGEST)
    if ctx.kind is not PrincipalKind.INTEGRATION or envelope.source_system != ctx.source_system:
        raise AppError(ErrorCode.FORBIDDEN)  # a connector may only speak for its own source
    if envelope.payload.alert_type not in ctx.permitted_alert_types:
        raise AppError(
            ErrorCode.SEMANTIC_VALIDATION_FAILED,
            "Alert type is not enabled for this source.",
            {"field": "payload.alert_type"},
        )

    tenant_id = ctx.tenant_id
    request_hash = fingerprint(envelope.model_dump(mode="json"))
    now = datetime.now(UTC)

    # 1. Idempotency (FR-032, prompt §41). Concurrent duplicates block on the unique index.
    inserted = session.execute(
        pg_insert(IdempotencyRecord)
        .values(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            operation=OPERATION,
            key=idempotency_key,
            request_hash=request_hash,
            expires_at=now + timedelta(days=retention_days),
        )
        .on_conflict_do_nothing(constraint="uq_idempotency")
        .returning(IdempotencyRecord.id)
    ).scalar_one_or_none()
    record = session.scalar(
        select(IdempotencyRecord).where(
            IdempotencyRecord.tenant_id == tenant_id,
            IdempotencyRecord.operation == OPERATION,
            IdempotencyRecord.key == idempotency_key,
        )
    )
    if record is None:  # unreachable unless the row vanished mid-transaction
        raise AppError(ErrorCode.INTERNAL_ERROR)
    if inserted is None:
        if record.request_hash != request_hash:
            raise AppError(
                ErrorCode.IDEMPOTENCY_CONFLICT, "Idempotency-Key was reused with a different request."
            )
        if record.response is not None and record.status_code is not None:
            return IntakeResult(record.status_code, record.response)

    # 2. Business-key dedup: (tenant, source_system, source_alert_id) (FR-008, DIR-026).
    payload = envelope.payload
    payload_fp = fingerprint(payload.model_dump(mode="json"))
    alert_id = session.execute(
        pg_insert(Alert)
        .values(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            source_system=envelope.source_system,
            source_alert_id=payload.source_alert_id,
            delivery_event_id=envelope.event_id,
            schema_version=envelope.schema_version,
            alert_type=payload.alert_type,
            severity=payload.severity,
            rule_ref=payload.rule_ref,
            occurred_at=payload.occurred_at,
            source_event_time=envelope.source_event_time,
            subject_refs=list(payload.subject_refs),
            window_start=payload.window_start,
            window_end=payload.window_end,
            payload_fingerprint=payload_fp,
        )
        .on_conflict_do_nothing(constraint="uq_alerts_source_key")
        .returning(Alert.id)
    ).scalar_one_or_none()

    if alert_id is None:
        result = _handle_duplicate(session, ctx, envelope, payload_fp)
    else:
        result = _create_case(session, ctx, alert_id, envelope, now)

    record.status_code, record.response = result.status_code, result.body
    record.resource_id = result.body.get("case_id")
    return result


def _handle_duplicate(
    session: Session, ctx: PrincipalContext, envelope: AlertEnvelope, payload_fp: str
) -> IntakeResult:
    existing = session.execute(
        select(Alert, Case)
        .join(Case, and_(Case.tenant_id == Alert.tenant_id, Case.alert_id == Alert.id))
        .where(
            Alert.tenant_id == ctx.tenant_id,
            Alert.source_system == envelope.source_system,
            Alert.source_alert_id == envelope.payload.source_alert_id,
        )
    ).one()
    alert, case = existing
    if alert.payload_fingerprint == payload_fp:
        audit.record(
            session,
            tenant_id=ctx.tenant_id,
            action="ALERT_REPLAY",
            object_type="alert",
            object_id=str(alert.id),
            outcome="DEDUPLICATED",
            actor_principal_id=ctx.principal_id,
            actor_kind=ctx.kind.value,
            case_id=case.id,
            details={"delivery_event_id": envelope.event_id},
        )
        return IntakeResult(200, _accepted(case, alert, replayed=True))

    # Materially changed duplicate: never replace evidence or create a 2nd case - quarantine (DIR-025).
    recon = IntakeReconciliation(
        tenant_id=ctx.tenant_id,
        source_system=envelope.source_system,
        source_alert_id=envelope.payload.source_alert_id,
        reason_code="CHANGED_PAYLOAD_REPLAY",
        existing_alert_id=alert.id,
        payload_fingerprint=payload_fp,
        correlation_id=get_correlation_id(),
    )
    session.add(recon)
    session.flush()
    audit.record(
        session,
        tenant_id=ctx.tenant_id,
        action="ALERT_CONFLICT_QUARANTINED",
        object_type="intake_reconciliation",
        object_id=str(recon.id),
        outcome="QUARANTINED",
        actor_principal_id=ctx.principal_id,
        actor_kind=ctx.kind.value,
        case_id=case.id,
    )
    body = error_body(
        ErrorCode.ALERT_PAYLOAD_CONFLICT,
        "An alert with this reference already exists with different content; "
        "it has been routed for reconciliation.",
        get_correlation_id(),
        {"reconciliation_id": str(recon.id)},
    )
    return IntakeResult(409, body)


def _create_case(
    session: Session, ctx: PrincipalContext, alert_id: uuid.UUID, envelope: AlertEnvelope, now: datetime
) -> IntakeResult:
    actor = f"integration:{ctx.principal_id}"
    corr = get_correlation_id()
    case = Case(
        tenant_id=ctx.tenant_id,
        alert_id=alert_id,
        status=CaseStatus.RECEIVED.value,
        priority=_PRIORITY[envelope.payload.severity],
        queue=DEFAULT_QUEUE,
    )
    session.add(case)
    session.flush()
    session.add(
        CaseStatusHistory(
            tenant_id=ctx.tenant_id,
            case_id=case.id,
            from_status=None,
            to_status=CaseStatus.RECEIVED.value,
            actor=actor,
            reason="validated alert intake",
            correlation_id=corr,
        )
    )
    audit.record(
        session,
        tenant_id=ctx.tenant_id,
        action="ALERT_RECEIVED",
        object_type="alert",
        object_id=str(alert_id),
        outcome="ACCEPTED",
        actor_principal_id=ctx.principal_id,
        actor_kind=ctx.kind.value,
        case_id=case.id,
        details={
            "source_system": envelope.source_system,
            "alert_type": envelope.payload.alert_type,
            "delivery_event_id": envelope.event_id,
        },
    )
    audit.record(
        session,
        tenant_id=ctx.tenant_id,
        action="CASE_CREATED",
        object_type="case",
        object_id=str(case.id),
        outcome="SUCCESS",
        actor_principal_id=ctx.principal_id,
        actor_kind=ctx.kind.value,
        case_id=case.id,
        details={"queue": DEFAULT_QUEUE},
    )

    analyst_id = _least_loaded_analyst(session, ctx.tenant_id)
    if analyst_id is not None:  # DEC-13 local default; otherwise remains RECEIVED in queue
        case.status = case_transition(CaseStatus.RECEIVED, CaseStatus.ASSIGNED).value
        case.record_version += 1
        session.add(
            CaseAssignment(
                tenant_id=ctx.tenant_id,
                case_id=case.id,
                principal_id=analyst_id,
                assignment_role="ANALYST",
                reason="auto: least-loaded analyst (DEC-13)",
                assigned_by=actor,
            )
        )
        session.add(
            CaseStatusHistory(
                tenant_id=ctx.tenant_id,
                case_id=case.id,
                from_status=CaseStatus.RECEIVED.value,
                to_status=case.status,
                actor=actor,
                reason="queue assignment",
                correlation_id=corr,
            )
        )
        audit.record(
            session,
            tenant_id=ctx.tenant_id,
            action="CASE_ASSIGNED",
            object_type="case",
            object_id=str(case.id),
            outcome="SUCCESS",
            actor_principal_id=ctx.principal_id,
            actor_kind=ctx.kind.value,
            case_id=case.id,
            details={"assignee_principal_id": str(analyst_id), "role": "ANALYST"},
        )

    session.add(
        OutboxEvent(
            tenant_id=ctx.tenant_id,
            event_type="case.created.v1",
            schema_version="1",
            resource_id=str(case.id),
            correlation_id=corr,
            payload={
                "case_id": str(case.id),
                "alert_id": str(alert_id),
                "status": case.status,
                "alert_type": envelope.payload.alert_type,
            },
        )
    )
    session.flush()
    alert = session.get(Alert, alert_id)
    if alert is None:
        raise AppError(ErrorCode.INTERNAL_ERROR)
    return IntakeResult(201, _accepted(case, alert, replayed=False))


def _least_loaded_analyst(session: Session, tenant_id: uuid.UUID) -> uuid.UUID | None:
    now = datetime.now(UTC)
    load = (
        select(func.count(CaseAssignment.id))
        .where(
            CaseAssignment.tenant_id == tenant_id,
            CaseAssignment.principal_id == Principal.id,
            CaseAssignment.active.is_(True),
        )
        .correlate(Principal)
        .scalar_subquery()
    )
    return session.scalar(
        select(Principal.id)
        .join(
            RoleAssignment,
            and_(
                RoleAssignment.tenant_id == Principal.tenant_id, RoleAssignment.principal_id == Principal.id
            ),
        )
        .where(
            Principal.tenant_id == tenant_id,
            Principal.status == "ACTIVE",
            Principal.kind == "USER",
            RoleAssignment.role == Role.ANALYST.value,
            RoleAssignment.valid_from <= now,
            (RoleAssignment.valid_to.is_(None)) | (RoleAssignment.valid_to > now),
        )
        .order_by(load.asc(), Principal.id.asc())
        .limit(1)
    )


def _accepted(case: Case, alert: Alert, replayed: bool) -> dict[str, Any]:
    return {
        "case_id": str(case.id),
        "alert_id": str(alert.id),
        "status": case.status,
        "replayed": replayed,
        "received_at": alert.received_at.isoformat(),
    }
