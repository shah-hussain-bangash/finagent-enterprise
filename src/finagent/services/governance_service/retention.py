"""Retention rules and legal holds (FR-039, DIR-034, SPR-024; DEC-05 / SEC-D05 OPEN).

No retention period is assumed. Each record class has a rule that stays NOT_SET until the owner
approves a period; `evaluate` is a DRY RUN that lists what an APPROVED rule would make eligible,
excluding every case under an active legal hold. Executing deletion is deliberately NOT implemented
in Release 1: it is blocked until periods, methods and backup treatment are approved (honest gap).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, StringConstraints
from sqlalchemy import and_, exists, select
from sqlalchemy.orm import Session

from finagent.core.errors import AppError, ErrorCode, not_found
from finagent.database.models import (
    Case,
    Document,
    DocumentExtraction,
    DocumentExtractionPayload,
    DocumentField,
    DocumentFieldPayload,
    DocumentVersion,
    EvidenceItem,
    EvidenceItemPayload,
    LegalHold,
    RetentionRule,
)
from finagent.documents.storage import BlobStore
from finagent.security.authorization.policy import Permission, PrincipalContext, require
from finagent.services.audit_service import writer as audit
from finagent.services.case_service.queries import load_scoped_case

RECORD_CLASSES = (
    ("case_record", "CASE_COMPLETED_OR_CANCELLED", "logical-delete+purge-content"),
    ("document_content", "CASE_COMPLETED_OR_CANCELLED", "blob-purge"),
    ("evidence_and_snapshots", "CASE_COMPLETED_OR_CANCELLED", "purge-content-keep-hashes"),
    ("reports_and_decisions", "CASE_COMPLETED_OR_CANCELLED", "retain-until-approved-period"),
    ("audit_events", "EVENT_TIME", "archive-then-delete"),
    ("model_usage_records", "EVENT_TIME", "delete"),
    ("exports", "EXPORT_CREATED", "blob-purge"),
)


def ensure_rules(session: Session, tenant_id: uuid.UUID) -> None:
    have = set(
        session.scalars(select(RetentionRule.record_class).where(RetentionRule.tenant_id == tenant_id))
    )
    for record_class, trigger, method in RECORD_CLASSES:
        if record_class not in have:
            session.add(
                RetentionRule(
                    tenant_id=tenant_id,
                    record_class=record_class,
                    trigger_event=trigger,
                    retention_days=None,
                    deletion_method=method,
                    owner_role="Compliance (OPEN: SEC-D05)",
                    status="NOT_SET",
                    note="No period assumed (DEC-05); deletion disabled",
                )
            )
    session.flush()


def list_rules(session: Session, ctx: PrincipalContext) -> dict[str, Any]:
    if not ({Permission.TENANT_ADMIN, Permission.AUDIT_READ} & ctx.permissions):
        raise AppError(ErrorCode.FORBIDDEN)
    ensure_rules(session, ctx.tenant_id)
    rows = session.scalars(
        select(RetentionRule)
        .where(RetentionRule.tenant_id == ctx.tenant_id)
        .order_by(RetentionRule.record_class)
    ).all()
    return {
        "items": [
            {
                "record_class": r.record_class,
                "trigger_event": r.trigger_event,
                "retention_days": r.retention_days,
                "deletion_method": r.deletion_method,
                "owner_role": r.owner_role,
                "status": r.status,
                "note": r.note,
            }
            for r in rows
        ]
    }


def evaluate(session: Session, ctx: PrincipalContext) -> dict[str, Any]:
    """Dry run only: which closed cases an APPROVED case_record rule would make eligible."""
    require(ctx, Permission.TENANT_ADMIN)
    ensure_rules(session, ctx.tenant_id)
    rule = session.scalar(
        select(RetentionRule).where(
            RetentionRule.tenant_id == ctx.tenant_id, RetentionRule.record_class == "case_record"
        )
    )
    held = exists().where(
        LegalHold.tenant_id == Case.tenant_id, LegalHold.case_id == Case.id, LegalHold.released_at.is_(None)
    )
    result: dict[str, Any] = {"dry_run": True, "deletion_executed": False, "eligible": [], "held": 0}
    if rule is None or rule.status != "APPROVED" or rule.retention_days is None:
        result["reason"] = "No approved retention period (DEC-05 / SEC-D05 OPEN): nothing is eligible."
    else:
        cutoff = datetime.now(UTC) - timedelta(days=rule.retention_days)
        base = and_(
            Case.tenant_id == ctx.tenant_id,
            Case.status.in_(("COMPLETED", "CANCELLED")),
            Case.status_changed_at < cutoff,
        )
        result["eligible"] = [str(i) for i in session.scalars(select(Case.id).where(base, ~held))]
        result["held"] = len(list(session.scalars(select(Case.id).where(base, held))))
    audit.record(
        session,
        tenant_id=ctx.tenant_id,
        action="RETENTION_EVALUATED",
        object_type="retention",
        object_id=None,
        outcome="SUCCESS",
        actor_principal_id=ctx.principal_id,
        actor_kind=ctx.kind.value,
        details={"eligible": len(result["eligible"]), "held": result["held"], "dry_run": True},
    )
    return result


def execute_document_content(
    session: Session,
    ctx: PrincipalContext,
    store: BlobStore,
    *,
    enabled: bool,
) -> dict[str, Any]:
    """Purge eligible document content while retaining hashes and lineage."""

    require(ctx, Permission.TENANT_ADMIN)

    if not enabled:
        raise AppError(
            ErrorCode.FEATURE_DISABLED,
            "Retention execution is disabled.",
        )

    ensure_rules(session, ctx.tenant_id)

    rule = session.scalar(
        select(RetentionRule).where(
            RetentionRule.tenant_id == ctx.tenant_id,
            RetentionRule.record_class == "document_content",
        )
    )

    if rule is None or rule.status != "APPROVED" or rule.retention_days is None:
        raise AppError(
            ErrorCode.CASE_INVALID_STATE,
            "No approved document_content retention period; execution refused.",
        )

    cutoff = datetime.now(UTC) - timedelta(days=rule.retention_days)

    held = exists().where(
        LegalHold.tenant_id == Case.tenant_id,
        LegalHold.case_id == Case.id,
        LegalHold.released_at.is_(None),
    )

    base = and_(
        Case.tenant_id == ctx.tenant_id,
        Case.status.in_(("COMPLETED", "CANCELLED")),
        Case.status_changed_at < cutoff,
    )

    held_ids = list(session.scalars(select(Case.id).where(base, held)))

    cases = session.scalars(select(Case).where(base, ~held).order_by(Case.id).with_for_update()).all()

    totals = {
        "cases_processed": 0,
        "blobs_deleted": 0,
        "versions_purged": 0,
        "extractions_purged": 0,
        "fields_purged": 0,
    }

    for case in cases:
        documents = session.scalars(
            select(Document).where(
                Document.tenant_id == ctx.tenant_id,
                Document.case_id == case.id,
            )
        ).all()

        document_ids = [document.id for document in documents]

        case_blobs = 0
        case_versions = 0
        case_extractions = 0
        case_fields = 0

        if document_ids:
            versions = session.scalars(
                select(DocumentVersion)
                .where(
                    DocumentVersion.tenant_id == ctx.tenant_id,
                    DocumentVersion.document_id.in_(document_ids),
                )
                .order_by(
                    DocumentVersion.document_id,
                    DocumentVersion.version_no,
                )
                .with_for_update()
            ).all()

            for version in versions:
                if version.storage_key is not None:
                    # BlobStore.delete is idempotent. Delete external bytes
                    # before clearing the database pointer. If the DB
                    # transaction later fails, a retry safely repeats delete.
                    store.delete(version.storage_key)
                    version.storage_key = None
                    case_blobs += 1

                case_versions += 1

            # Phase 3: sensitive parser/field values live in separately
            # mutable payload tables. Delete those payload rows while retaining
            # immutable/versioned parent metadata, hashes and lineage.
            version_ids = [version.id for version in versions]

            if version_ids:
                extraction_payloads = list(
                    session.scalars(
                        select(DocumentExtractionPayload)
                        .join(
                            DocumentExtraction,
                            and_(
                                DocumentExtraction.tenant_id == DocumentExtractionPayload.tenant_id,
                                DocumentExtraction.id == DocumentExtractionPayload.extraction_id,
                            ),
                        )
                        .where(
                            DocumentExtractionPayload.tenant_id == ctx.tenant_id,
                            DocumentExtraction.version_id.in_(version_ids),
                        )
                        .with_for_update(of=DocumentExtractionPayload)
                    )
                )

                for extraction_payload in extraction_payloads:
                    session.delete(extraction_payload)

                case_extractions = len(extraction_payloads)

            field_payloads = list(
                session.scalars(
                    select(DocumentFieldPayload)
                    .join(
                        DocumentField,
                        and_(
                            DocumentField.tenant_id == DocumentFieldPayload.tenant_id,
                            DocumentField.id == DocumentFieldPayload.field_id,
                        ),
                    )
                    .where(
                        DocumentFieldPayload.tenant_id == ctx.tenant_id,
                        DocumentField.document_id.in_(document_ids),
                    )
                    .with_for_update(of=DocumentFieldPayload)
                )
            )

            for field_payload in field_payloads:
                session.delete(field_payload)

            case_fields = len(field_payloads)

        totals["cases_processed"] += 1
        totals["blobs_deleted"] += case_blobs
        totals["versions_purged"] += case_versions
        totals["extractions_purged"] += case_extractions
        totals["fields_purged"] += case_fields

        audit.record(
            session,
            tenant_id=ctx.tenant_id,
            action="RETENTION_DOCUMENT_CONTENT_PURGED",
            object_type="case",
            object_id=str(case.id),
            outcome="SUCCESS",
            actor_principal_id=ctx.principal_id,
            actor_kind=ctx.kind.value,
            case_id=case.id,
            details={
                "record_class": "document_content",
                "blobs_deleted": case_blobs,
                "versions_examined": case_versions,
                "extractions_purged": case_extractions,
                "fields_purged": case_fields,
            },
        )

    deletion_executed = bool(
        totals["blobs_deleted"] or totals["extractions_purged"] or totals["fields_purged"]
    )

    return {
        "dry_run": False,
        "execution_enabled": True,
        "record_class": "document_content",
        "deletion_executed": deletion_executed,
        "eligible": len(cases),
        "held": len(held_ids),
        **totals,
    }


def execute_evidence_and_snapshots(
    session: Session,
    ctx: PrincipalContext,
    *,
    enabled: bool,
) -> dict[str, Any]:
    """Purge evidence bodies while preserving immutable evidence/snapshot lineage."""

    require(ctx, Permission.TENANT_ADMIN)

    if not enabled:
        raise AppError(
            ErrorCode.FEATURE_DISABLED,
            "Retention execution is disabled.",
        )

    ensure_rules(session, ctx.tenant_id)

    rule = session.scalar(
        select(RetentionRule).where(
            RetentionRule.tenant_id == ctx.tenant_id,
            RetentionRule.record_class == "evidence_and_snapshots",
        )
    )

    if rule is None or rule.status != "APPROVED" or rule.retention_days is None:
        raise AppError(
            ErrorCode.CASE_INVALID_STATE,
            "No approved evidence_and_snapshots retention period; execution refused.",
        )

    cutoff = datetime.now(UTC) - timedelta(
        days=rule.retention_days,
    )

    held = exists().where(
        LegalHold.tenant_id == Case.tenant_id,
        LegalHold.case_id == Case.id,
        LegalHold.released_at.is_(None),
    )

    base = and_(
        Case.tenant_id == ctx.tenant_id,
        Case.status.in_(("COMPLETED", "CANCELLED")),
        Case.status_changed_at < cutoff,
    )

    held_ids = list(
        session.scalars(
            select(Case.id).where(
                base,
                held,
            )
        )
    )

    cases = list(
        session.scalars(
            select(Case)
            .where(
                base,
                ~held,
            )
            .order_by(Case.id)
            .with_for_update()
        )
    )

    payloads_purged = 0
    cases_processed = 0

    for case in cases:
        payloads = list(
            session.scalars(
                select(EvidenceItemPayload)
                .join(
                    EvidenceItem,
                    and_(
                        EvidenceItem.tenant_id == EvidenceItemPayload.tenant_id,
                        EvidenceItem.id == EvidenceItemPayload.evidence_item_id,
                    ),
                )
                .where(
                    EvidenceItemPayload.tenant_id == ctx.tenant_id,
                    EvidenceItem.case_id == case.id,
                )
                .order_by(
                    EvidenceItemPayload.id,
                )
                .with_for_update(
                    of=EvidenceItemPayload,
                )
            )
        )

        case_payloads = len(payloads)

        for payload in payloads:
            session.delete(payload)

        if case_payloads:
            cases_processed += 1
            payloads_purged += case_payloads

            audit.record(
                session,
                tenant_id=ctx.tenant_id,
                action="RETENTION_EVIDENCE_PAYLOAD_PURGED",
                object_type="case",
                object_id=str(case.id),
                outcome="SUCCESS",
                actor_principal_id=ctx.principal_id,
                actor_kind=ctx.kind.value,
                details={
                    "record_class": "evidence_and_snapshots",
                    "payloads_purged": case_payloads,
                    "snapshots_preserved": True,
                    "evidence_metadata_preserved": True,
                },
            )

    session.flush()

    return {
        "dry_run": False,
        "execution_enabled": True,
        "record_class": "evidence_and_snapshots",
        "deletion_executed": payloads_purged > 0,
        "eligible": len(cases),
        "held": len(held_ids),
        "cases_processed": cases_processed,
        "payloads_purged": payloads_purged,
        "evidence_metadata_preserved": True,
        "snapshots_preserved": True,
    }


class HoldRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: uuid.UUID
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=10, max_length=500)]


class ReleaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=10, max_length=500)]


def _hold_view(h: LegalHold) -> dict[str, Any]:
    return {
        "legal_hold_id": str(h.id),
        "case_id": str(h.case_id),
        "reason": h.reason,
        "placed_by": str(h.placed_by),
        "placed_at": h.placed_at.isoformat(),
        "released_at": h.released_at.isoformat() if h.released_at else None,
        "release_reason": h.release_reason,
        "active": h.released_at is None,
    }


def place_hold(session: Session, ctx: PrincipalContext, body: HoldRequest) -> dict[str, Any]:
    require(ctx, Permission.TENANT_ADMIN)  # MFA step-up
    case = session.scalar(select(Case).where(Case.tenant_id == ctx.tenant_id, Case.id == body.case_id))
    if case is None:
        raise not_found()
    hold = LegalHold(tenant_id=ctx.tenant_id, case_id=case.id, reason=body.reason, placed_by=ctx.principal_id)
    session.add(hold)
    session.flush()
    audit.record(
        session,
        tenant_id=ctx.tenant_id,
        action="LEGAL_HOLD_PLACED",
        object_type="legal_hold",
        object_id=str(hold.id),
        outcome="SUCCESS",
        actor_principal_id=ctx.principal_id,
        actor_kind=ctx.kind.value,
        case_id=case.id,
        details={"reason": body.reason},
    )
    return _hold_view(hold)


def release_hold(
    session: Session, ctx: PrincipalContext, raw_id: str, body: ReleaseRequest
) -> dict[str, Any]:
    require(ctx, Permission.TENANT_ADMIN)
    try:
        hid = uuid.UUID(raw_id)
    except ValueError as exc:
        raise not_found() from exc
    hold = session.scalar(
        select(LegalHold).where(LegalHold.tenant_id == ctx.tenant_id, LegalHold.id == hid).with_for_update()
    )
    if hold is None:
        raise not_found()
    if hold.released_at is not None:
        raise AppError(ErrorCode.CASE_INVALID_STATE, "This hold was already released.")
    hold.released_at, hold.released_by, hold.release_reason = datetime.now(UTC), ctx.principal_id, body.reason
    audit.record(
        session,
        tenant_id=ctx.tenant_id,
        action="LEGAL_HOLD_RELEASED",
        object_type="legal_hold",
        object_id=str(hold.id),
        outcome="SUCCESS",
        actor_principal_id=ctx.principal_id,
        actor_kind=ctx.kind.value,
        case_id=hold.case_id,
        details={"reason": body.reason},
    )
    return _hold_view(hold)


def list_holds(session: Session, ctx: PrincipalContext) -> dict[str, Any]:
    if not ({Permission.TENANT_ADMIN, Permission.AUDIT_READ} & ctx.permissions):
        raise AppError(ErrorCode.FORBIDDEN)
    rows = session.scalars(
        select(LegalHold).where(LegalHold.tenant_id == ctx.tenant_id).order_by(LegalHold.placed_at.desc())
    ).all()
    return {"items": [_hold_view(h) for h in rows]}


def active_hold(session: Session, ctx: PrincipalContext, raw_case_id: str) -> bool:
    case = load_scoped_case(session, ctx, raw_case_id)
    if case is None:
        raise not_found()
    return (
        session.scalar(
            select(LegalHold.id).where(
                LegalHold.tenant_id == ctx.tenant_id,
                LegalHold.case_id == case.id,
                LegalHold.released_at.is_(None),
            )
        )
        is not None
    )
