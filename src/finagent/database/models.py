"""Relational model for Release 1 foundation (MST §08, DIR §7, ADR-0006).

Tenant integrity: every tenant-scoped table has UNIQUE(tenant_id, id) and children reference
(tenant_id, parent_id) with composite foreign keys, so a row can never point at another tenant's
row. Later phases add evidence, documents, workflow, agent, knowledge and approval tables using the
same pattern.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY as PG_ARRAY
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from finagent.domain.states import CaseStatus, RunStatus


class Base(DeclarativeBase):
    pass


def _pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _tenant() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="RESTRICT"),
        nullable=False,
    )


def _now() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


def _status_check(column: str, values: list[str], name: str) -> CheckConstraint:
    quoted = ",".join(f"'{v}'" for v in values)
    return CheckConstraint(f"{column} IN ({quoted})", name=name)


class Tenant(Base):
    __tablename__ = "tenants"
    id: Mapped[uuid.UUID] = _pk()
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")
    is_synthetic: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = _now()


class Principal(Base):
    """Users and integration identities. (issuer, subject) maps a token to exactly one principal."""

    __tablename__ = "principals"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_principals_tenant_id"),
        UniqueConstraint("issuer", "subject", name="uq_principals_issuer_subject"),
        _status_check("kind", ["USER", "INTEGRATION"], "ck_principals_kind"),
        _status_check("status", ["ACTIVE", "SUSPENDED", "REMOVED"], "ck_principals_status"),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    issuer: Mapped[str] = mapped_column(String(300), nullable=False)
    subject: Mapped[str] = mapped_column(String(300), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")
    created_at: Mapped[datetime] = _now()


class RoleAssignment(Base):
    __tablename__ = "role_assignments"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "principal_id"], ["principals.tenant_id", "principals.id"]),
        UniqueConstraint("tenant_id", "principal_id", "role", name="uq_role_assignment"),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    principal_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    valid_from: Mapped[datetime] = _now()
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    granted_by: Mapped[str] = mapped_column(String(200), nullable=False)


class IntegrationConnection(Base):
    """Connector identity -> tenant + source system mapping (DIR-002, DIR-021)."""

    __tablename__ = "integration_connections"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "principal_id"], ["principals.tenant_id", "principals.id"]),
        UniqueConstraint("principal_id", name="uq_integration_principal"),
        UniqueConstraint("tenant_id", "source_system", name="uq_integration_source"),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    principal_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    source_system: Mapped[str] = mapped_column(String(100), nullable=False)
    permitted_alert_types: Mapped[list[str]] = mapped_column(ARRAY(String(64)), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")
    contract_version: Mapped[str] = mapped_column(String(16), nullable=False, default="1.0")


class Alert(Base):
    __tablename__ = "alerts"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_alerts_tenant_id"),
        UniqueConstraint("tenant_id", "source_system", "source_alert_id", name="uq_alerts_source_key"),
        CheckConstraint("window_start < window_end", name="ck_alerts_window"),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    source_system: Mapped[str] = mapped_column(String(100), nullable=False)
    source_alert_id: Mapped[str] = mapped_column(String(200), nullable=False)
    delivery_event_id: Mapped[str] = mapped_column(String(200), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(16), nullable=False)
    alert_type: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str | None] = mapped_column(String(32))
    rule_ref: Mapped[str | None] = mapped_column(String(200))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_event_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = _now()
    subject_refs: Mapped[list[str]] = mapped_column(ARRAY(String(200)), nullable=False)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    payload_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)


class Case(Base):
    __tablename__ = "cases"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_cases_tenant_id"),
        UniqueConstraint("tenant_id", "alert_id", name="uq_cases_alert"),
        ForeignKeyConstraint(["tenant_id", "alert_id"], ["alerts.tenant_id", "alerts.id"]),
        _status_check("status", [s.value for s in CaseStatus], "ck_cases_status"),
        Index(
            "ix_cases_tenant_status_created",
            "tenant_id",
            "status",
            text("created_at DESC"),
        ),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    alert_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    priority: Mapped[str] = mapped_column(String(16), nullable=False, default="NORMAL")
    queue: Mapped[str] = mapped_column(String(64), nullable=False)
    record_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    classification: Mapped[str] = mapped_column(String(32), nullable=False, default="RESTRICTED")
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()
    # Start of the current status episode: escalation due times are measured from here (FR-011).
    status_changed_at: Mapped[datetime] = _now()
    blocked_reason: Mapped[str | None] = mapped_column(String(300))
    blocked_owner_role: Mapped[str | None] = mapped_column(String(32))


class CaseAssignment(Base):
    __tablename__ = "case_assignments"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "case_id"], ["cases.tenant_id", "cases.id"]),
        ForeignKeyConstraint(["tenant_id", "principal_id"], ["principals.tenant_id", "principals.id"]),
        _status_check("assignment_role", ["ANALYST", "REVIEWER"], "ck_assignment_role"),
        Index(
            "uq_case_active_assignment",
            "tenant_id",
            "case_id",
            "assignment_role",
            unique=True,
            postgresql_where=text("active"),
        ),
        Index("ix_assignments_principal_active", "tenant_id", "principal_id", "active"),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    case_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    principal_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    assignment_role: Mapped[str] = mapped_column(String(16), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    reason: Mapped[str] = mapped_column(String(300), nullable=False)
    assigned_by: Mapped[str] = mapped_column(String(200), nullable=False)
    assigned_at: Mapped[datetime] = _now()
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CaseStatusHistory(Base):
    """Ordered by `seq`: rows written in one transaction share now(), so time cannot order them."""

    __tablename__ = "case_status_history"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "case_id"], ["cases.tenant_id", "cases.id"]),
        Index("ix_case_status_history_case", "tenant_id", "case_id", "seq"),
    )
    id: Mapped[uuid.UUID] = _pk()
    seq: Mapped[int] = mapped_column(BigInteger, Identity(always=True), nullable=False, unique=True)
    tenant_id: Mapped[uuid.UUID] = _tenant()
    case_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(32))
    to_status: Mapped[str] = mapped_column(String(32), nullable=False)
    actor: Mapped[str] = mapped_column(String(200), nullable=False)
    reason: Mapped[str] = mapped_column(String(300), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    occurred_at: Mapped[datetime] = _now()


class AuditEvent(Base):
    """Append-only (DB trigger), hash-chained per tenant (SPR-035/036, FR-036)."""

    __tablename__ = "audit_events"
    __table_args__ = (
        UniqueConstraint("tenant_id", "seq", name="uq_audit_tenant_seq"),
        Index("ix_audit_tenant_case_time", "tenant_id", "case_id", "occurred_at"),
        Index("ix_audit_tenant_action_time", "tenant_id", "action", "occurred_at"),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    occurred_at: Mapped[datetime] = _now()
    actor_principal_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    actor_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    object_type: Mapped[str] = mapped_column(String(64), nullable=False)
    object_id: Mapped[str | None] = mapped_column(String(100))
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    case_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    prev_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    hash: Mapped[str] = mapped_column(String(64), nullable=False)


class OutboxEvent(Base):
    """Transactional outbox (MST:ARCH-003, MST:DB-003, prompt §21). id == envelope event_id."""

    __tablename__ = "outbox_events"
    __table_args__ = (
        Index(
            "ix_outbox_unpublished",
            "created_at",
            postgresql_where=text("published_at IS NULL"),
        ),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(16), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(100), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    occurred_at: Mapped[datetime] = _now()
    created_at: Mapped[datetime] = _now()
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(64))


_ACTIVE_RUN = "status NOT IN ('SUCCEEDED','FAILED','CANCELLED')"


class WorkflowRun(Base):
    """Technical execution of a workflow for one case (SRS FR-005; ADR-0003/0004).

    Run status is separate from case status. At most one non-terminal run per case.
    """

    __tablename__ = "workflow_runs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_workflow_runs_tenant_id"),
        ForeignKeyConstraint(["tenant_id", "case_id"], ["cases.tenant_id", "cases.id"]),
        ForeignKeyConstraint(["tenant_id", "requested_by"], ["principals.tenant_id", "principals.id"]),
        _status_check("status", [s.value for s in RunStatus], "ck_workflow_runs_status"),
        Index(
            "uq_workflow_runs_active_case",
            "tenant_id",
            "case_id",
            unique=True,
            postgresql_where=text(_ACTIVE_RUN),
        ),
        Index("ix_workflow_runs_status_next", "status", "next_attempt_at"),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    case_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    workflow_type: Mapped[str] = mapped_column(String(64), nullable=False)
    graph_version: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    status_reason: Mapped[str] = mapped_column(String(300), nullable=False)
    requested_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(64))
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_requested_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    resume_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    record_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WorkflowRunHistory(Base):
    """Every run status change with reason and timestamp (FR-005)."""

    __tablename__ = "workflow_run_history"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "run_id"], ["workflow_runs.tenant_id", "workflow_runs.id"]),
        Index("ix_workflow_run_history_run", "tenant_id", "run_id", "seq"),
    )
    id: Mapped[uuid.UUID] = _pk()
    seq: Mapped[int] = mapped_column(BigInteger, Identity(always=True), nullable=False, unique=True)
    tenant_id: Mapped[uuid.UUID] = _tenant()
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(32))
    to_status: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str] = mapped_column(String(300), nullable=False)
    actor: Mapped[str] = mapped_column(String(200), nullable=False)
    occurred_at: Mapped[datetime] = _now()


class WorkflowStep(Base):
    """Completed-step references (FR-031). `step_key` is unique per run so a replayed node finds
    its committed result instead of repeating the effect (AER-021)."""

    __tablename__ = "workflow_steps"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_workflow_steps_tenant_id"),
        UniqueConstraint("tenant_id", "run_id", "step_key", name="uq_workflow_steps_key"),
        ForeignKeyConstraint(["tenant_id", "run_id"], ["workflow_runs.tenant_id", "workflow_runs.id"]),
        _status_check(
            "status",
            ["RUNNING", "SUCCEEDED", "FAILED", "CANCELLED", "DISCARDED"],
            "ck_workflow_steps_status",
        ),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    step_key: Mapped[str] = mapped_column(String(120), nullable=False)
    node: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    fencing_token: Mapped[int] = mapped_column(BigInteger, nullable=False)
    worker_id: Mapped[str] = mapped_column(String(100), nullable=False)
    output_ref: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error_code: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime] = _now()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Checkpoint(Base):
    """LangGraph checkpoints in OUR schema, tenant-scoped, payloads encrypted (prompt §7, §20)."""

    __tablename__ = "checkpoints"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "run_id"], ["workflow_runs.tenant_id", "workflow_runs.id"]),
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    checkpoint_ns: Mapped[str] = mapped_column(String(200), primary_key=True)
    checkpoint_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    parent_checkpoint_id: Mapped[str | None] = mapped_column(String(64))
    graph_version: Mapped[str] = mapped_column(String(32), nullable=False)
    fencing_token: Mapped[int] = mapped_column(BigInteger, nullable=False)
    checkpoint_type: Mapped[str] = mapped_column(String(64), nullable=False)
    checkpoint_data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    metadata_type: Mapped[str] = mapped_column(String(64), nullable=False)
    metadata_data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = _now()


class CheckpointBlob(Base):
    __tablename__ = "checkpoint_blobs"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "run_id"], ["workflow_runs.tenant_id", "workflow_runs.id"]),
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    checkpoint_ns: Mapped[str] = mapped_column(String(200), primary_key=True)
    channel: Mapped[str] = mapped_column(String(200), primary_key=True)
    version: Mapped[str] = mapped_column(String(80), primary_key=True)
    value_type: Mapped[str] = mapped_column(String(64), nullable=False)
    value_data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)


class CheckpointWrite(Base):
    __tablename__ = "checkpoint_writes"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "run_id"], ["workflow_runs.tenant_id", "workflow_runs.id"]),
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    checkpoint_ns: Mapped[str] = mapped_column(String(200), primary_key=True)
    checkpoint_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    idx: Mapped[int] = mapped_column(Integer, primary_key=True)
    channel: Mapped[str] = mapped_column(String(200), nullable=False)
    task_path: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    value_type: Mapped[str] = mapped_column(String(64), nullable=False)
    value_data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)


class Lease(Base):
    """Exclusive execution lease with a monotonic fencing token (FR-034, DIR-031, prompt §20).

    Rows are never deleted: the token keeps increasing across owners, so any write carrying an
    older token is provably stale.
    """

    __tablename__ = "leases"
    __table_args__ = (
        UniqueConstraint("tenant_id", "resource_type", "resource_id", name="uq_leases_resource"),
        CheckConstraint("fencing_token > 0", name="ck_leases_token"),
        Index("ix_leases_expiry", "resource_type", "expires_at"),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    owner: Mapped[str | None] = mapped_column(String(100))
    fencing_token: Mapped[int] = mapped_column(BigInteger, nullable=False)
    acquired_at: Mapped[datetime] = _now()
    heartbeat_at: Mapped[datetime] = _now()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class InboxEvent(Base):
    """Consumer-side dedup: an event id is processed at most once per consumer (prompt §21)."""

    __tablename__ = "inbox_events"
    __table_args__ = (UniqueConstraint("tenant_id", "consumer", "event_id", name="uq_inbox_event"),)
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    consumer: Mapped[str] = mapped_column(String(64), nullable=False)
    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    processed_at: Mapped[datetime] = _now()


class DeadLetter(Base):
    """Poison messages after bounded deliveries (NQ-023): restricted, owned, with a reason."""

    __tablename__ = "dead_letters"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_dead_letters_tenant_id"),
        _status_check("status", ["OPEN", "REPLAYED", "DISCARDED"], "ck_dead_letters_status"),
        Index("ix_dead_letters_open", "tenant_id", "status", "created_at"),
    )
    id: Mapped[uuid.UUID] = _pk()
    # NULL only for poison messages whose tenant could not be established (malformed/forged).
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=True
    )
    consumer: Mapped[str] = mapped_column(String(64), nullable=False)
    event_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    delivery_count: Mapped[int] = mapped_column(Integer, nullable=False)
    envelope: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    owner_role: Mapped[str] = mapped_column(String(32), nullable=False, default="PLATFORM_OPERATOR")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="OPEN")
    created_at: Mapped[datetime] = _now()
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    resolution_note: Mapped[str | None] = mapped_column(String(500))


EXTERNAL_OP_STATUSES = [
    "PENDING",
    "SENT",
    "CONFIRMED",
    "FAILED",
    "OUTCOME_UNCERTAIN",
    "RECONCILED_APPLIED",
    "RECONCILED_NOT_APPLIED",
]


class ExternalOperation(Base):
    """Ledger of externally visible effects (prompt §20; DIR-029; MST:REC-002/003).

    One row per (tenant, operation_type, operation_key). The key is sent to the provider as its
    idempotency key; before any retry the provider is asked for the outcome. If it cannot prove
    the outcome the row becomes OUTCOME_UNCERTAIN and only a human reconciliation moves it on.
    """

    __tablename__ = "external_operations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_external_operations_tenant_id"),
        UniqueConstraint(
            "tenant_id",
            "operation_type",
            "operation_key",
            name="uq_external_operation_key",
        ),
        ForeignKeyConstraint(["tenant_id", "case_id"], ["cases.tenant_id", "cases.id"]),
        ForeignKeyConstraint(["tenant_id", "run_id"], ["workflow_runs.tenant_id", "workflow_runs.id"]),
        _status_check("status", EXTERNAL_OP_STATUSES, "ck_external_operations_status"),
        Index("ix_external_operations_status", "tenant_id", "status"),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    case_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    operation_type: Mapped[str] = mapped_column(String(64), nullable=False)
    operation_key: Mapped[str] = mapped_column(String(200), nullable=False)
    target_system: Mapped[str] = mapped_column(String(100), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="PENDING")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error_code: Mapped[str | None] = mapped_column(String(64))
    provider_reference: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()
    reconciled_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    reconciled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reconciliation_note: Mapped[str | None] = mapped_column(String(500))


class QueueMessage(Base):
    """PostgreSQL-backed local queue (prompt §2 "PostgreSQL-backed local queue"). Azure uses
    Service Bus behind the same port; this table is never used when that adapter is selected."""

    __tablename__ = "queue_messages"
    __table_args__ = (
        UniqueConstraint("topic", "event_id", name="uq_queue_message_event"),
        Index("ix_queue_messages_ready", "topic", "visible_at"),
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = _tenant()
    topic: Mapped[str] = mapped_column(String(64), nullable=False)
    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    body: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    enqueued_at: Mapped[datetime] = _now()
    visible_at: Mapped[datetime] = _now()
    delivery_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lock_token: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ToolInvocation(Base):
    """Every tool-gateway decision, allowed or denied (FR-024, AER-025, SPR-029). Arguments are
    stored only as a hash: denied requests are recorded safely without echoing their content."""

    __tablename__ = "tool_invocations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_tool_invocations_tenant_id"),
        ForeignKeyConstraint(["tenant_id", "case_id"], ["cases.tenant_id", "cases.id"]),
        _status_check("outcome", ["ALLOWED", "DENIED", "FAILED"], "ck_tool_invocations_outcome"),
        Index("ix_tool_invocations_case", "tenant_id", "case_id", "created_at"),
        Index("ix_tool_invocations_outcome", "tenant_id", "outcome", "created_at"),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    case_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    capability_id: Mapped[str] = mapped_column(String(64), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(100), nullable=False)
    tool_version: Mapped[str | None] = mapped_column(String(16))
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(64))
    args_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = _now()


class FeatureFlag(Base):
    """DB-backed, tenant-scoped, audited flags (prompt §44). Absent row = disabled (fail safe)."""

    __tablename__ = "feature_flags"
    __table_args__ = (UniqueConstraint("tenant_id", "flag_key", name="uq_feature_flag"),)
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    flag_key: Mapped[str] = mapped_column(String(64), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    reason: Mapped[str] = mapped_column(String(300), nullable=False)
    updated_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    updated_at: Mapped[datetime] = _now()


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (UniqueConstraint("tenant_id", "operation", "key", name="uq_idempotency"),)
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    operation: Mapped[str] = mapped_column(String(100), nullable=False)
    key: Mapped[str] = mapped_column(String(200), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status_code: Mapped[int | None] = mapped_column(Integer)
    response: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    resource_id: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = _now()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Delegation(Base):
    """Explicit, time-bounded, audited delegation of one role (FR-003, SPR-006)."""

    __tablename__ = "delegations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_delegations_tenant_id"),
        ForeignKeyConstraint(["tenant_id", "delegator_id"], ["principals.tenant_id", "principals.id"]),
        ForeignKeyConstraint(["tenant_id", "delegate_id"], ["principals.tenant_id", "principals.id"]),
        CheckConstraint("delegator_id <> delegate_id", name="ck_delegation_not_self"),
        CheckConstraint("valid_from < valid_to", name="ck_delegation_window"),
        Index("ix_delegations_delegate", "tenant_id", "delegate_id", "valid_to"),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    delegator_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    delegate_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str] = mapped_column(String(300), nullable=False)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_to: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = _now()
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


class BreakGlassGrant(Base):
    """Emergency read access (SPR-008): justified, auto-expiring, alerted, reviewed afterwards."""

    __tablename__ = "break_glass_grants"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_break_glass_tenant_id"),
        ForeignKeyConstraint(["tenant_id", "principal_id"], ["principals.tenant_id", "principals.id"]),
        CheckConstraint("activated_at < expires_at", name="ck_break_glass_window"),
        _status_check(
            "review_outcome",
            ["JUSTIFIED", "NOT_JUSTIFIED"],
            "ck_break_glass_review_outcome",
        ),
        Index("ix_break_glass_active", "tenant_id", "principal_id", "expires_at"),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    principal_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    justification: Mapped[str] = mapped_column(String(1000), nullable=False)
    activated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    review_outcome: Mapped[str | None] = mapped_column(String(16))
    review_note: Mapped[str | None] = mapped_column(String(1000))


class EscalationRule(Base):
    """Configured due time per case status and its escalation owner (FR-011; SLAs are DEC-06)."""

    __tablename__ = "escalation_rules"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_escalation_rules_tenant_id"),
        UniqueConstraint("tenant_id", "rule_key", name="uq_escalation_rule_key"),
        _status_check(
            "applies_to_status",
            [s.value for s in CaseStatus],
            "ck_escalation_rule_status",
        ),
        CheckConstraint("due_after_minutes > 0", name="ck_escalation_rule_due"),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    rule_key: Mapped[str] = mapped_column(String(64), nullable=False)
    applies_to_status: Mapped[str] = mapped_column(String(32), nullable=False)
    due_after_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    owner_role: Mapped[str] = mapped_column(String(32), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    version: Mapped[str] = mapped_column(String(32), nullable=False, default="1-PROPOSED")


class Escalation(Base):
    """One escalation per (case, rule, status episode): re-evaluation and notification retries
    never create a second escalation. Delivery state stays visible (FR-011)."""

    __tablename__ = "escalations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_escalations_tenant_id"),
        UniqueConstraint(
            "tenant_id",
            "case_id",
            "rule_id",
            "episode_started_at",
            name="uq_escalation_once",
        ),
        ForeignKeyConstraint(["tenant_id", "case_id"], ["cases.tenant_id", "cases.id"]),
        ForeignKeyConstraint(
            ["tenant_id", "rule_id"],
            ["escalation_rules.tenant_id", "escalation_rules.id"],
        ),
        _status_check(
            "notification_status",
            ["PENDING", "SENT", "FAILED", "GAVE_UP"],
            "ck_escalation_notification",
        ),
        Index("ix_escalations_open", "tenant_id", "resolved_at", "escalated_at"),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    case_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    rule_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    case_status: Mapped[str] = mapped_column(String(32), nullable=False)
    episode_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    escalated_at: Mapped[datetime] = _now()
    owner_role: Mapped[str] = mapped_column(String(32), nullable=False)
    notification_status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING")
    notification_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_notification_error: Mapped[str | None] = mapped_column(String(64))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# --------------------------------------------------------------------------------------------------
# P6 documents and evidence (FR-013..016, DIR-014, DIR-025, AER-017, SPR-012)

DOCUMENT_VERSION_STATUSES = [
    "RECEIVED",  # bytes accepted, not yet scanned (never visible as usable)
    "QUARANTINED",  # suspicious: malware, signature mismatch, encrypted, active content
    "STORED",  # scanned clean and stored; waiting for the parser
    "PARSING",
    "PARSED",
    "OCR_UNAVAILABLE",  # no text layer and no OCR provider: explicit, never "empty but fine"
    "PARSE_FAILED",  # recoverable: source preserved, retry or re-upload possible
]


class Document(Base):
    """A logical case document. Content lives in immutable DocumentVersion rows."""

    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_documents_tenant_id"),
        ForeignKeyConstraint(["tenant_id", "case_id"], ["cases.tenant_id", "cases.id"]),
        _status_check("source", ["UPLOAD", "CONNECTOR"], "ck_documents_source"),
        Index(
            "uq_documents_connector_ref",
            "tenant_id",
            "case_id",
            "source_ref",
            unique=True,
            postgresql_where=text("source = 'CONNECTOR'"),
        ),
        Index("ix_documents_case", "tenant_id", "case_id", "created_at"),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    case_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    document_type: Mapped[str] = mapped_column(String(48), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    source_ref: Mapped[str | None] = mapped_column(String(200))
    subject_ref: Mapped[str | None] = mapped_column(String(200))  # customer the document describes
    classification: Mapped[str] = mapped_column(String(32), nullable=False, default="CONFIDENTIAL")
    current_version_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_by: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()
    record_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class DocumentVersion(Base):
    """One immutable upload/fetch of a document's bytes plus its processing state."""

    __tablename__ = "document_versions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_document_versions_tenant_id"),
        UniqueConstraint("tenant_id", "document_id", "version_no", name="uq_document_version_no"),
        ForeignKeyConstraint(["tenant_id", "document_id"], ["documents.tenant_id", "documents.id"]),
        _status_check("status", DOCUMENT_VERSION_STATUSES, "ck_document_versions_status"),
        CheckConstraint("size_bytes >= 0", name="ck_document_versions_size"),
        Index("ix_document_versions_pending", "status", "next_attempt_at"),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    declared_media_type: Mapped[str] = mapped_column(String(120), nullable=False)
    detected_media_type: Mapped[str | None] = mapped_column(String(120))
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_key: Mapped[str | None] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    status_reason: Mapped[str | None] = mapped_column(String(300))
    scan_result: Mapped[str | None] = mapped_column(String(24))
    scan_engine: Mapped[str | None] = mapped_column(String(64))
    scanned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error_code: Mapped[str | None] = mapped_column(String(64))
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()


class DocumentExtraction(Base):
    """Parser output for one version: status, limits hit, and text segments WITH provenance."""

    __tablename__ = "document_extractions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_document_extractions_tenant_id"),
        ForeignKeyConstraint(
            ["tenant_id", "version_id"],
            ["document_versions.tenant_id", "document_versions.id"],
        ),
        Index("ix_document_extractions_version", "tenant_id", "version_id"),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    parser: Mapped[str] = mapped_column(String(64), nullable=False)
    extraction_profile: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    page_count: Mapped[int | None] = mapped_column(Integer)
    row_count: Mapped[int | None] = mapped_column(Integer)
    segments: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)
    warnings: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    limits: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = _now()


class DocumentExtractionPayload(Base):
    """Purgeable parser payload separated from immutable extraction metadata."""

    __tablename__ = "document_extraction_payloads"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "extraction_id",
            name="uq_document_extraction_payload",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "extraction_id"],
            [
                "document_extractions.tenant_id",
                "document_extractions.id",
            ],
            ondelete="CASCADE",
        ),
    )

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    extraction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    segments: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
    )
    created_at: Mapped[datetime] = _now()


class DocumentField(Base):
    """Typed extracted value with provenance. Corrections ADD a row (origin CORRECTED) that
    supersedes the previous one; the original extracted value is never overwritten (AER-017)."""

    __tablename__ = "document_fields"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_document_fields_tenant_id"),
        ForeignKeyConstraint(
            ["tenant_id", "extraction_id"],
            ["document_extractions.tenant_id", "document_extractions.id"],
        ),
        ForeignKeyConstraint(["tenant_id", "document_id"], ["documents.tenant_id", "documents.id"]),
        ForeignKeyConstraint(
            ["tenant_id", "supersedes_id"],
            ["document_fields.tenant_id", "document_fields.id"],
        ),
        _status_check("origin", ["EXTRACTED", "CORRECTED"], "ck_document_fields_origin"),
        _status_check(
            "value_state",
            [
                "PRESENT",
                "MISSING",
                "UNKNOWN",
                "WITHHELD",
                "NOT_APPLICABLE",
                "EXTRACTION_FAILED",
            ],
            "ck_document_fields_state",
        ),
        _status_check(
            "validation_status",
            ["VALID", "INVALID_FORMAT", "UNVERIFIED", "CONFLICTING", "MISSING"],
            "ck_document_fields_validation",
        ),
        Index("ix_document_fields_doc", "tenant_id", "document_id", "is_current"),
    )
    id: Mapped[uuid.UUID] = _pk()
    seq: Mapped[int] = mapped_column(BigInteger, Identity(always=True), nullable=False, unique=True)
    tenant_id: Mapped[uuid.UUID] = _tenant()
    extraction_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    field_key: Mapped[str] = mapped_column(String(64), nullable=False)
    value_text: Mapped[str | None] = mapped_column(String(1000))
    value_type: Mapped[str] = mapped_column(String(16), nullable=False)
    value_state: Mapped[str] = mapped_column(String(24), nullable=False)
    currency: Mapped[str | None] = mapped_column(String(3))
    page: Mapped[int | None] = mapped_column(Integer)
    row: Mapped[int | None] = mapped_column(Integer)
    section: Mapped[str | None] = mapped_column(String(120))
    validation_status: Mapped[str] = mapped_column(String(24), nullable=False)
    confidence_note: Mapped[str | None] = mapped_column(String(300))
    origin: Mapped[str] = mapped_column(String(16), nullable=False, default="EXTRACTED")
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    corrected_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    correction_reason: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = _now()


class DocumentFieldPayload(Base):
    """Purgeable sensitive value for a versioned document field."""

    __tablename__ = "document_field_payloads"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "field_id",
            name="uq_document_field_payload",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "field_id"],
            [
                "document_fields.tenant_id",
                "document_fields.id",
            ],
            ondelete="CASCADE",
        ),
    )

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    field_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    value_text: Mapped[str | None] = mapped_column(String(1000))
    created_at: Mapped[datetime] = _now()


class QuarantineRecord(Base):
    """Restricted quarantine for suspicious content (DIR-014, DIR-025): safe reason, owner, state."""

    __tablename__ = "quarantine_records"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_quarantine_records_tenant_id"),
        ForeignKeyConstraint(["tenant_id", "case_id"], ["cases.tenant_id", "cases.id"]),
        ForeignKeyConstraint(
            ["tenant_id", "document_version_id"],
            ["document_versions.tenant_id", "document_versions.id"],
        ),
        _status_check("status", ["OPEN", "CLOSED"], "ck_quarantine_status"),
        Index("ix_quarantine_open", "tenant_id", "status", "created_at"),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    case_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    document_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    detail: Mapped[str] = mapped_column(String(300), nullable=False)
    owner_role: Mapped[str] = mapped_column(String(32), nullable=False, default="AML_SUPERVISOR")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="OPEN")
    created_at: Mapped[datetime] = _now()
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution_note: Mapped[str | None] = mapped_column(String(500))


EVIDENCE_SOURCE_TYPES = [
    "CUSTOMER_PROFILE",
    "TRANSACTIONS",
    "KYC_DOCUMENT_LIST",
    "SANCTIONS_SUMMARY",
    "DOCUMENT_EXTRACTION",
]


class EvidenceItem(Base):
    """Versioned evidence record. Re-fetching with changed content creates a new version and marks
    the old one SUPERSEDED (FR-016); identical content reuses the current version."""

    __tablename__ = "evidence_items"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_evidence_items_tenant_id"),
        UniqueConstraint(
            "tenant_id",
            "case_id",
            "source_type",
            "source_ref",
            "version_no",
            name="uq_evidence_item_version",
        ),
        ForeignKeyConstraint(["tenant_id", "case_id"], ["cases.tenant_id", "cases.id"]),
        ForeignKeyConstraint(
            ["tenant_id", "supersedes_id"],
            ["evidence_items.tenant_id", "evidence_items.id"],
        ),
        _status_check("source_type", EVIDENCE_SOURCE_TYPES, "ck_evidence_source_type"),
        _status_check("status", ["CURRENT", "SUPERSEDED"], "ck_evidence_status"),
        _status_check("freshness", ["FRESH", "STALE", "UNKNOWN"], "ck_evidence_freshness"),
        _status_check(
            "completeness",
            ["COMPLETE", "INCOMPLETE", "NOT_APPLICABLE"],
            "ck_evidence_completeness",
        ),
        Index(
            "uq_evidence_current",
            "tenant_id",
            "case_id",
            "source_type",
            "source_ref",
            unique=True,
            postgresql_where=text("status = 'CURRENT'"),
        ),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    case_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="CURRENT")
    content: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_system: Mapped[str] = mapped_column(String(100), nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    as_of: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    freshness: Mapped[str] = mapped_column(String(16), nullable=False)
    completeness: Mapped[str] = mapped_column(String(16), nullable=False)
    classification: Mapped[str] = mapped_column(String(32), nullable=False)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    change_reason: Mapped[str | None] = mapped_column(String(300))
    tool_invocation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    document_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_by: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = _now()


class EvidenceItemPayload(Base):
    """Purgeable evidence body; parent identity and content hash remain retained."""

    __tablename__ = "evidence_item_payloads"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "evidence_item_id",
            name="uq_evidence_item_payload",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "evidence_item_id"],
            [
                "evidence_items.tenant_id",
                "evidence_items.id",
            ],
            ondelete="CASCADE",
        ),
    )

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    evidence_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    content: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
    )
    created_at: Mapped[datetime] = _now()


class EvidenceSnapshot(Base):
    """Immutable, versioned set of evidence item versions (FR-016). A DB trigger rejects UPDATE and
    DELETE, so a review bound to a snapshot id always sees exactly what was reviewed."""

    __tablename__ = "evidence_snapshots"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_evidence_snapshots_tenant_id"),
        UniqueConstraint("tenant_id", "case_id", "version_no", name="uq_evidence_snapshot_version"),
        ForeignKeyConstraint(["tenant_id", "case_id"], ["cases.tenant_id", "cases.id"]),
        _status_check(
            "completeness_status",
            ["COMPLETE", "INCOMPLETE", "CONFLICTED"],
            "ck_evidence_snapshot_status",
        ),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    case_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    completeness_status: Mapped[str] = mapped_column(String(16), nullable=False)
    requirements_version: Mapped[str] = mapped_column(String(32), nullable=False)
    gaps: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    open_conflicts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_by: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = _now()


class EvidenceSnapshotItem(Base):
    __tablename__ = "evidence_snapshot_items"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "snapshot_id"],
            ["evidence_snapshots.tenant_id", "evidence_snapshots.id"],
        ),
        ForeignKeyConstraint(
            ["tenant_id", "evidence_item_id"],
            ["evidence_items.tenant_id", "evidence_items.id"],
        ),
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    snapshot_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    evidence_item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)


class EvidenceConflict(Base):
    """Contradictory values between two evidence records (FR-015, AER-007). Both values are kept;
    an analyst resolution records a reason and never deletes either source."""

    __tablename__ = "evidence_conflicts"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_evidence_conflicts_tenant_id"),
        UniqueConstraint(
            "tenant_id",
            "case_id",
            "field_key",
            "item_a_id",
            "item_b_id",
            name="uq_evidence_conflict_pair",
        ),
        ForeignKeyConstraint(["tenant_id", "case_id"], ["cases.tenant_id", "cases.id"]),
        ForeignKeyConstraint(
            ["tenant_id", "item_a_id"],
            ["evidence_items.tenant_id", "evidence_items.id"],
        ),
        ForeignKeyConstraint(
            ["tenant_id", "item_b_id"],
            ["evidence_items.tenant_id", "evidence_items.id"],
        ),
        _status_check("status", ["OPEN", "RESOLVED", "SUPERSEDED"], "ck_evidence_conflict_status"),
        _status_check(
            "resolution",
            [
                "USE_A",
                "USE_B",
                "BOTH_RETAINED_EXPLAINED",
                "SOURCE_CORRECTION_REQUESTED",
            ],
            "ck_evidence_conflict_resolution",
        ),
        Index("ix_evidence_conflicts_case", "tenant_id", "case_id", "status"),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    case_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    field_key: Mapped[str] = mapped_column(String(64), nullable=False)
    item_a_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    item_b_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    value_a: Mapped[str | None] = mapped_column(String(300))
    value_b: Mapped[str | None] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="OPEN")
    resolution: Mapped[str | None] = mapped_column(String(32))
    rationale: Mapped[str | None] = mapped_column(String(1000))
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _now()


# --------------------------------------------------------------------------------------------------
# P7 policy knowledge (FR-017..020, AER-011..013, prompt §14)

POLICY_INGESTION_STATUSES = [
    "RECEIVED",
    "SCANNED",
    "PARSED",
    "INDEXED",
    "FAILED",
    "DELETED",
]


class PolicyDocument(Base):
    __tablename__ = "policy_documents"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_policy_documents_tenant_id"),
        UniqueConstraint("tenant_id", "policy_key", name="uq_policy_documents_key"),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    policy_key: Mapped[str] = mapped_column(String(100), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    owner_role: Mapped[str] = mapped_column(String(32), nullable=False, default="POLICY_ADMIN")
    owner_label: Mapped[str] = mapped_column(String(200), nullable=False)
    department: Mapped[str] = mapped_column(String(64), nullable=False)
    classification: Mapped[str] = mapped_column(String(32), nullable=False)
    applicability: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="CONNECTOR")
    created_at: Mapped[datetime] = _now()


class PolicyVersion(Base):
    """Only APPROVED versions whose effective window covers the as-of date are retrievable."""

    __tablename__ = "policy_versions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_policy_versions_tenant_id"),
        UniqueConstraint(
            "tenant_id",
            "policy_document_id",
            "version_label",
            name="uq_policy_version_label",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "policy_document_id"],
            ["policy_documents.tenant_id", "policy_documents.id"],
        ),
        _status_check(
            "approval_status",
            ["DRAFT", "APPROVED", "RETIRED", "REVOKED"],
            "ck_policy_version_approval",
        ),
        _status_check("ingestion_status", POLICY_INGESTION_STATUSES, "ck_policy_version_ingestion"),
        CheckConstraint(
            "effective_to IS NULL OR effective_from <= effective_to",
            name="ck_policy_effective",
        ),
        Index(
            "ix_policy_versions_effective",
            "tenant_id",
            "policy_document_id",
            "effective_from",
        ),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    policy_document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    version_label: Mapped[str] = mapped_column(String(32), nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date)
    approval_status: Mapped[str] = mapped_column(String(16), nullable=False, default="DRAFT")
    ingestion_status: Mapped[str] = mapped_column(String(16), nullable=False, default="RECEIVED")
    allowed_roles: Mapped[list[str]] = mapped_column(PG_ARRAY(String(32)), nullable=False)  # && overlap
    sections: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_ref: Mapped[str | None] = mapped_column(String(200))
    submitted_by: Mapped[str] = mapped_column(String(200), nullable=False)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status_reason: Mapped[str | None] = mapped_column(String(300))
    status_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status_changed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    index_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = _now()


class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_knowledge_chunks_tenant_id"),
        UniqueConstraint("tenant_id", "policy_version_id", "chunk_key", name="uq_knowledge_chunk_key"),
        ForeignKeyConstraint(
            ["tenant_id", "policy_version_id"],
            ["policy_versions.tenant_id", "policy_versions.id"],
        ),
        Index("ix_knowledge_chunks_version", "tenant_id", "policy_version_id", "active"),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    policy_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    chunk_key: Mapped[str] = mapped_column(String(64), nullable=False)
    section: Mapped[str] = mapped_column(String(64), nullable=False)
    heading: Mapped[str] = mapped_column(String(300), nullable=False)
    page: Mapped[int | None] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    text_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = _now()


class RetrievalRun(Base):
    """Every retrieval: who, which filters, which chunks, and the outcome (FR-018, AER-011)."""

    __tablename__ = "retrieval_runs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_retrieval_runs_tenant_id"),
        _status_check(
            "outcome",
            ["ANSWERED", "INSUFFICIENT_EVIDENCE", "CONFLICTING_GUIDANCE"],
            "ck_retrieval_outcome",
        ),
        Index("ix_retrieval_runs_case", "tenant_id", "case_id", "created_at"),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    case_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    principal_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    query_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    as_of: Mapped[date] = mapped_column(Date, nullable=False)
    retriever: Mapped[str] = mapped_column(String(64), nullable=False)
    filters: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    results: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = _now()


# --------------------------------------------------------------------------------------------------
# P8 model gateway usage and agent outputs (AER-019..022, NQ-048, prompt §10/§11)


class ModelRequest(Base):
    """Usage record for EVERY model call, including failures, retries and repairs (prompt §11)."""

    __tablename__ = "model_requests"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_model_requests_tenant_id"),
        _status_check(
            "status",
            [
                "SUCCEEDED",
                "FAILED",
                "TIMEOUT",
                "INVALID_OUTPUT",
                "BUDGET_DENIED",
                "POLICY_DENIED",
            ],
            "ck_model_requests_status",
        ),
        _status_check("purpose", ["INITIAL", "RETRY", "REPAIR"], "ck_model_requests_purpose"),
        Index("ix_model_requests_tenant_time", "tenant_id", "created_at"),
        Index("ix_model_requests_run", "tenant_id", "run_id"),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    case_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    capability_id: Mapped[str] = mapped_column(String(64), nullable=False)
    capability_version: Mapped[str] = mapped_column(String(32), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    config_version: Mapped[str] = mapped_column(String(64), nullable=False)
    purpose: Mapped[str] = mapped_column(String(16), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    estimated_cost: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False, default=Decimal("0"))
    cost_currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_code: Mapped[str | None] = mapped_column(String(64))
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = _now()


class AgentOutput(Base):
    """Validated capability output with lineage. Invalid output is stored as FAILED, never as
    accepted findings (prompt §10)."""

    __tablename__ = "agent_outputs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_agent_outputs_tenant_id"),
        ForeignKeyConstraint(["tenant_id", "case_id"], ["cases.tenant_id", "cases.id"]),
        ForeignKeyConstraint(
            ["tenant_id", "snapshot_id"],
            ["evidence_snapshots.tenant_id", "evidence_snapshots.id"],
        ),
        _status_check(
            "status",
            ["SUCCEEDED", "PARTIAL", "INSUFFICIENT_EVIDENCE", "ESCALATED", "FAILED"],
            "ck_agent_outputs_status",
        ),
        Index("ix_agent_outputs_case", "tenant_id", "case_id", "created_at"),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    case_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    snapshot_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    capability_id: Mapped[str] = mapped_column(String(64), nullable=False)
    capability_version: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    output: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    validation: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    lineage: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = _now()


# --------------------------------------------------------------------------------------------------
# P10 reports, review requests and decisions (FR-026..030, AER-016, DIR-018, SPR-033)

REPORT_STATUSES = ["DRAFT", "SUBMITTED", "APPROVED", "FINALIZED"]
APPROVAL_STATUSES = [
    "PENDING",
    "APPROVED",
    "RETURNED",
    "INFO_REQUESTED",
    "REJECTED",
    "INVALIDATED",
    "WITHDRAWN",
    "CONSUMED",  # an approval used by a completed finalization
]
DECISIONS = ["APPROVE", "RETURN_FOR_REWORK", "REQUEST_INFORMATION", "REJECT"]


class Report(Base):
    __tablename__ = "reports"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_reports_tenant_id"),
        UniqueConstraint("tenant_id", "case_id", name="uq_reports_case"),
        ForeignKeyConstraint(["tenant_id", "case_id"], ["cases.tenant_id", "cases.id"]),
        _status_check("status", REPORT_STATUSES, "ck_reports_status"),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    case_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="DRAFT")
    current_version_no: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    record_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()


class ReportVersion(Base):
    """Immutable revision (DB trigger): content, author, time, snapshot binding and lineage."""

    __tablename__ = "report_versions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_report_versions_tenant_id"),
        UniqueConstraint("tenant_id", "report_id", "version_no", name="uq_report_version_no"),
        ForeignKeyConstraint(["tenant_id", "report_id"], ["reports.tenant_id", "reports.id"]),
        ForeignKeyConstraint(
            ["tenant_id", "snapshot_id"],
            ["evidence_snapshots.tenant_id", "evidence_snapshots.id"],
        ),
        _status_check("origin", ["AI_DRAFT", "ANALYST_EDIT"], "ck_report_versions_origin"),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    report_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    content: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    origin: Mapped[str] = mapped_column(String(16), nullable=False)
    author_principal_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    author_label: Mapped[str] = mapped_column(String(200), nullable=False)
    base_version_no: Mapped[int | None] = mapped_column(Integer)
    edit_note: Mapped[str | None] = mapped_column(String(500))
    lineage: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    validation: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = _now()


class ReportClaim(Base):
    """Claim-to-evidence link with an explicit support status (AER-026, DIR-017). Immutable."""

    __tablename__ = "report_claims"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_report_claims_tenant_id"),
        UniqueConstraint("tenant_id", "report_version_id", "claim_key", name="uq_report_claim_key"),
        ForeignKeyConstraint(
            ["tenant_id", "report_version_id"],
            ["report_versions.tenant_id", "report_versions.id"],
        ),
        _status_check(
            "claim_type",
            ["FACT", "DETERMINISTIC_RESULT", "INTERPRETATION", "GAP"],
            "ck_report_claims_type",
        ),
        _status_check(
            "support_status",
            ["SUPPORTED", "PARTIAL", "UNSUPPORTED", "NOT_APPLICABLE"],
            "ck_report_claims_support",
        ),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    report_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    claim_key: Mapped[str] = mapped_column(String(64), nullable=False)
    section: Mapped[str] = mapped_column(String(64), nullable=False)
    claim_type: Mapped[str] = mapped_column(String(24), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    support_status: Mapped[str] = mapped_column(String(16), nullable=False)
    evidence_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    citation_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = _now()


class ApprovalRequest(Base):
    """Deterministic review request bound to an exact report version and evidence snapshot."""

    __tablename__ = "approval_requests"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_approval_requests_tenant_id"),
        ForeignKeyConstraint(["tenant_id", "case_id"], ["cases.tenant_id", "cases.id"]),
        ForeignKeyConstraint(["tenant_id", "report_id"], ["reports.tenant_id", "reports.id"]),
        ForeignKeyConstraint(
            ["tenant_id", "report_version_id"],
            ["report_versions.tenant_id", "report_versions.id"],
        ),
        ForeignKeyConstraint(
            ["tenant_id", "snapshot_id"],
            ["evidence_snapshots.tenant_id", "evidence_snapshots.id"],
        ),
        ForeignKeyConstraint(["tenant_id", "requested_by"], ["principals.tenant_id", "principals.id"]),
        _status_check("status", APPROVAL_STATUSES, "ck_approval_requests_status"),
        Index(
            "uq_approval_open_per_case",
            "tenant_id",
            "case_id",
            unique=True,
            postgresql_where=text("status IN ('PENDING','APPROVED')"),
        ),
        Index("ix_approval_requests_reviewer", "tenant_id", "reviewer_id", "status"),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    case_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    report_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    report_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    snapshot_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    requested_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    reviewer_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    matrix_version: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING")
    record_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = _now()
    status_changed_at: Mapped[datetime] = _now()
    status_reason: Mapped[str | None] = mapped_column(String(300))


class ApprovalDecision(Base):
    """Append-only reviewer decision (one per request). Binds report version + snapshot."""

    __tablename__ = "approval_decisions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_approval_decisions_tenant_id"),
        UniqueConstraint("tenant_id", "request_id", name="uq_approval_decision_once"),
        ForeignKeyConstraint(
            ["tenant_id", "request_id"],
            ["approval_requests.tenant_id", "approval_requests.id"],
        ),
        ForeignKeyConstraint(["tenant_id", "reviewer_id"], ["principals.tenant_id", "principals.id"]),
        _status_check("decision", DECISIONS, "ck_approval_decisions_decision"),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    request_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    decision: Mapped[str] = mapped_column(String(24), nullable=False)
    rationale: Mapped[str] = mapped_column(String(2000), nullable=False)
    reviewer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    reviewer_role: Mapped[str] = mapped_column(String(32), nullable=False)
    delegation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    on_behalf_of: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    mfa_satisfied: Mapped[bool] = mapped_column(Boolean, nullable=False)
    report_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    snapshot_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    config_version: Mapped[str] = mapped_column(String(64), nullable=False)
    effect: Mapped[str] = mapped_column(String(32), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    decided_at: Mapped[datetime] = _now()


class Finalization(Base):
    """Finalization attempt bound to one approval. The case reaches COMPLETED only when the
    external write is confirmed or explicitly not performed by configuration (FR-029, AER-023)."""

    __tablename__ = "finalizations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_finalizations_tenant_id"),
        UniqueConstraint("tenant_id", "approval_request_id", name="uq_finalization_approval"),
        ForeignKeyConstraint(["tenant_id", "case_id"], ["cases.tenant_id", "cases.id"]),
        ForeignKeyConstraint(
            ["tenant_id", "approval_request_id"],
            ["approval_requests.tenant_id", "approval_requests.id"],
        ),
        _status_check(
            "status",
            [
                "STARTED",
                "COMPLETED",
                "WRITE_NOT_PERFORMED",
                "OUTCOME_UNCERTAIN",
                "FAILED",
                "INVALIDATED",
            ],
            "ck_finalizations_status",
        ),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    case_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    approval_request_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="STARTED")
    external_operation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    write_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    requested_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    status_reason: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()


class ReportExport(Base):
    """Authorized export with lineage and a mandatory Draft watermark when not approved (FR-030)."""

    __tablename__ = "report_exports"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_report_exports_tenant_id"),
        ForeignKeyConstraint(["tenant_id", "case_id"], ["cases.tenant_id", "cases.id"]),
        ForeignKeyConstraint(
            ["tenant_id", "report_version_id"],
            ["report_versions.tenant_id", "report_versions.id"],
        ),
        _status_check("kind", ["DRAFT", "APPROVED"], "ck_report_exports_kind"),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    case_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    report_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    approval_request_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    media_type: Mapped[str] = mapped_column(String(100), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(300), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = _now()


# --------------------------------------------------------------------------------------------------
# P12 governance: evaluation, configuration promotion, retention and legal hold (AER-029..035, FR-039)


class EvaluationRun(Base):
    """One evaluation of the CURRENT configuration on a versioned SYNTHETIC dataset. Immutable."""

    __tablename__ = "evaluation_runs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_evaluation_runs_tenant_id"),
        _status_check(
            "gate",
            ["PASS_PROPOSED", "FAIL_PROPOSED", "INCOMPLETE"],
            "ck_evaluation_gate",
        ),
        Index("ix_evaluation_runs_tenant", "tenant_id", "created_at"),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    dataset: Mapped[str] = mapped_column(String(100), nullable=False)
    dataset_version: Mapped[str] = mapped_column(String(32), nullable=False)
    dataset_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    thresholds_version: Mapped[str] = mapped_column(String(64), nullable=False)
    gate: Mapped[str] = mapped_column(String(16), nullable=False)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    results: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = _now()


class ConfigurationVersion(Base):
    """Promotion record for one agent/model/retrieval configuration (config hash). Promotion needs a
    passing evaluation of the SAME hash; rollback re-promotes an earlier version (lineage kept)."""

    __tablename__ = "configuration_versions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_configuration_versions_tenant_id"),
        UniqueConstraint("tenant_id", "config_hash", name="uq_configuration_hash"),
        ForeignKeyConstraint(
            ["tenant_id", "evaluation_id"],
            ["evaluation_runs.tenant_id", "evaluation_runs.id"],
        ),
        _status_check(
            "status",
            ["CANDIDATE", "PROMOTED", "SUPERSEDED", "ROLLED_BACK"],
            "ck_configuration_status",
        ),
        Index(
            "uq_configuration_one_promoted",
            "tenant_id",
            unique=True,
            postgresql_where=text("status = 'PROMOTED'"),
        ),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    content: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="CANDIDATE")
    evaluation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    previous_promoted_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    changed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    change_reason: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()


class RetentionRule(Base):
    """Record-class retention (DIR-034, FR-039). No period is assumed: NOT_SET until approved."""

    __tablename__ = "retention_rules"
    __table_args__ = (
        UniqueConstraint("tenant_id", "record_class", name="uq_retention_record_class"),
        _status_check("status", ["NOT_SET", "PROPOSED", "APPROVED"], "ck_retention_status"),
        CheckConstraint("retention_days IS NULL OR retention_days > 0", name="ck_retention_days"),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    record_class: Mapped[str] = mapped_column(String(64), nullable=False)
    trigger_event: Mapped[str] = mapped_column(String(64), nullable=False)
    retention_days: Mapped[int | None] = mapped_column(Integer)
    deletion_method: Mapped[str] = mapped_column(String(64), nullable=False)
    owner_role: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="NOT_SET")
    note: Mapped[str | None] = mapped_column(String(300))


class LegalHold(Base):
    """A hold suspends retention deletion for a case (FR-039, SPR-024). Release is recorded, not deleted."""

    __tablename__ = "legal_holds"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_legal_holds_tenant_id"),
        ForeignKeyConstraint(["tenant_id", "case_id"], ["cases.tenant_id", "cases.id"]),
        Index("ix_legal_holds_active", "tenant_id", "case_id", "released_at"),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    case_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    placed_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    placed_at: Mapped[datetime] = _now()
    released_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    release_reason: Mapped[str | None] = mapped_column(String(500))


class IntakeReconciliation(Base):
    """Restricted quarantine for conflicting/invalid intake (DIR-010, DIR-025, FR-008)."""

    __tablename__ = "intake_reconciliations"
    __table_args__ = (Index("ix_intake_recon_open", "tenant_id", "status"),)
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    source_system: Mapped[str] = mapped_column(String(100), nullable=False)
    source_alert_id: Mapped[str] = mapped_column(String(200), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    existing_alert_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    payload_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="OPEN")
    owner_role: Mapped[str] = mapped_column(String(32), nullable=False, default="AML_SUPERVISOR")
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = _now()
    note: Mapped[str | None] = mapped_column(Text)


class CapabilityIncident(Base):
    """Tenant emergency stop. Evidence and lifecycle records live in immutable events."""

    __tablename__ = "capability_incidents"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_capability_incident_tenant_id"),
        CheckConstraint(
            "status IN ('OPEN','INVESTIGATED','RESTORED')",
            name="ck_capability_incident_status",
        ),
        CheckConstraint("record_version > 0", name="ck_capability_incident_version"),
        CheckConstraint(
            "notification_status IN ('PENDING','FAILED','SENT','GAVE_UP')",
            name="ck_capability_incident_notification",
        ),
        Index(
            "uq_capability_incident_active",
            "tenant_id",
            "capability_id",
            unique=True,
            postgresql_where=text("status <> 'RESTORED'"),
        ),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    capability_id: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="OPEN")
    record_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    owner_role: Mapped[str] = mapped_column(String(32), nullable=False)
    notification_status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING")
    notification_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_notification_error: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = _now()


class CapabilityIncidentEvent(Base):
    """Append-only metadata: declaration, evidence references, investigation and restoration."""

    __tablename__ = "capability_incident_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "incident_id"],
            ["capability_incidents.tenant_id", "capability_incidents.id"],
        ),
        CheckConstraint(
            "kind IN ('OPENED','INVESTIGATED','RESTORED','NOTIFIED',"
            "'NOTIFICATION_FAILED','NOTIFICATION_REQUEUED')",
            name="ck_capability_incident_event_kind",
        ),
        Index("ix_capability_incident_events", "tenant_id", "incident_id", "created_at"),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    incident_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = _now()


class ConnectorObservation(Base):
    """Immutable logical connector call metadata; never URLs, arguments or response content."""

    __tablename__ = "connector_observations"
    __table_args__ = (
        CheckConstraint("source IN ('core-banking','OTHER')", name="ck_connector_observation_source"),
        CheckConstraint("operation IN ('JSON','BYTES')", name="ck_connector_observation_operation"),
        CheckConstraint("outcome IN ('SUCCEEDED','FAILED')", name="ck_connector_observation_outcome"),
        CheckConstraint("attempts >= 0 AND latency_ms >= 0", name="ck_connector_observation_counts"),
        Index("ix_connector_observation_time", "tenant_id", "source", "created_at"),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    operation: Mapped[str] = mapped_column(String(8), nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(32))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = _now()


class PasswordCredential(Base):
    """Global login lookup, explicitly linked to an existing tenant-owned human."""

    __tablename__ = "password_credentials"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_password_credentials_tenant_id"),
        UniqueConstraint("email", name="uq_password_credentials_email"),
        UniqueConstraint("principal_id", name="uq_password_credentials_principal"),
        ForeignKeyConstraint(["tenant_id", "principal_id"], ["principals.tenant_id", "principals.id"]),
        CheckConstraint("version > 0 AND failed_attempts >= 0", name="ck_password_credential_counters"),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    principal_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    email: Mapped[str] = mapped_column(String(254), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(300), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    failed_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _now()


class PasswordSession(Base):
    __tablename__ = "password_sessions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_password_sessions_tenant_id"),
        UniqueConstraint("token_hash", name="uq_password_sessions_token_hash"),
        ForeignKeyConstraint(
            ["tenant_id", "credential_id"], ["password_credentials.tenant_id", "password_credentials.id"]
        ),
        Index("ix_password_sessions_expiry", "expires_at"),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    credential_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    credential_version: Mapped[int] = mapped_column(Integer, nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _now()


class ConnectorCredential(Base):
    """Pre-tenant local SYNTHETIC identity lookup, never an authority claim."""

    __tablename__ = "connector_credentials"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_connector_credentials_tenant_id"),
        UniqueConstraint("token_hash", name="uq_connector_credentials_token_hash"),
        ForeignKeyConstraint(["tenant_id", "principal_id"], ["principals.tenant_id", "principals.id"]),
        CheckConstraint("scope = 'alert:ingest'", name="ck_connector_credential_scope"),
        CheckConstraint("expires_at > created_at", name="ck_connector_credential_window"),
        Index("ix_connector_credentials_principal_expiry", "tenant_id", "principal_id", "expires_at"),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    principal_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    scope: Mapped[str] = mapped_column(String(32), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _now()


class TotpFactor(Base):
    __tablename__ = "totp_factors"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_totp_factors_tenant_id"),
        UniqueConstraint("tenant_id", "principal_id", name="uq_totp_factors_principal"),
        ForeignKeyConstraint(["tenant_id", "principal_id"], ["principals.tenant_id", "principals.id"]),
        CheckConstraint(
            "version > 0 AND last_counter >= -1 AND failed_attempts >= 0", name="ck_totp_factor_counters"
        ),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    principal_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    encrypted_secret: Mapped[str] = mapped_column(String(300), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    last_counter: Mapped[int] = mapped_column(BigInteger, nullable=False, default=-1)
    failed_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _now()


class TotpGrant(Base):
    __tablename__ = "totp_grants"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_totp_grants_tenant_id"),
        UniqueConstraint("password_session_id", name="uq_totp_grants_session"),
        ForeignKeyConstraint(
            ["tenant_id", "password_session_id"], ["password_sessions.tenant_id", "password_sessions.id"]
        ),
        ForeignKeyConstraint(["tenant_id", "factor_id"], ["totp_factors.tenant_id", "totp_factors.id"]),
        CheckConstraint(
            "expires_at > verified_at AND expires_at <= verified_at + interval '5 minutes'",
            name="ck_totp_grant_window",
        ),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    password_session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    factor_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    factor_version: Mapped[int] = mapped_column(Integer, nullable=False)
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class EmailVerificationChallenge(Base):
    __tablename__ = "email_verification_challenges"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_email_verification_tenant_id"),
        ForeignKeyConstraint(
            ["tenant_id", "credential_id"], ["password_credentials.tenant_id", "password_credentials.id"]
        ),
        ForeignKeyConstraint(
            ["tenant_id", "password_session_id"], ["password_sessions.tenant_id", "password_sessions.id"]
        ),
        CheckConstraint(
            "failed_attempts >= 0 AND failed_attempts <= 5 AND credential_version > 0",
            name="ck_email_verification_attempts",
        ),
        CheckConstraint(
            "expires_at > created_at AND expires_at <= created_at + interval '5 minutes'",
            name="ck_email_verification_window",
        ),
        CheckConstraint(
            "delivery_status IN ('PENDING','SENT','FAILED')", name="ck_email_verification_delivery"
        ),
        Index("ix_email_verification_account_created", "tenant_id", "credential_id", "created_at"),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    credential_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    credential_version: Mapped[int] = mapped_column(Integer, nullable=False)
    password_session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    email_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    delivery_status: Mapped[str] = mapped_column(String(10), nullable=False)
    failed_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = _now()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PasskeyFactor(Base):
    __tablename__ = "passkey_factors"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_passkey_factor_tenant"),
        UniqueConstraint("tenant_id", "principal_id", name="uq_passkey_factor_principal"),
        UniqueConstraint("credential_id", name="uq_passkey_credential_id"),
        ForeignKeyConstraint(["tenant_id", "principal_id"], ["principals.tenant_id", "principals.id"]),
        CheckConstraint("version > 0 AND sign_count >= 0", name="ck_passkey_factor_counters"),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    principal_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    credential_id: Mapped[str] = mapped_column(String(2048), nullable=False)
    public_key: Mapped[str] = mapped_column(String(4096), nullable=False)
    sign_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = _now()


class PasskeyChallenge(Base):
    __tablename__ = "passkey_challenges"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_passkey_challenge_tenant"),
        ForeignKeyConstraint(
            ["tenant_id", "credential_id"], ["password_credentials.tenant_id", "password_credentials.id"]
        ),
        ForeignKeyConstraint(
            ["tenant_id", "password_session_id"], ["password_sessions.tenant_id", "password_sessions.id"]
        ),
        CheckConstraint(
            "purpose IN ('REGISTER','VERIFY') AND credential_version > 0", name="ck_passkey_challenge_purpose"
        ),
        CheckConstraint(
            "expires_at > created_at AND expires_at <= created_at + interval '2 minutes'",
            name="ck_passkey_challenge_window",
        ),
        Index("ix_passkey_challenge_session", "tenant_id", "password_session_id", "purpose"),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    password_session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    credential_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    credential_version: Mapped[int] = mapped_column(Integer, nullable=False)
    purpose: Mapped[str] = mapped_column(String(12), nullable=False)
    challenge: Mapped[str] = mapped_column(String(100), nullable=False)
    rp_id: Mapped[str] = mapped_column(String(253), nullable=False)
    origin: Mapped[str] = mapped_column(String(300), nullable=False)
    created_at: Mapped[datetime] = _now()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PasskeyGrant(Base):
    __tablename__ = "passkey_grants"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_passkey_grant_tenant"),
        UniqueConstraint("password_session_id", name="uq_passkey_grant_session"),
        ForeignKeyConstraint(
            ["tenant_id", "password_session_id"], ["password_sessions.tenant_id", "password_sessions.id"]
        ),
        ForeignKeyConstraint(["tenant_id", "factor_id"], ["passkey_factors.tenant_id", "passkey_factors.id"]),
        CheckConstraint(
            "factor_version > 0 AND expires_at > verified_at "
            "AND expires_at <= verified_at + interval '5 minutes'",
            name="ck_passkey_grant_window",
        ),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    password_session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    factor_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    factor_version: Mapped[int] = mapped_column(Integer, nullable=False)
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
