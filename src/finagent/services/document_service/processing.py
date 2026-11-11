"""Asynchronous document processing (FR-013, FR-014, AER-017, NQ-023).

The worker claims STORED versions (and retry-due or abandoned ones) with SKIP LOCKED, so two
workers never parse the same version. The claim increments `attempts`; the result is committed
only if the row is still PARSING with the same attempt number, which fences out a stale worker.

Status mapping (parser result -> version status):
  PARSED                         -> PARSED
  OCR_UNAVAILABLE                -> OCR_UNAVAILABLE   (actionable; source preserved)
  ENCRYPTED / ACTIVE_CONTENT     -> QUARANTINED      (DATA-D04 default for encrypted files)
  LIMIT_EXCEEDED / CORRUPT / UNSUPPORTED_CONTENT -> PARSE_FAILED, not retried (permanent)
  TIMEOUT / CRASHED              -> PARSE_FAILED, retried with backoff up to the attempt limit
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.orm.attributes import set_committed_value

from finagent.core.correlation import get_correlation_id
from finagent.database.models import (
    Document,
    DocumentExtraction,
    DocumentExtractionPayload,
    DocumentField,
    DocumentFieldPayload,
    DocumentVersion,
    OutboxEvent,
    QuarantineRecord,
)
from finagent.database.session import transaction
from finagent.documents import parsers
from finagent.documents.extraction import PROFILE_VERSION, ExtractedField, extract
from finagent.documents.ocr import NoOcrProvider, OcrPort
from finagent.documents.sandbox import SandboxConfig, run_parser
from finagent.documents.storage import BlobNotFoundError, BlobStore
from finagent.services.audit_service import writer as audit
from finagent.services.case_service.transitions import Actor
from finagent.services.evidence_service.items import EvidenceInput, record_item

log = logging.getLogger("finagent.documents")
RETRYABLE = frozenset({parsers.TIMEOUT, parsers.CRASHED})
QUARANTINE = {parsers.ENCRYPTED: "ENCRYPTED_FILE", parsers.ACTIVE_CONTENT: "ACTIVE_CONTENT"}
_ACTIONS = {
    "PARSED": None,
    "OCR_UNAVAILABLE": "No text layer and no OCR provider is configured: upload a text-based copy.",
    "QUARANTINED": "Held in quarantine; a supervisor must review. Upload a clean copy as a new version.",
    "PARSE_FAILED": "The file could not be read. The original is preserved; upload a corrected version.",
}


@dataclass(frozen=True)
class ProcessingConfig:
    sandbox: SandboxConfig
    max_attempts: int
    ocr: OcrPort = field(default_factory=NoOcrProvider)
    stale_after: timedelta = timedelta(minutes=10)
    retry_base: timedelta = timedelta(seconds=30)


def next_action(status: str) -> str | None:
    return _ACTIONS.get(status)


def field_view(f: DocumentField) -> dict[str, Any]:
    return {
        "field_id": str(f.id),
        "field_key": f.field_key,
        "value": f.value_text,
        "value_type": f.value_type,
        "value_state": f.value_state,
        "validation_status": f.validation_status,
        "currency": f.currency,
        "page": f.page,
        "row": f.row,
        "section": f.section,
        "confidence_note": f.confidence_note,
        "origin": f.origin,
        "supersedes_id": str(f.supersedes_id) if f.supersedes_id else None,
        "is_current": f.is_current,
        "correction_reason": f.correction_reason,
        "created_at": f.created_at.isoformat() if f.created_at else None,
    }


def hydrate_field_payloads(
    session: Session,
    fields: list[DocumentField],
) -> list[DocumentField]:
    """Prefer purgeable field payloads with legacy inline fallback."""
    if not fields:
        return fields

    field_ids = [field.id for field in fields]
    payloads = {
        payload.field_id: payload
        for payload in session.scalars(
            select(DocumentFieldPayload).where(DocumentFieldPayload.field_id.in_(field_ids))
        )
    }

    for document_field in fields:
        payload = payloads.get(document_field.id)
        if payload is not None:
            set_committed_value(
                document_field,
                "value_text",
                payload.value_text,
            )

    return fields


def current_fields(session: Session, document: Document) -> list[DocumentField]:
    fields = list(
        session.scalars(
            select(DocumentField)
            .where(
                DocumentField.tenant_id == document.tenant_id,
                DocumentField.document_id == document.id,
                DocumentField.is_current.is_(True),
            )
            .order_by(DocumentField.seq)
        )
    )
    return hydrate_field_payloads(session, fields)


def record_document_evidence(
    session: Session, document: Document, version: DocumentVersion, fields: list[DocumentField], actor: Actor
) -> None:
    """The document's typed fields become (versioned) case evidence; corrections are visible."""
    content = {
        "document_id": str(document.id),
        "document_type": document.document_type,
        "title": document.title,
        "source": document.source,
        "source_ref": document.source_ref,
        "customer_ref": document.subject_ref,
        "version_no": version.version_no,
        "version_id": str(version.id),
        "sha256": version.sha256,
        "media_type": version.detected_media_type or version.declared_media_type,
        "status": version.status,
        "status_reason": version.status_reason,
        "extraction_profile": PROFILE_VERSION,
        "fields": [
            {k: v for k, v in field_view(f).items() if k not in ("created_at", "is_current")} for f in fields
        ],
    }
    record_item(
        session,
        tenant_id=document.tenant_id,
        case_id=document.case_id,
        data=EvidenceInput(
            source_type="DOCUMENT_EXTRACTION",
            source_ref=str(document.id),
            content=content,
            source_system="document-intelligence" if document.source == "UPLOAD" else "core-banking",
            retrieved_at=datetime.now(UTC),
            as_of=None,
            freshness="UNKNOWN",
            completeness="COMPLETE" if version.status == "PARSED" else "INCOMPLETE",
            classification=document.classification,
            document_version_id=version.id,
        ),
        created_by=actor.label,
        actor_principal_id=actor.principal_id,
        actor_kind=actor.kind,
    )


def claim(
    factory: sessionmaker[Session], cfg: ProcessingConfig, limit: int = 5
) -> list[tuple[uuid.UUID, int]]:
    now = datetime.now(UTC)
    with transaction(factory) as s:
        rows = s.scalars(
            select(DocumentVersion)
            .where(
                or_(
                    DocumentVersion.status == "STORED",
                    and_(
                        DocumentVersion.status == "PARSE_FAILED",
                        DocumentVersion.next_attempt_at.is_not(None),
                        DocumentVersion.next_attempt_at <= now,
                        DocumentVersion.attempts < cfg.max_attempts,
                    ),
                    and_(
                        DocumentVersion.status == "PARSING",
                        DocumentVersion.updated_at < now - cfg.stale_after,  # abandoned by a crashed worker
                    ),
                )
            )
            .order_by(DocumentVersion.created_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        ).all()
        claimed = []
        for v in rows:
            v.status, v.attempts, v.updated_at, v.next_attempt_at = "PARSING", v.attempts + 1, now, None
            claimed.append((v.id, v.attempts))
        return claimed


def _load(s: Session, version_id: uuid.UUID, attempt: int) -> tuple[DocumentVersion, Document] | None:
    version = s.scalar(select(DocumentVersion).where(DocumentVersion.id == version_id).with_for_update())
    if version is None or version.status != "PARSING" or version.attempts != attempt:
        return None  # another worker took over (fenced by the attempt number)
    doc = s.scalar(
        select(Document).where(Document.tenant_id == version.tenant_id, Document.id == version.document_id)
    )
    return (version, doc) if doc is not None else None


def _ocr_if_required(
    result: dict[str, Any],
    media_type: str,
    data: bytes,
    cfg: ProcessingConfig,
) -> dict[str, Any]:
    """Offer only text-layer failures to the configured OCR provider.

    Disabled/unavailable/failed OCR preserves the parser's original
    OCR_UNAVAILABLE result. An OCR provider is allowed to produce PARSED only
    when it returns at least one non-empty page.
    """

    if result.get("status") != parsers.OCR_UNAVAILABLE:
        return result

    ocr = cfg.ocr.extract(
        media_type,
        data,
        max_pages=cfg.sandbox.limits.max_pages,
    )

    if ocr.status != parsers.PARSED:
        return result

    pages = tuple(
        page for page in ocr.pages if 1 <= page.page <= cfg.sandbox.limits.max_pages and page.text.strip()
    )

    if not pages:
        return result

    if len(pages) > cfg.sandbox.limits.max_pages:
        return result

    return {
        **result,
        "status": parsers.PARSED,
        "reason": None,
        "parser": f"ocr:{ocr.provider}",
        "page_count": max(page.page for page in pages),
        "segments": [
            {
                "kind": "page",
                "page": page.page,
                "section": None,
                "row": None,
                "text": page.text,
            }
            for page in pages
        ],
        "warnings": list(ocr.warnings),
        "ocr_provider": ocr.provider,
    }


def process_one(
    factory: sessionmaker[Session],
    store: BlobStore,
    cfg: ProcessingConfig,
    version_id: uuid.UUID,
    attempt: int,
) -> str:
    with transaction(factory) as s:
        loaded = _load(s, version_id, attempt)
        if loaded is None:
            return "SKIPPED"
        version, _ = loaded
        key, sha, media = version.storage_key, version.sha256, version.detected_media_type
    try:
        data = store.get(key or "")
        integrity_ok = hashlib.sha256(data).hexdigest() == sha
    except (BlobNotFoundError, ValueError):
        data, integrity_ok = b"", False
    if not integrity_ok:
        result: dict[str, Any] = {
            "status": parsers.CORRUPT,
            "reason": "STORED_CONTENT_INTEGRITY",
            "segments": [],
        }
        result |= {"parser": "integrity-check", "warnings": []}
    else:
        result = run_parser(media or "", data, cfg.sandbox)
        result = _ocr_if_required(result, media or "", data, cfg)
    return _commit(factory, cfg, version_id, attempt, result)


def _commit(
    factory: sessionmaker[Session],
    cfg: ProcessingConfig,
    version_id: uuid.UUID,
    attempt: int,
    result: dict[str, Any],
) -> str:
    actor = Actor.system("document-processor")
    parse_status = str(result.get("status"))
    now = datetime.now(UTC)
    with transaction(factory) as s:
        loaded = _load(s, version_id, attempt)
        if loaded is None:
            return "SKIPPED"
        version, doc = loaded
        retry = parse_status in RETRYABLE and attempt < cfg.max_attempts
        if parse_status == parsers.PARSED:
            status, reason = "PARSED", None
        elif parse_status == parsers.OCR_UNAVAILABLE:
            status, reason = "OCR_UNAVAILABLE", "NO_TEXT_LAYER"
        elif parse_status in QUARANTINE:
            status, reason = "QUARANTINED", QUARANTINE[parse_status]
        else:
            status, reason = "PARSE_FAILED", str(result.get("reason") or parse_status)[:64]
        version.status, version.status_reason, version.updated_at = status, reason, now
        version.last_error_code = None if status == "PARSED" else (reason or parse_status)[:64]
        version.next_attempt_at = now + cfg.retry_base * (2 ** (attempt - 1)) if retry else None
        extraction = DocumentExtraction(
            tenant_id=version.tenant_id,
            version_id=version.id,
            parser=str(result.get("parser") or "unknown")[:64],
            extraction_profile=PROFILE_VERSION,
            status=parse_status[:32],
            page_count=result.get("page_count"),
            row_count=result.get("row_count"),
            segments=None,
            warnings=[str(w)[:100] for w in (result.get("warnings") or [])][:200],
            limits={
                **cfg.sandbox.limits.as_dict(),
                "timeout_s": cfg.sandbox.timeout_s,
                "memory_bytes": cfg.sandbox.memory_bytes,
                "memory_limit_enforced": result.get("memory_limit_enforced"),
                "network_limit_enforced": result.get("network_limit_enforced"),
                "process_limit_enforced": result.get("process_limit_enforced"),
                "isolation_method": result.get("isolation_method"),
            },
        )
        s.add(extraction)
        s.flush()

        s.add(
            DocumentExtractionPayload(
                tenant_id=version.tenant_id,
                extraction_id=extraction.id,
                segments=list(result.get("segments") or []),
            )
        )
        s.flush()

        if status == "QUARANTINED":
            s.add(
                QuarantineRecord(
                    tenant_id=version.tenant_id,
                    case_id=doc.case_id,
                    document_version_id=version.id,
                    reason_code=reason or "SUSPICIOUS",
                    detail="Detected while parsing: "
                    + ("encrypted file" if reason == "ENCRYPTED_FILE" else "active content"),
                )
            )
        fields: list[DocumentField] = []
        if not retry:
            if version.version_no == doc.current_version_no:
                for old in current_fields(s, doc):
                    old.is_current = False
                s.flush()
                fields = _store_fields(s, doc, extraction, extract(doc.document_type, result))
            record_document_evidence(s, doc, version, fields, actor)
        audit.record(
            s,
            tenant_id=version.tenant_id,
            action="DOCUMENT_PROCESSED",
            object_type="document_version",
            object_id=str(version.id),
            outcome="SUCCESS" if status == "PARSED" else status,
            actor_principal_id=None,
            actor_kind="SYSTEM",
            case_id=doc.case_id,
            details={
                "document_id": str(doc.id),
                "status": status,
                "reason": reason,
                "attempt": attempt,
                "retry_scheduled": retry,
                "parser": extraction.parser,
                "fields": len(fields),
            },
        )
        s.add(
            OutboxEvent(
                tenant_id=version.tenant_id,
                event_type="document.processed.v1",
                schema_version="1",
                resource_id=str(version.id),
                correlation_id=get_correlation_id(),
                payload={"document_id": str(doc.id), "case_id": str(doc.case_id), "status": status},
            )
        )
        return status


def _store_fields(
    s: Session, doc: Document, extraction: DocumentExtraction, extracted: list[ExtractedField]
) -> list[DocumentField]:
    rows = [
        DocumentField(
            tenant_id=doc.tenant_id,
            extraction_id=extraction.id,
            document_id=doc.id,
            field_key=f.field_key[:64],
            value_text=None,
            value_type=f.value_type,
            value_state=f.value_state,
            currency=f.currency,
            page=f.page,
            row=f.row,
            section=(f.section or None) and f.section[:120],
            validation_status=f.validation_status,
            confidence_note=f.confidence_note[:300],
            origin="EXTRACTED",
            is_current=True,
        )
        for f in extracted
    ]
    s.add_all(rows)
    s.flush()

    s.add_all(
        [
            DocumentFieldPayload(
                tenant_id=row.tenant_id,
                field_id=row.id,
                value_text=source.value_text,
            )
            for row, source in zip(rows, extracted, strict=True)
        ]
    )
    s.flush()

    return hydrate_field_payloads(s, rows)


def process_pending(
    factory: sessionmaker[Session], store: BlobStore, cfg: ProcessingConfig, limit: int = 5
) -> list[tuple[str, str]]:
    out = []
    for version_id, attempt in claim(factory, cfg, limit):
        try:
            status = process_one(factory, store, cfg, version_id, attempt)
        except Exception as exc:  # leave it PARSING: the stale-claim rule retries it later
            log.error("document_processing_failed", extra={"error_type": type(exc).__name__})
            status = "ERROR"
        out.append((str(version_id), status))
    return out
