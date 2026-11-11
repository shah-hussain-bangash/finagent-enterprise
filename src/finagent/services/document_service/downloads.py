"""Short-lived, scoped download links (SPR-012, SPR-011, FR-002).

A link is an HMAC-signed token naming tenant, principal, document and exact version, valid for
`download_link_ttl_seconds`. Redeeming it requires the SAME authenticated principal and re-checks
CURRENT permission and case scope at download time, so a link that leaks, or outlives a
reassignment, is useless. Quarantined and unscanned content is never downloadable.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from finagent.core.config import Settings
from finagent.core.errors import AppError, ErrorCode, not_found
from finagent.database.models import DocumentVersion
from finagent.documents.storage import BlobNotFoundError, BlobStore
from finagent.security.authorization.policy import Permission, PrincipalContext, require
from finagent.services.audit_service import writer as audit
from finagent.services.document_service.queries import load_scoped_document

_PURPOSE = b"finagent:download-links:v1"
NOT_DOWNLOADABLE = frozenset({"QUARANTINED", "RECEIVED"})


@dataclass(frozen=True)
class Download:
    data: bytes
    media_type: str
    filename: str


def _key(settings: Settings) -> bytes:
    return hmac.new(settings.signing_key(), _PURPOSE, hashlib.sha256).digest()  # purpose-separated key


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def issue_link(
    session: Session, ctx: PrincipalContext, raw_document_id: str, settings: Settings
) -> dict[str, Any]:
    require(ctx, Permission.DOCUMENT_DOWNLOAD)
    found = load_scoped_document(session, ctx, raw_document_id)
    if found is None:
        raise not_found()
    doc, case = found
    version = session.scalar(
        select(DocumentVersion).where(
            DocumentVersion.tenant_id == ctx.tenant_id,
            DocumentVersion.document_id == doc.id,
            DocumentVersion.version_no == doc.current_version_no,
        )
    )
    if version is None or version.status in NOT_DOWNLOADABLE:
        raise AppError(
            ErrorCode.RESOURCE_UNAVAILABLE,
            "This document version is quarantined or not yet scanned and cannot be downloaded.",
        )
    expires = datetime.now(UTC) + timedelta(seconds=settings.download_link_ttl_seconds)
    claims = {
        "v": 1,
        "t": str(ctx.tenant_id),
        "p": str(ctx.principal_id),
        "d": str(doc.id),
        "dv": str(version.id),
        "exp": int(expires.timestamp()),
        "n": secrets.token_hex(8),
    }
    body = _b64(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode())
    token = body + "." + _b64(hmac.new(_key(settings), body.encode(), hashlib.sha256).digest())
    audit.record(
        session,
        tenant_id=ctx.tenant_id,
        action="DOCUMENT_LINK_ISSUED",
        object_type="document_version",
        object_id=str(version.id),
        outcome="SUCCESS",
        actor_principal_id=ctx.principal_id,
        actor_kind=ctx.kind.value,
        case_id=case.id,
        details={"document_id": str(doc.id), "expires_at": expires.isoformat()},
    )
    return {
        "url": f"/api/v1/document-downloads/{token}",
        "expires_at": expires.isoformat(),
        "version_no": version.version_no,
    }


def redeem(
    session: Session, ctx: PrincipalContext, token: str, settings: Settings, store: BlobStore
) -> Download:
    body, _, sig = token.partition(".")
    try:
        expected = hmac.new(_key(settings), body.encode(), hashlib.sha256).digest()
        if not sig or not hmac.compare_digest(expected, _unb64(sig)):
            raise not_found()
        claims = json.loads(_unb64(body))
    except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
        raise not_found() from exc
    if claims.get("t") != str(ctx.tenant_id) or claims.get("p") != str(ctx.principal_id):
        raise not_found()  # bound to one principal: a forwarded link reveals nothing
    if int(claims.get("exp", 0)) < int(datetime.now(UTC).timestamp()):
        raise AppError(ErrorCode.LINK_EXPIRED, "This download link has expired. Request a new one.")
    require(ctx, Permission.DOCUMENT_DOWNLOAD)  # CURRENT rights, not the rights at issue time
    found = load_scoped_document(session, ctx, str(claims.get("d", "")))
    if found is None:
        raise not_found()
    doc, case = found
    try:
        version_id = uuid.UUID(str(claims.get("dv")))
    except ValueError as exc:
        raise not_found() from exc
    version = session.scalar(
        select(DocumentVersion).where(
            DocumentVersion.tenant_id == ctx.tenant_id,
            DocumentVersion.document_id == doc.id,
            DocumentVersion.id == version_id,
        )
    )
    if version is None or version.status in NOT_DOWNLOADABLE or not version.storage_key:
        raise not_found()
    try:
        data = store.get(version.storage_key)
    except BlobNotFoundError as exc:
        raise AppError(ErrorCode.DEPENDENCY_UNAVAILABLE, "The stored file is unavailable.") from exc
    if hashlib.sha256(data).hexdigest() != version.sha256:
        raise AppError(ErrorCode.DEPENDENCY_UNAVAILABLE, "The stored file failed its integrity check.")
    audit.record(
        session,
        tenant_id=ctx.tenant_id,
        action="DOCUMENT_DOWNLOADED",
        object_type="document_version",
        object_id=str(version.id),
        outcome="SUCCESS",
        actor_principal_id=ctx.principal_id,
        actor_kind=ctx.kind.value,
        case_id=case.id,
        details={"document_id": str(doc.id), "sha256": version.sha256},
    )
    return Download(data, version.detected_media_type or "application/octet-stream", doc.title)
