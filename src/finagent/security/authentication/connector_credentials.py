"""Short-lived, revocable credentials for registered SYNTHETIC local connectors.

An opaque token conveys no tenant, role, MFA or configurable scope. Its digest
maps to an existing integration principal, resolved normally on every request.
Issuance/revocation is a schema-owner operation, never a public API capability.
"""

from __future__ import annotations

import hashlib
import re
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from finagent.core.config import LOCAL_ONLY_ENVIRONMENTS, Settings
from finagent.core.errors import AppError, ErrorCode
from finagent.database.models import ConnectorCredential, Principal, Tenant
from finagent.security.authentication.resolver import resolve_principal_by_id
from finagent.security.authentication.tokens import VerifiedIdentity
from finagent.security.authorization.policy import Permission, PrincipalKind
from finagent.services.audit_service import writer as audit

SESSION_MARKER = "fa_int_"
_FORMAT = re.compile(r"^fa_int_[A-Za-z0-9_-]{43}$")
SCOPE = "alert:ingest"


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def require_local(settings: Settings, *, enabled: bool = True) -> None:
    if settings.environment not in LOCAL_ONLY_ENVIRONMENTS or (
        enabled and not settings.local_connector_auth_enabled
    ):
        raise AppError(ErrorCode.FEATURE_DISABLED)


def issue_local_credential(
    session: Session, settings: Settings, principal_id: UUID, *, ttl_seconds: int = 900, reason: str
) -> tuple[str, ConnectorCredential]:
    require_local(settings)
    if type(ttl_seconds) is not int or not 60 <= ttl_seconds <= 3600 or not 10 <= len(reason.strip()) <= 300:
        raise ValueError("A 60-3600 second lifetime and 10-300 character reason are required.")
    principal = session.get(Principal, principal_id)
    tenant = session.get(Tenant, principal.tenant_id) if principal else None
    if principal is None or tenant is None or not tenant.is_synthetic:
        raise AppError(ErrorCode.UNAUTHENTICATED)
    ctx = resolve_principal_by_id(session, tenant.id, principal.id)
    if ctx.kind is not PrincipalKind.INTEGRATION or Permission.ALERT_INGEST not in ctx.permissions:
        raise AppError(ErrorCode.UNAUTHENTICATED)
    token = SESSION_MARKER + secrets.token_urlsafe(32)
    now = datetime.now(UTC)
    row = ConnectorCredential(
        tenant_id=tenant.id,
        principal_id=principal.id,
        token_hash=digest(token),
        scope=SCOPE,
        created_at=now,
        expires_at=now + timedelta(seconds=ttl_seconds),
    )
    session.add(row)
    session.flush()
    audit.record(
        session,
        tenant_id=row.tenant_id,
        action="CONNECTOR_CREDENTIAL_ISSUED",
        object_type="connector_credential",
        object_id=str(row.id),
        outcome="SUCCESS",
        actor_principal_id=None,
        actor_kind="SYSTEM",
        details={"reason": reason.strip(), "ttl_seconds": ttl_seconds, "scope": SCOPE},
    )
    return token, row


def validate_credential(session: Session, settings: Settings, token: str) -> VerifiedIdentity:
    if (
        settings.environment not in LOCAL_ONLY_ENVIRONMENTS
        or not settings.local_connector_auth_enabled
        or not _FORMAT.fullmatch(token)
    ):
        raise AppError(ErrorCode.UNAUTHENTICATED)
    result = session.execute(
        select(ConnectorCredential, Principal, Tenant)
        .join(
            Principal,
            (Principal.id == ConnectorCredential.principal_id)
            & (Principal.tenant_id == ConnectorCredential.tenant_id),
        )
        .join(Tenant, Tenant.id == ConnectorCredential.tenant_id)
        .where(ConnectorCredential.token_hash == digest(token))
    ).one_or_none()
    if result is None:
        raise AppError(ErrorCode.UNAUTHENTICATED)
    row, principal, tenant = result
    now = datetime.now(UTC)
    if (
        row.revoked_at is not None
        or row.expires_at <= now
        or row.created_at > now
        or row.expires_at - row.created_at > timedelta(seconds=3600)
        or row.scope != SCOPE
        or principal.kind != "INTEGRATION"
        or principal.status != "ACTIVE"
        or tenant.status != "ACTIVE"
        or not tenant.is_synthetic
    ):
        raise AppError(ErrorCode.UNAUTHENTICATED)
    # The normal resolver still checks current roles and IntegrationConnection status.
    return VerifiedIdentity(issuer=principal.issuer, subject=principal.subject, mfa=False)


def revoke_local_credential(
    session: Session, settings: Settings, credential_id: UUID, *, reason: str
) -> None:
    require_local(settings, enabled=False)
    if not 10 <= len(reason.strip()) <= 300:
        raise ValueError("A 10-300 character reason is required.")
    row = session.scalar(
        select(ConnectorCredential).where(ConnectorCredential.id == credential_id).with_for_update()
    )
    if row is None or row.revoked_at is not None:
        return
    row.revoked_at = datetime.now(UTC)
    audit.record(
        session,
        tenant_id=row.tenant_id,
        action="CONNECTOR_CREDENTIAL_REVOKED",
        object_type="connector_credential",
        object_id=str(row.id),
        outcome="SUCCESS",
        actor_principal_id=None,
        actor_kind="SYSTEM",
        details={"reason": reason.strip()},
    )
