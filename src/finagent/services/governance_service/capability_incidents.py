"""Critical incident response (AER-037): declaration immediately creates a durable stop.

A trusted, MFA authenticated operator classifies the incident. A model cannot classify,
restore or bypass this stop. Blocked adversarial evaluation output is not itself proof
that a control failed, so ordinary validator rejection does not open an incident.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationInfo,
    field_validator,
)
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from finagent.agents.registry import CAPABILITIES, CapabilityUnavailableError
from finagent.core.errors import AppError, ErrorCode, not_found
from finagent.database.models import (
    AgentOutput,
    CapabilityIncident,
    CapabilityIncidentEvent,
    ModelRequest,
    ToolInvocation,
)
from finagent.database.session import transaction
from finagent.domain.fingerprint import fingerprint
from finagent.integrations.notifications.port import (
    Notification,
    NotificationError,
    NotificationPort,
)
from finagent.security.authorization.policy import (
    Permission,
    PrincipalContext,
    Role,
    require,
)
from finagent.services.audit_service import writer as audit

SERVICE_CAPABILITIES = frozenset({"SVC-EVIDENCE", "SVC-FINALIZATION"})
FINALIZATION_DEPENDENCIES = frozenset(
    {"AG-KYC", "AG-AML", "AG-COMPLIANCE", "AG-REPORT", "CAP-DOCUMENT", "SVC-EVIDENCE"}
)


EvidenceReference = Annotated[
    str,
    StringConstraints(
        pattern=r"^(agent_output|model_request|tool_invocation):[0-9a-fA-F-]{36}$",
        max_length=64,
    ),
]


class IncidentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    capability_id: str = Field(min_length=1, max_length=64)
    owner_role: Literal["AML_SUPERVISOR", "TENANT_ADMIN", "PLATFORM_OPERATOR"]
    reason: str = Field(min_length=10, max_length=500)
    evidence_refs: list[EvidenceReference] = Field(min_length=1, max_length=20)

    @field_validator("reason")
    @classmethod
    def meaningful_reason(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 10:
            raise ValueError("Provide a substantive incident reason.")
        return value


class InvestigationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    expected_version: int = Field(ge=1)
    findings: str = Field(min_length=10, max_length=1000)
    remediation_ref: str = Field(min_length=3, max_length=200)
    verification_ref: str = Field(min_length=3, max_length=200)

    @field_validator("findings", "remediation_ref", "verification_ref")
    @classmethod
    def nonblank(cls, value: str, info: ValidationInfo) -> str:
        value = value.strip()
        if len(value) < (10 if info.field_name == "findings" else 3):
            raise ValueError("Investigation and verification references cannot be blank.")
        return value


class RestoreRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    expected_version: int = Field(ge=1)
    reason: str = Field(min_length=10, max_length=500)

    @field_validator("reason")
    @classmethod
    def nonblank(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 10:
            raise ValueError("Provide a substantive restoration reason.")
        return value


def _lock(session: Session, tenant_id: uuid.UUID, *, shared: bool) -> None:
    # Shared admission spans the external call. Declaration takes the exclusive lock:
    # admitted calls drain first; no new affected call can pass after declaration commits.
    fn = "pg_advisory_xact_lock_shared" if shared else "pg_advisory_xact_lock"
    session.execute(
        text(f"SELECT {fn}(hashtextextended(:key, 0))"),
        {"key": f"capability-incidents:{tenant_id}"},
    )


def is_blocked(session: Session, tenant_id: uuid.UUID, capability_id: str) -> bool:
    targets = {capability_id}
    if capability_id == "SVC-FINALIZATION":
        targets.update(FINALIZATION_DEPENDENCIES)
    return (
        session.scalar(
            select(CapabilityIncident.id)
            .where(
                CapabilityIncident.tenant_id == tenant_id,
                CapabilityIncident.capability_id.in_(targets),
                CapabilityIncident.status != "RESTORED",
            )
            .limit(1)
        )
        is not None
    )


@contextmanager
def admission(factory: sessionmaker[Session], tenant_id: uuid.UUID, capability_id: str) -> Iterator[None]:
    with transaction(factory) as session:
        _lock(session, tenant_id, shared=True)
        if is_blocked(session, tenant_id, capability_id):
            raise CapabilityUnavailableError(capability_id, "CRITICAL_INCIDENT_ACTIVE")
        yield


def _authorize(ctx: PrincipalContext, *, restore: bool = False) -> None:
    if restore:
        require(ctx, Permission.TENANT_ADMIN)
        if Role.TENANT_ADMIN not in ctx.direct_roles:
            raise AppError(ErrorCode.FORBIDDEN)
    elif not ({Role.TENANT_ADMIN, Role.PLATFORM_OPERATOR} & ctx.direct_roles):
        raise AppError(ErrorCode.FORBIDDEN)
    if not ctx.mfa_satisfied:
        raise AppError(ErrorCode.MFA_REQUIRED)


def _invalid(message: str) -> AppError:
    return AppError(ErrorCode.SEMANTIC_VALIDATION_FAILED, message)


def _event(
    session: Session,
    incident: CapabilityIncident,
    kind: str,
    actor: uuid.UUID | None,
    details: dict[str, Any],
) -> None:
    session.add(
        CapabilityIncidentEvent(
            tenant_id=incident.tenant_id,
            incident_id=incident.id,
            kind=kind,
            actor_id=actor,
            details=details,
        )
    )
    audit.record(
        session,
        tenant_id=incident.tenant_id,
        action=f"CAPABILITY_INCIDENT_{kind}",
        object_type="capability_incident",
        object_id=str(incident.id),
        outcome="SUCCESS",
        actor_principal_id=actor,
        actor_kind="USER" if actor else "SYSTEM",
        details={
            "capability_id": incident.capability_id,
            "record_version": incident.record_version,
        },
    )


def view(row: CapabilityIncident) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "capability_id": row.capability_id,
        "status": row.status,
        "record_version": row.record_version,
        "owner_role": row.owner_role,
        "notification_status": row.notification_status,
        "notification_attempts": row.notification_attempts,
        "last_notification_error": row.last_notification_error,
    }


def list_incidents(session: Session, ctx: PrincipalContext) -> dict[str, Any]:
    _authorize(ctx)
    rows = session.scalars(
        select(CapabilityIncident)
        .where(CapabilityIncident.tenant_id == ctx.tenant_id)
        .order_by(CapabilityIncident.created_at.desc(), CapabilityIncident.id)
        .limit(100)
    ).all()
    return {"items": [view(row) for row in rows], "limit": 100}


def declare(session: Session, ctx: PrincipalContext, body: IncidentRequest) -> dict[str, Any]:
    _authorize(ctx)
    if body.capability_id not in CAPABILITIES and body.capability_id not in SERVICE_CAPABILITIES:
        raise _invalid("Unknown capability identifier.")
    _lock(session, ctx.tenant_id, shared=False)
    current = session.scalar(
        select(CapabilityIncident).where(
            CapabilityIncident.tenant_id == ctx.tenant_id,
            CapabilityIncident.capability_id == body.capability_id,
            CapabilityIncident.status != "RESTORED",
        )
    )
    if current is not None:
        raise _invalid("An active incident already blocks this capability.")
    refs = []
    evidence_metadata = []
    for raw in body.evidence_refs:
        try:
            kind, identifier = raw.split(":", 1)
            ref_id = uuid.UUID(identifier)
        except ValueError as exc:
            raise _invalid("Use agent_output, model_request or tool_invocation UUID references.") from exc
        row: AgentOutput | ModelRequest | ToolInvocation | None
        if kind == "agent_output":
            row = session.scalar(
                select(AgentOutput).where(AgentOutput.tenant_id == ctx.tenant_id, AgentOutput.id == ref_id)
            )
        elif kind == "model_request":
            row = session.scalar(
                select(ModelRequest).where(ModelRequest.tenant_id == ctx.tenant_id, ModelRequest.id == ref_id)
            )
        elif kind == "tool_invocation":
            row = session.scalar(
                select(ToolInvocation).where(
                    ToolInvocation.tenant_id == ctx.tenant_id,
                    ToolInvocation.id == ref_id,
                )
            )
        else:
            raise _invalid("Unsupported incident evidence reference type.")
        if row is None:
            raise not_found()
        refs.append(f"{kind}:{ref_id}")
        metadata: dict[str, Any] = {"ref": refs[-1], "capability_id": row.capability_id}
        if isinstance(row, AgentOutput):
            metadata.update(
                status=row.status,
                output_hash=fingerprint(row.output),
                validation_hash=fingerprint(row.validation),
            )
        elif isinstance(row, ModelRequest):
            metadata.update(
                status=row.status,
                prompt_hash=row.prompt_hash,
                provider=row.provider,
                model=row.model,
            )
        else:
            metadata.update(outcome=row.outcome, tool_name=row.tool_name, args_hash=row.args_hash)
        evidence_metadata.append(metadata)
    incident = CapabilityIncident(
        tenant_id=ctx.tenant_id,
        capability_id=body.capability_id,
        status="OPEN",
        record_version=1,
        owner_role=body.owner_role,
        notification_status="PENDING",
        notification_attempts=0,
    )
    session.add(incident)
    session.flush()
    _event(
        session,
        incident,
        "OPENED",
        ctx.principal_id,
        {
            "reason": body.reason,
            "evidence_refs": sorted(set(refs)),
            "evidence_metadata": evidence_metadata,
            "owner_role": body.owner_role,
        },
    )
    return view(incident)


def _load(
    session: Session, ctx: PrincipalContext, incident_id: uuid.UUID, version: int
) -> CapabilityIncident:
    _lock(session, ctx.tenant_id, shared=False)
    row = session.scalar(
        select(CapabilityIncident)
        .where(
            CapabilityIncident.tenant_id == ctx.tenant_id,
            CapabilityIncident.id == incident_id,
        )
        .with_for_update()
    )
    if row is None:
        raise not_found()
    if row.record_version != version:
        raise _invalid("Stale incident version. Reload before retrying.")
    return row


def investigate(
    session: Session,
    ctx: PrincipalContext,
    incident_id: uuid.UUID,
    body: InvestigationRequest,
) -> dict[str, Any]:
    _authorize(ctx)
    row = _load(session, ctx, incident_id, body.expected_version)
    if row.status != "OPEN":
        raise _invalid("Only an OPEN incident can be investigated.")
    row.status, row.record_version = "INVESTIGATED", row.record_version + 1
    _event(
        session,
        row,
        "INVESTIGATED",
        ctx.principal_id,
        body.model_dump(exclude={"expected_version"}),
    )
    return view(row)


def restore(
    session: Session,
    ctx: PrincipalContext,
    incident_id: uuid.UUID,
    body: RestoreRequest,
) -> dict[str, Any]:
    _authorize(ctx, restore=True)
    row = _load(session, ctx, incident_id, body.expected_version)
    if row.status != "INVESTIGATED" or row.notification_status != "SENT":
        raise _invalid("Restoration requires recorded investigation and delivered owner notification.")
    if (
        session.scalar(
            select(CapabilityIncidentEvent.id).where(
                CapabilityIncidentEvent.tenant_id == ctx.tenant_id,
                CapabilityIncidentEvent.incident_id == row.id,
                CapabilityIncidentEvent.kind == "INVESTIGATED",
            )
        )
        is None
    ):
        raise _invalid("Investigation evidence is missing.")
    row.status, row.record_version = "RESTORED", row.record_version + 1
    _event(session, row, "RESTORED", ctx.principal_id, {"reason": body.reason})
    return view(row)


def deliver_notifications(session: Session, port: NotificationPort, batch: int = 50) -> dict[str, int]:
    rows = session.scalars(
        select(CapabilityIncident)
        .where(
            CapabilityIncident.notification_status.in_(["PENDING", "FAILED"]),
            CapabilityIncident.status != "RESTORED",
        )
        .order_by(CapabilityIncident.created_at)
        .limit(batch)
        .with_for_update(skip_locked=True)
    ).all()
    result = {"sent": 0, "failed": 0, "gave_up": 0}
    for row in rows:
        row.notification_attempts += 1
        try:
            port.send(
                Notification(
                    tenant_id=row.tenant_id,
                    idempotency_key=f"capability-incident:{row.tenant_id}:{row.id}",
                    recipient_role=row.owner_role,
                    template="capability.incident",
                    resource_ref=str(row.id),
                )
            )
        except NotificationError as exc:
            row.last_notification_error = exc.code[:64]
            row.notification_status = (
                "GAVE_UP" if not exc.transient or row.notification_attempts >= 5 else "FAILED"
            )
            result["gave_up" if row.notification_status == "GAVE_UP" else "failed"] += 1
            _event(
                session,
                row,
                "NOTIFICATION_FAILED",
                None,
                {
                    "attempt": row.notification_attempts,
                    "error_code": row.last_notification_error,
                },
            )
        else:
            row.notification_status, row.last_notification_error = "SENT", None
            result["sent"] += 1
            _event(
                session,
                row,
                "NOTIFIED",
                None,
                {"attempt": row.notification_attempts, "owner_role": row.owner_role},
            )
    session.flush()
    return result


def detail(session: Session, ctx: PrincipalContext, incident_id: uuid.UUID) -> dict[str, Any]:
    _authorize(ctx)
    row = session.scalar(
        select(CapabilityIncident).where(
            CapabilityIncident.tenant_id == ctx.tenant_id,
            CapabilityIncident.id == incident_id,
        )
    )
    if row is None:
        raise not_found()
    events = session.scalars(
        select(CapabilityIncidentEvent)
        .where(
            CapabilityIncidentEvent.tenant_id == ctx.tenant_id,
            CapabilityIncidentEvent.incident_id == incident_id,
        )
        .order_by(CapabilityIncidentEvent.created_at, CapabilityIncidentEvent.id)
        .limit(100)
    ).all()
    return {
        **view(row),
        "events": [{"id": str(event.id), "kind": event.kind, "details": event.details} for event in events],
        "event_limit": 100,
    }


def requeue_notification(
    session: Session,
    ctx: PrincipalContext,
    incident_id: uuid.UUID,
    body: RestoreRequest,
) -> dict[str, Any]:
    _authorize(ctx)
    row = _load(session, ctx, incident_id, body.expected_version)
    if row.status == "RESTORED" or row.notification_status != "GAVE_UP":
        raise _invalid("Only a stopped incident with GAVE_UP notification can be requeued.")
    # Keep total attempts and immutable failure history. Each explicit request permits
    # one additional attempt after the automatic five-attempt limit was exhausted.
    row.notification_status, row.record_version = "PENDING", row.record_version + 1
    _event(session, row, "NOTIFICATION_REQUEUED", ctx.principal_id, {"reason": body.reason})
    return view(row)
