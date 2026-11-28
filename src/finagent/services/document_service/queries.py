"""Scoped document reads: metadata, processing status, versions, typed fields (FR-013/014, UI §11)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from finagent.core.errors import not_found
from finagent.database.models import (
    Case,
    Document,
    DocumentExtraction,
    DocumentExtractionPayload,
    DocumentField,
    DocumentVersion,
    QuarantineRecord,
)
from finagent.domain.states import CASE_TERMINAL, CaseStatus
from finagent.security.authorization.policy import Permission, PrincipalContext, require
from finagent.services.audit_service import writer as audit
from finagent.services.case_service.queries import load_scoped_case, scope_filter
from finagent.services.document_service.processing import field_view, hydrate_field_payloads, next_action


def load_scoped_document(
    session: Session, ctx: PrincipalContext, raw_document_id: str, *, for_update: bool = False
) -> tuple[Document, Case] | None:
    try:
        document_id = uuid.UUID(raw_document_id)
    except ValueError:
        return None
    stmt = (
        select(Document, Case)
        .join(Case, and_(Case.tenant_id == Document.tenant_id, Case.id == Document.case_id))
        .where(Document.tenant_id == ctx.tenant_id, Document.id == document_id, scope_filter(ctx))
    )
    if for_update:
        stmt = stmt.with_for_update(of=Document)
    row = session.execute(stmt).first()
    return (row[0], row[1]) if row else None


def _version_view(session: Session, v: DocumentVersion) -> dict[str, Any]:
    extraction = session.scalar(
        select(DocumentExtraction)
        .where(DocumentExtraction.tenant_id == v.tenant_id, DocumentExtraction.version_id == v.id)
        .order_by(DocumentExtraction.created_at.desc())
        .limit(1)
    )
    quarantine = session.scalar(
        select(QuarantineRecord).where(
            QuarantineRecord.tenant_id == v.tenant_id, QuarantineRecord.document_version_id == v.id
        )
    )

    extraction_segments = None
    if extraction is not None:
        extraction_payload = session.scalar(
            select(DocumentExtractionPayload).where(
                DocumentExtractionPayload.tenant_id == extraction.tenant_id,
                DocumentExtractionPayload.extraction_id == extraction.id,
            )
        )
        extraction_segments = (
            extraction_payload.segments if extraction_payload is not None else extraction.segments
        )

    return {
        "version_id": str(v.id),
        "version_no": v.version_no,
        "status": v.status,
        "status_reason": v.status_reason,
        "next_action": next_action(v.status),
        "declared_media_type": v.declared_media_type,
        "detected_media_type": v.detected_media_type,
        "size_bytes": v.size_bytes,
        "sha256": v.sha256,
        "scan_result": v.scan_result,
        "scan_engine": v.scan_engine,
        "attempts": v.attempts,
        "next_attempt_at": v.next_attempt_at.isoformat() if v.next_attempt_at else None,
        "created_by": v.created_by,
        "created_at": v.created_at.isoformat(),
        "downloadable": v.status not in ("QUARANTINED", "RECEIVED"),
        "extraction": None
        if extraction is None
        else {
            "parser": extraction.parser,
            "profile": extraction.extraction_profile,
            "status": extraction.status,
            "page_count": extraction.page_count,
            "row_count": extraction.row_count,
            "warnings": extraction.warnings,
            "segments": extraction_segments[:200] if extraction_segments is not None else [],
        },
        "quarantine": None
        if quarantine is None
        else {
            "reason_code": quarantine.reason_code,
            "detail": quarantine.detail,
            "owner_role": quarantine.owner_role,
            "status": quarantine.status,
        },
    }


def document_view(session: Session, doc: Document) -> dict[str, Any]:
    versions = session.scalars(
        select(DocumentVersion)
        .where(DocumentVersion.tenant_id == doc.tenant_id, DocumentVersion.document_id == doc.id)
        .order_by(DocumentVersion.version_no.desc())
    ).all()
    fields = list(
        session.scalars(
            select(DocumentField)
            .where(DocumentField.tenant_id == doc.tenant_id, DocumentField.document_id == doc.id)
            .order_by(DocumentField.seq)
        ).all()
    )
    hydrate_field_payloads(session, fields)
    current = next((v for v in versions if v.version_no == doc.current_version_no), None)
    current_extractions = (
        set(
            session.scalars(
                select(DocumentExtraction.id).where(
                    DocumentExtraction.tenant_id == doc.tenant_id,
                    DocumentExtraction.version_id == current.id,
                )
            ).all()
        )
        if current is not None
        else set()
    )
    return {
        "document_id": str(doc.id),
        "case_id": str(doc.case_id),
        "document_type": doc.document_type,
        "title": doc.title,
        "source": doc.source,
        "source_ref": doc.source_ref,
        "classification": doc.classification,
        "current_version_no": doc.current_version_no,
        "status": current.status if current else None,
        "record_version": doc.record_version,
        "created_at": doc.created_at.isoformat(),
        "versions": [_version_view(session, v) for v in versions],
        "fields": [
            {**field_view(f), "from_current_version": f.extraction_id in current_extractions}
            for f in fields
            if f.is_current
        ],
        "field_history": [field_view(f) for f in fields if not f.is_current],
    }


def get_document(session: Session, ctx: PrincipalContext, raw_document_id: str) -> dict[str, Any]:
    require(ctx, Permission.CASE_READ)
    found = load_scoped_document(session, ctx, raw_document_id)
    if found is None:
        raise not_found()
    doc, case = found
    result = document_view(session, doc)
    can_upload = (
        Permission.DOCUMENT_UPLOAD in ctx.permissions
        and doc.source == "UPLOAD"
        and CaseStatus(case.status) not in CASE_TERMINAL
    )
    result["can_upload_version"] = can_upload
    result["upload_version_requires_mfa"] = (
        can_upload and Permission.DOCUMENT_UPLOAD in ctx.step_up and not ctx.mfa_satisfied
    )
    can_correct = Permission.CASE_UPDATE in ctx.permissions and CaseStatus(case.status) not in CASE_TERMINAL
    result["can_correct_fields"] = can_correct
    result["correct_fields_requires_mfa"] = (
        can_correct and Permission.CASE_UPDATE in ctx.step_up and not ctx.mfa_satisfied
    )
    return result


def extraction_segments_page(
    session: Session,
    ctx: PrincipalContext,
    raw_document_id: str,
    raw_version_id: str,
    offset: int,
    limit: int,
) -> dict[str, Any]:
    """Return a bounded slice of a scoped version's retained extraction payload."""
    require(ctx, Permission.CASE_READ)
    found = load_scoped_document(session, ctx, raw_document_id)
    if found is None:
        raise not_found()
    try:
        version_id = uuid.UUID(raw_version_id)
    except ValueError as exc:
        raise not_found() from exc
    doc = found[0]
    version = session.scalar(
        select(DocumentVersion).where(
            DocumentVersion.tenant_id == ctx.tenant_id,
            DocumentVersion.document_id == doc.id,
            DocumentVersion.id == version_id,
        )
    )
    if version is None:
        raise not_found()
    extraction = session.scalar(
        select(DocumentExtraction)
        .where(DocumentExtraction.tenant_id == ctx.tenant_id, DocumentExtraction.version_id == version.id)
        .order_by(DocumentExtraction.created_at.desc())
        .limit(1)
    )
    segments = None
    if extraction is not None:
        payload = session.scalar(
            select(DocumentExtractionPayload).where(
                DocumentExtractionPayload.tenant_id == ctx.tenant_id,
                DocumentExtractionPayload.extraction_id == extraction.id,
            )
        )
        segments = payload.segments if payload is not None else extraction.segments
    items = segments[offset : offset + limit] if segments is not None else []
    next_offset = (
        offset + len(items) if segments is not None and offset + len(items) < len(segments) else None
    )
    audit.record(
        session,
        tenant_id=ctx.tenant_id,
        action="DOCUMENT_SEGMENTS_VIEWED",
        object_type="document_version",
        object_id=str(version.id),
        outcome="SUCCESS",
        actor_principal_id=ctx.principal_id,
        actor_kind=ctx.kind.value,
        case_id=doc.case_id,
        details={
            "document_id": str(doc.id),
            "offset": offset,
            "returned_count": len(items),
            "available": segments is not None,
        },
    )
    return {
        "version_id": str(version.id),
        "available": segments is not None,
        "items": items,
        "next_offset": next_offset,
    }


def list_case_documents(
    session: Session,
    ctx: PrincipalContext,
    raw_case_id: str,
    *,
    limit: int | None = None,
    offset: int = 0,
    status: str | None = None,
    document_type: str | None = None,
) -> dict[str, Any]:
    require(ctx, Permission.CASE_READ)
    case = load_scoped_case(session, ctx, raw_case_id)
    if case is None:
        raise not_found()
    stmt = (
        select(Document, DocumentVersion)
        .outerjoin(
            DocumentVersion,
            and_(
                DocumentVersion.tenant_id == Document.tenant_id,
                DocumentVersion.document_id == Document.id,
                DocumentVersion.version_no == Document.current_version_no,
            ),
        )
        .where(Document.tenant_id == ctx.tenant_id, Document.case_id == case.id)
        .order_by(Document.created_at, Document.id)
    )
    if status is not None:
        stmt = stmt.where(DocumentVersion.status == status)
    if document_type is not None:
        stmt = stmt.where(Document.document_type == document_type)
    if limit is not None:
        stmt = stmt.offset(offset).limit(limit + 1)
    documents = session.execute(stmt).all()
    has_more = limit is not None and len(documents) > limit
    if limit is not None:
        documents = documents[:limit]
    items = []
    for d, current in documents:
        items.append(
            {
                "document_id": str(d.id),
                "document_type": d.document_type,
                "title": d.title,
                "source": d.source,
                "current_version_no": d.current_version_no,
                "status": current.status if current else None,
                "status_reason": current.status_reason if current else None,
                "next_action": next_action(current.status) if current else None,
                "created_at": d.created_at.isoformat(),
            }
        )
    can_upload = (
        Permission.DOCUMENT_UPLOAD in ctx.permissions and CaseStatus(case.status) not in CASE_TERMINAL
    )
    result: dict[str, Any] = {
        "items": items,
        "can_upload": can_upload,
        "upload_requires_mfa": (
            can_upload and Permission.DOCUMENT_UPLOAD in ctx.step_up and not ctx.mfa_satisfied
        ),
    }
    if limit is not None:
        result["applied_pagination"] = {"offset": offset, "limit": limit}
        result["next_offset"] = offset + limit if has_more else None
    if status is not None or document_type is not None:
        result["applied_filters"] = {"status": status, "document_type": document_type}
    return result
