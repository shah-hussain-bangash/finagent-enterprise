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
from finagent.security.authorization.policy import Permission, PrincipalContext, require
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
        "fields": [field_view(f) for f in fields if f.is_current],
        "field_history": [field_view(f) for f in fields if not f.is_current],
    }


def get_document(session: Session, ctx: PrincipalContext, raw_document_id: str) -> dict[str, Any]:
    require(ctx, Permission.CASE_READ)
    found = load_scoped_document(session, ctx, raw_document_id)
    if found is None:
        raise not_found()
    return document_view(session, found[0])


def list_case_documents(session: Session, ctx: PrincipalContext, raw_case_id: str) -> dict[str, Any]:
    require(ctx, Permission.CASE_READ)
    case = load_scoped_case(session, ctx, raw_case_id)
    if case is None:
        raise not_found()
    docs = session.scalars(
        select(Document)
        .where(Document.tenant_id == ctx.tenant_id, Document.case_id == case.id)
        .order_by(Document.created_at)
    ).all()
    items = []
    for d in docs:
        current = session.scalar(
            select(DocumentVersion).where(
                DocumentVersion.tenant_id == d.tenant_id,
                DocumentVersion.document_id == d.id,
                DocumentVersion.version_no == d.current_version_no,
            )
        )
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
    return {"items": items}
