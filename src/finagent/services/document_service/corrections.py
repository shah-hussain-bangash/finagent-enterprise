"""Analyst correction of an extracted field (FR-014, AER-017, FR-006).

A correction adds a CORRECTED field row that supersedes the current one. The original extracted
value, its provenance and the source document are never changed. The document's evidence record is
re-versioned, which is a material change and invalidates affected approvals.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, StringConstraints
from sqlalchemy import select
from sqlalchemy.orm import Session

from finagent.core.errors import AppError, ErrorCode, not_found
from finagent.database.models import DocumentExtraction, DocumentField, DocumentFieldPayload, DocumentVersion
from finagent.documents.extraction import typed_field
from finagent.domain.states import CASE_TERMINAL, CaseStatus
from finagent.security.authorization.policy import Permission, PrincipalContext, require
from finagent.services.audit_service import writer as audit
from finagent.services.case_service.transitions import Actor
from finagent.services.document_service.processing import current_fields, field_view, record_document_evidence
from finagent.services.document_service.queries import load_scoped_document


class FieldCorrection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    value: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=10, max_length=300)]


def correct_field(
    session: Session, ctx: PrincipalContext, raw_document_id: str, raw_field_id: str, body: FieldCorrection
) -> dict[str, Any]:
    require(ctx, Permission.CASE_UPDATE)
    found = load_scoped_document(session, ctx, raw_document_id, for_update=True)
    if found is None:
        raise not_found()
    doc, case = found
    if CaseStatus(case.status) in CASE_TERMINAL:
        raise AppError(ErrorCode.CASE_INVALID_STATE, "Completed or cancelled cases cannot be changed.")
    try:
        field_id = uuid.UUID(raw_field_id)
    except ValueError as exc:
        raise not_found() from exc
    field = session.scalar(
        select(DocumentField)
        .where(
            DocumentField.tenant_id == ctx.tenant_id,
            DocumentField.document_id == doc.id,
            DocumentField.id == field_id,
        )
        .with_for_update()
    )
    if field is None:
        raise not_found()
    if not field.is_current:
        raise AppError(
            ErrorCode.VERSION_CONFLICT,
            "This value was already corrected or replaced. Reload the document and try again.",
        )
    version = session.scalar(
        select(DocumentVersion).where(
            DocumentVersion.tenant_id == ctx.tenant_id,
            DocumentVersion.document_id == doc.id,
            DocumentVersion.version_no == doc.current_version_no,
        )
    )
    matching_extraction = (
        session.scalar(
            select(DocumentExtraction.id).where(
                DocumentExtraction.tenant_id == ctx.tenant_id,
                DocumentExtraction.id == field.extraction_id,
                DocumentExtraction.version_id == version.id,
            )
        )
        if version is not None
        else None
    )
    if version is None or matching_extraction is None:
        raise AppError(
            ErrorCode.VERSION_CONFLICT,
            "This field belongs to an earlier document version. "
            "Reload after the current version is processed.",
        )
    typed = typed_field(
        field.field_key, body.value, {"page": field.page, "row": field.row, "section": field.section}
    )
    field.is_current = False
    session.flush()
    corrected = DocumentField(
        tenant_id=ctx.tenant_id,
        extraction_id=field.extraction_id,
        document_id=doc.id,
        field_key=field.field_key,
        value_text=None,
        value_type=field.value_type,
        value_state="PRESENT",
        currency=field.currency,
        page=field.page,
        row=field.row,
        section=field.section,
        validation_status=typed.validation_status,
        confidence_note="analyst correction; the source document is unchanged",
        origin="CORRECTED",
        supersedes_id=field.id,
        is_current=True,
        corrected_by=ctx.principal_id,
        correction_reason=body.reason,
    )
    session.add(corrected)
    session.flush()

    session.add(
        DocumentFieldPayload(
            tenant_id=ctx.tenant_id,
            field_id=corrected.id,
            value_text=typed.value_text,
        )
    )
    session.flush()

    audit.record(
        session,
        tenant_id=ctx.tenant_id,
        action="DOCUMENT_FIELD_CORRECTED",
        object_type="document_field",
        object_id=str(corrected.id),
        outcome="SUCCESS",
        actor_principal_id=ctx.principal_id,
        actor_kind=ctx.kind.value,
        case_id=case.id,
        details={
            "document_id": str(doc.id),
            "field_key": field.field_key,
            "superseded_field_id": str(field.id),
            "reason": body.reason,
        },
    )
    record_document_evidence(session, doc, version, current_fields(session, doc), Actor.of(ctx))
    session.flush()
    return field_view(corrected)
