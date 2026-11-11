"""Accept document bytes: validate, scan, quarantine or store (FR-013, DIR-014, DIR-025).

Order: declared type supported? -> signature agrees? -> malware scan -> store (encrypted, platform
key) -> QUARANTINED or STORED. Nothing is parsed here; parsing happens later in the isolated worker
only for STORED versions. Suspicious bytes are kept in a separate quarantine namespace so the
source is preserved for investigation, but they can never be parsed or downloaded.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints
from sqlalchemy import select
from sqlalchemy.orm import Session

from finagent.core.correlation import get_correlation_id
from finagent.core.errors import AppError, ErrorCode, not_found
from finagent.database.models import Document, DocumentVersion, OutboxEvent, QuarantineRecord
from finagent.documents import formats
from finagent.documents.scanner import MalwareScanner, ScanVerdict
from finagent.documents.storage import BlobStore, new_key
from finagent.domain.fingerprint import fingerprint
from finagent.domain.states import CASE_TERMINAL, CaseStatus
from finagent.security.authorization.policy import Permission, PrincipalContext, require
from finagent.services import idempotency
from finagent.services.audit_service import writer as audit
from finagent.services.case_service.queries import load_scoped_case
from finagent.services.case_service.transitions import Actor

DocumentType = Literal["PASSPORT", "PROOF_OF_ADDRESS", "BANK_STATEMENT", "SOURCE_OF_FUNDS", "OTHER"]
_UNSAFE = re.compile(r"[^A-Za-z0-9._ ()-]")
UPLOAD_OPERATION = "documents.upload"


class UploadMeta(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: uuid.UUID
    document_type: DocumentType
    filename: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]


@dataclass(frozen=True)
class DocumentPorts:
    store: BlobStore
    scanner: MalwareScanner


def safe_title(filename: str) -> str:
    """Display name only: never used for storage paths (keys are generated)."""
    base = filename.replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = _UNSAFE.sub("_", base).strip(" .")
    return (cleaned or "document")[:200]


def _quarantine_reason(fmt: formats.FormatCheck, verdict: ScanVerdict) -> tuple[str, str] | None:
    if verdict is ScanVerdict.INFECTED:
        return "MALWARE_DETECTED", "The malware scanner flagged this file."
    if verdict is ScanVerdict.ERROR:
        return "SCAN_ERROR", "The file could not be scanned; it is held until it can be."
    if not fmt.ok and fmt.reason:
        messages = {
            "SIGNATURE_MISMATCH": "The file content does not match its declared type.",
            "ACTIVE_CONTENT_MACROS": "The document contains macros (active content).",
            "ENCRYPTED_OR_LEGACY_CONTAINER": "The file is encrypted or a legacy Office container.",
            "UNSUPPORTED_MEDIA_TYPE": "The file type is not supported.",
        }
        return fmt.reason, messages.get(fmt.reason, "The file failed validation.")
    return None


def store_version(
    session: Session,
    *,
    document: Document,
    version_no: int,
    declared: str,
    data: bytes,
    actor: Actor,
    ports: DocumentPorts,
) -> DocumentVersion:
    """Scan + store one version of `document` in the caller's transaction."""
    fmt = formats.check(declared, data)
    scan = ports.scanner.scan(data)
    quarantine = _quarantine_reason(fmt, scan.verdict)
    key = new_key(document.tenant_id, "quarantine" if quarantine else "documents")
    ports.store.put(key, data)  # encrypted at rest; tenant-scoped generated key
    now = datetime.now(UTC)
    version = DocumentVersion(
        tenant_id=document.tenant_id,
        document_id=document.id,
        version_no=version_no,
        declared_media_type=fmt.declared[:120],
        detected_media_type=fmt.declared if fmt.ok else None,
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        storage_key=key,
        status="QUARANTINED" if quarantine else "STORED",
        status_reason=quarantine[0] if quarantine else None,
        scan_result=scan.verdict.value,
        scan_engine=scan.engine,
        scanned_at=now,
        created_by=actor.label,
    )
    session.add(version)
    session.flush()
    if quarantine:
        session.add(
            QuarantineRecord(
                tenant_id=document.tenant_id,
                case_id=document.case_id,
                document_version_id=version.id,
                reason_code=quarantine[0],
                detail=quarantine[1],
            )
        )
    audit.record(
        session,
        tenant_id=document.tenant_id,
        action="DOCUMENT_QUARANTINED" if quarantine else "DOCUMENT_STORED",
        object_type="document_version",
        object_id=str(version.id),
        outcome="QUARANTINED" if quarantine else "SUCCESS",
        actor_principal_id=actor.principal_id,
        actor_kind=actor.kind,
        case_id=document.case_id,
        details={
            "document_id": str(document.id),
            "version_no": version_no,
            "document_type": document.document_type,
            "sha256": version.sha256,
            "size_bytes": version.size_bytes,
            "scan": scan.verdict.value,
            "scan_engine": scan.engine,
            "signature": scan.signature,
            "reason": quarantine[0] if quarantine else None,
        },
    )
    session.add(
        OutboxEvent(
            tenant_id=document.tenant_id,
            event_type="document.received.v1",
            schema_version="1",
            resource_id=str(version.id),
            correlation_id=get_correlation_id(),
            payload={
                "document_id": str(document.id),
                "version_id": str(version.id),
                "case_id": str(document.case_id),
                "status": version.status,
            },
        )
    )
    if quarantine:
        from finagent.services.document_service.processing import record_document_evidence

        record_document_evidence(session, document, version, [], actor)
    session.flush()
    return version


def _check_case_open(status: str) -> None:
    if CaseStatus(status) in CASE_TERMINAL:
        raise AppError(ErrorCode.CASE_INVALID_STATE, "Completed or cancelled cases cannot be changed.")


def _check_supported(declared: str) -> None:
    if formats.normalize_media_type(declared) not in formats.SUPPORTED_MEDIA_TYPES:
        raise AppError(
            ErrorCode.UNSUPPORTED_MEDIA_TYPE,
            "Only PDF, DOCX and CSV documents are accepted.",
            {"supported": sorted(formats.SUPPORTED_MEDIA_TYPES)},
        )


def upload(
    session: Session,
    ctx: PrincipalContext,
    meta: UploadMeta,
    declared: str,
    data: bytes,
    key: str,
    retention_days: int,
    ports: DocumentPorts,
) -> tuple[int, dict[str, Any]]:
    from finagent.services.document_service.queries import document_view

    require(ctx, Permission.DOCUMENT_UPLOAD)
    sha = hashlib.sha256(data).hexdigest()
    request_hash = fingerprint({**meta.model_dump(mode="json"), "sha256": sha, "declared": declared})
    idem = idempotency.begin(session, ctx.tenant_id, UPLOAD_OPERATION, key, request_hash, retention_days)
    if idem.replay and idem.record.status_code is not None and idem.record.response is not None:
        return idem.record.status_code, idem.record.response
    case = load_scoped_case(session, ctx, str(meta.case_id), for_update=True)
    if case is None:
        raise not_found()
    _check_case_open(case.status)
    _check_supported(declared)
    actor = Actor.of(ctx)
    doc = Document(
        tenant_id=ctx.tenant_id,
        case_id=case.id,
        document_type=meta.document_type,
        title=safe_title(meta.filename),
        source="UPLOAD",
        created_by=actor.label,
    )
    session.add(doc)
    session.flush()
    store_version(session, document=doc, version_no=1, declared=declared, data=data, actor=actor, ports=ports)
    body = document_view(session, doc)
    idem.finish(201, body, str(doc.id))
    return 201, body


def upload_version(
    session: Session,
    ctx: PrincipalContext,
    raw_document_id: str,
    declared: str,
    data: bytes,
    key: str,
    retention_days: int,
    ports: DocumentPorts,
) -> tuple[int, dict[str, Any]]:
    """Corrected re-upload: a NEW version; earlier versions and their failure history stay."""
    from finagent.services.document_service.queries import document_view, load_scoped_document

    require(ctx, Permission.DOCUMENT_UPLOAD)
    sha = hashlib.sha256(data).hexdigest()
    request_hash = fingerprint({"document_id": raw_document_id, "sha256": sha, "declared": declared})
    idem = idempotency.begin(
        session, ctx.tenant_id, UPLOAD_OPERATION + ".version", key, request_hash, retention_days
    )
    if idem.replay and idem.record.status_code is not None and idem.record.response is not None:
        return idem.record.status_code, idem.record.response
    found = load_scoped_document(session, ctx, raw_document_id, for_update=True)
    if found is None:
        raise not_found()
    doc, case = found
    _check_case_open(case.status)
    _check_supported(declared)
    if doc.source != "UPLOAD":
        raise AppError(
            ErrorCode.SEMANTIC_VALIDATION_FAILED,
            "Connector documents are refreshed from their source system, not re-uploaded.",
        )
    doc.current_version_no += 1
    doc.record_version += 1
    doc.updated_at = datetime.now(UTC)
    store_version(
        session,
        document=doc,
        version_no=doc.current_version_no,
        declared=declared,
        data=data,
        actor=Actor.of(ctx),
        ports=ports,
    )
    body = document_view(session, doc)
    idem.finish(201, body, str(doc.id))
    return 201, body


def ingest_connector_document(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    case_id: uuid.UUID,
    source_ref: str,
    document_type: str,
    classification: str,
    customer_ref: str,
    media_type: str,
    data: bytes,
    actor: Actor,
    ports: DocumentPorts,
) -> DocumentVersion:
    """Workflow path: a KYC document fetched through the tool gateway. Same bytes -> same version."""
    doc = session.scalar(
        select(Document)
        .where(
            Document.tenant_id == tenant_id,
            Document.case_id == case_id,
            Document.source == "CONNECTOR",
            Document.source_ref == source_ref,
        )
        .with_for_update()
    )
    sha = hashlib.sha256(data).hexdigest()
    if doc is not None:
        latest = session.scalar(
            select(DocumentVersion).where(
                DocumentVersion.tenant_id == tenant_id,
                DocumentVersion.document_id == doc.id,
                DocumentVersion.version_no == doc.current_version_no,
            )
        )
        if latest is not None and latest.sha256 == sha:
            return latest
        doc.current_version_no += 1
        doc.record_version += 1
        doc.updated_at = datetime.now(UTC)
    else:
        doc = Document(
            tenant_id=tenant_id,
            case_id=case_id,
            document_type=document_type[:48],
            title=safe_title(source_ref),
            source="CONNECTOR",
            source_ref=source_ref[:200],
            subject_ref=customer_ref[:200],
            classification=classification[:32],
            created_by=actor.label,
        )
        session.add(doc)
        session.flush()
    return store_version(
        session,
        document=doc,
        version_no=doc.current_version_no,
        declared=media_type,
        data=data,
        actor=actor,
        ports=ports,
    )
