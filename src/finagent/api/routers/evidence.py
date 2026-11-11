"""Document and evidence endpoints (SRS §12; prompt §22; FR-013..016, SPR-012).

Uploads are raw bodies (the declared type is the Content-Type header; metadata are query
parameters). Authentication and authorization run BEFORE the body is read, and the body is read
with a hard byte counter, so a missing or false Content-Length cannot bypass the size limit.
"""

from __future__ import annotations

import re
from datetime import timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Header, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from finagent.api.deps import AppSettings, Factory, IdempotencyKey, Principal, charge
from finagent.core.errors import AppError, ErrorCode, not_found
from finagent.database.session import transaction
from finagent.security.authorization.policy import Permission, require
from finagent.services.case_service.queries import load_scoped_case
from finagent.services.case_service.transitions import Actor
from finagent.services.document_service import corrections, downloads, ingest
from finagent.services.document_service import queries as doc_queries
from finagent.services.document_service.corrections import FieldCorrection
from finagent.services.document_service.ingest import DocumentPorts, UploadMeta
from finagent.services.evidence_service import conflicts as conflict_service
from finagent.services.evidence_service import queries as evidence_queries
from finagent.services.evidence_service.conflicts import ConflictResolution
from finagent.services.evidence_service.queries import snapshot_view
from finagent.services.evidence_service.snapshots import create_snapshot

evidence = APIRouter(prefix="/api/v1")
UPLOAD_COST = 10  # quota units: uploads are expensive (NQ-028)
_DISPOSITION = re.compile(r"[^A-Za-z0-9._ ()-]")


def _ports(request: Request) -> DocumentPorts:
    ports: DocumentPorts = request.app.state.document_ports
    return ports


def _max_age(settings: AppSettings) -> timedelta:
    return timedelta(hours=settings.source_max_age_hours)


async def read_limited(request: Request, limit: int) -> bytes:
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise AppError(ErrorCode.PAYLOAD_TOO_LARGE, f"Documents are limited to {limit} bytes.")
    buf = bytearray()
    async for chunk in request.stream():
        buf += chunk
        if len(buf) > limit:  # enforced on actual bytes, not on the header
            raise AppError(ErrorCode.PAYLOAD_TOO_LARGE, f"Documents are limited to {limit} bytes.")
    if not buf:
        raise AppError(ErrorCode.SCHEMA_VALIDATION_FAILED, "The upload body is empty.")
    return bytes(buf)


@evidence.post("/documents", tags=["documents"], status_code=201)
async def upload_document(
    request: Request,
    ctx: Principal,
    factory: Factory,
    key: IdempotencyKey,
    settings: AppSettings,
    case_id: Annotated[str, Query()],
    document_type: Annotated[str, Query()],
    filename: Annotated[str, Query()],
    content_type: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    """Upload a PDF, DOCX or CSV (raw body). 201 with processing status; 413 / 415 on limits."""
    require(ctx, Permission.DOCUMENT_UPLOAD)  # before reading a single body byte
    try:
        meta = UploadMeta.model_validate(
            {"case_id": case_id, "document_type": document_type, "filename": filename}
        )
    except ValidationError as exc:
        fields = [{"loc": [str(p) for p in e["loc"]], "type": e["type"]} for e in exc.errors()]
        raise AppError(
            ErrorCode.SCHEMA_VALIDATION_FAILED, "Invalid upload metadata.", {"errors": fields}
        ) from exc
    charge(request, ctx, UPLOAD_COST)
    data = await read_limited(request, settings.document_max_bytes)
    ports = _ports(request)

    def work() -> tuple[int, dict[str, Any]]:
        with transaction(factory) as s:
            return ingest.upload(
                s, ctx, meta, content_type or "", data, key, settings.idempotency_retention_days, ports
            )

    status, body = await run_in_threadpool(work)
    return JSONResponse(status_code=status, content=body)


@evidence.post("/documents/{document_id}/versions", tags=["documents"], status_code=201)
async def upload_document_version(
    document_id: str,
    request: Request,
    ctx: Principal,
    factory: Factory,
    key: IdempotencyKey,
    settings: AppSettings,
    content_type: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    """Corrected re-upload as a new version; earlier versions and failures are preserved."""
    require(ctx, Permission.DOCUMENT_UPLOAD)
    charge(request, ctx, UPLOAD_COST)
    data = await read_limited(request, settings.document_max_bytes)
    ports = _ports(request)

    def work() -> tuple[int, dict[str, Any]]:
        with transaction(factory) as s:
            return ingest.upload_version(
                s, ctx, document_id, content_type or "", data, key, settings.idempotency_retention_days, ports
            )

    status, body = await run_in_threadpool(work)
    return JSONResponse(status_code=status, content=body)


@evidence.get("/documents/{document_id}", tags=["documents"])
def get_document(document_id: str, ctx: Principal, factory: Factory) -> dict[str, Any]:
    with transaction(factory) as s:
        return doc_queries.get_document(s, ctx, document_id)


@evidence.post("/documents/{document_id}/fields/{field_id}/correction", tags=["documents"], status_code=201)
def correct_field(
    document_id: str, field_id: str, body: FieldCorrection, ctx: Principal, factory: Factory
) -> dict[str, Any]:
    with transaction(factory) as s:
        return corrections.correct_field(s, ctx, document_id, field_id, body)


@evidence.get("/documents/{document_id}/download-link", tags=["documents"])
def get_download_link(
    document_id: str, ctx: Principal, factory: Factory, settings: AppSettings
) -> dict[str, Any]:
    with transaction(factory) as s:
        return downloads.issue_link(s, ctx, document_id, settings)


@evidence.get("/document-downloads/{token}", tags=["documents"])
def download(
    token: str, request: Request, ctx: Principal, factory: Factory, settings: AppSettings
) -> Response:
    with transaction(factory) as s:
        file = downloads.redeem(s, ctx, token, settings, _ports(request).store)
    name = _DISPOSITION.sub("_", file.filename)[:100] or "document"
    return Response(
        content=file.data,
        media_type=file.media_type,
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@evidence.get("/cases/{case_id}/documents", tags=["documents"])
def list_case_documents(case_id: str, ctx: Principal, factory: Factory) -> dict[str, Any]:
    with transaction(factory) as s:
        return doc_queries.list_case_documents(s, ctx, case_id)


@evidence.get("/cases/{case_id}/evidence", tags=["evidence"])
def get_case_evidence(
    case_id: str, ctx: Principal, factory: Factory, settings: AppSettings
) -> dict[str, Any]:
    with transaction(factory) as s:
        return evidence_queries.case_evidence(s, ctx, case_id, _max_age(settings))


@evidence.get("/cases/{case_id}/snapshots", tags=["evidence"])
def list_snapshots(case_id: str, ctx: Principal, factory: Factory) -> dict[str, Any]:
    with transaction(factory) as s:
        return evidence_queries.list_snapshots(s, ctx, case_id)


@evidence.post("/cases/{case_id}/snapshots", tags=["evidence"], status_code=201)
def post_snapshot(case_id: str, ctx: Principal, factory: Factory, settings: AppSettings) -> JSONResponse:
    """Freeze the current evidence set. Returns 200 with the latest snapshot if nothing changed."""
    require(ctx, Permission.CASE_UPDATE)
    with transaction(factory) as s:
        case = load_scoped_case(s, ctx, case_id)
        if case is None:
            raise not_found()
        snap, created = create_snapshot(
            s,
            tenant_id=ctx.tenant_id,
            case_id=case.id,
            created_by=Actor.of(ctx).label,
            max_age=_max_age(settings),
            actor_principal_id=ctx.principal_id,
            actor_kind=ctx.kind.value,
        )
        body = snapshot_view(snap)
    return JSONResponse(status_code=201 if created else 200, content=body)


@evidence.get("/cases/{case_id}/snapshots/{snapshot_id}", tags=["evidence"])
def get_snapshot(case_id: str, snapshot_id: str, ctx: Principal, factory: Factory) -> dict[str, Any]:
    with transaction(factory) as s:
        return evidence_queries.get_snapshot(s, ctx, case_id, snapshot_id)


@evidence.post("/cases/{case_id}/evidence-conflicts/{conflict_id}/resolution", tags=["evidence"])
def resolve_conflict(
    case_id: str, conflict_id: str, body: ConflictResolution, ctx: Principal, factory: Factory
) -> dict[str, Any]:
    with transaction(factory) as s:
        return conflict_service.resolve(s, ctx, case_id, conflict_id, body)
