"""Local/test authenticator verification. Enrollment is a schema-owner operation.

RFC 6238 HMAC-SHA1, 30-second steps, six digits, +/- one step. Accepted counters
are consumed under a DB row lock. Only the current opaque password session can
receive a five-minute grant; no API accepts a client-supplied MFA claim.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import struct
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import select
from sqlalchemy.orm import Session

from finagent.core.config import LOCAL_ONLY_ENVIRONMENTS, Settings
from finagent.core.errors import AppError, ErrorCode
from finagent.database.models import PasswordCredential, PasswordSession, TotpFactor, TotpGrant
from finagent.security.authentication.passwords import token_digest, validate_session
from finagent.security.authentication.tokens import VerifiedIdentity
from finagent.services.audit_service import writer as audit

STEP_SECONDS = 30
GRANT_SECONDS = 300


def require_enabled(settings: Settings) -> None:
    if (
        not settings.local_totp_enabled
        or settings.environment not in LOCAL_ONLY_ENVIRONMENTS
        or not settings.password_login_enabled
        or settings.local_totp_key is None
    ):
        raise AppError(ErrorCode.FEATURE_DISABLED)


def otp(secret: bytes, counter: int, digits: int = 6) -> str:
    if len(secret) < 20 or counter < 0 or digits not in (6, 8):
        raise ValueError("Invalid authenticator parameters")
    # HMAC-SHA1 is the interoperable RFC 6238 primitive, not a password hash.
    digest = hmac.new(secret, struct.pack(">Q", counter), hashlib.sha1).digest()  # nosec B303
    offset = digest[-1] & 15
    value = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(value % (10**digits)).zfill(digits)


def matching_counter(secret: bytes, code: str, now: datetime, last: int = -1) -> int | None:
    if not re.fullmatch(r"[0-9]{6}", code):
        return None
    current = int(now.timestamp()) // STEP_SECONDS
    matches = [
        counter
        for counter in (current - 1, current, current + 1)
        if counter >= 0 and hmac.compare_digest(otp(secret, counter), code)
    ]
    return max(matches) if matches and max(matches) > last else None


def _aad(tenant_id: UUID, principal_id: UUID) -> bytes:
    return f"finagent-local-totp-v1:{tenant_id}:{principal_id}".encode("ascii")


def _cipher(settings: Settings) -> AESGCM:
    require_enabled(settings)
    if settings.local_totp_key is None:
        raise AppError(ErrorCode.FEATURE_DISABLED)
    return AESGCM(base64.b64decode(settings.local_totp_key.get_secret_value(), validate=True))


def seal(settings: Settings, tenant: UUID, principal: UUID, secret: bytes) -> str:
    nonce = secrets.token_bytes(12)
    raw = nonce + _cipher(settings).encrypt(nonce, secret, _aad(tenant, principal))
    return base64.b64encode(raw).decode("ascii")


def unseal(settings: Settings, factor: TotpFactor) -> bytes:
    try:
        raw = base64.b64decode(factor.encrypted_secret, validate=True)
        return _cipher(settings).decrypt(raw[:12], raw[12:], _aad(factor.tenant_id, factor.principal_id))
    except (ValueError, TypeError, InvalidTag) as error:
        raise AppError(ErrorCode.DEPENDENCY_UNAVAILABLE) from error


def _login(session: Session, token: str) -> tuple[PasswordSession, PasswordCredential]:
    validate_session(session, token)
    row = session.execute(
        select(PasswordSession, PasswordCredential)
        .join(
            PasswordCredential,
            (PasswordCredential.id == PasswordSession.credential_id)
            & (PasswordCredential.tenant_id == PasswordSession.tenant_id),
        )
        .where(PasswordSession.token_hash == token_digest(token))
    ).one()
    return row[0], row[1]


def factor_for(session: Session, credential: PasswordCredential, *, lock: bool = False) -> TotpFactor | None:
    query = select(TotpFactor).where(
        TotpFactor.tenant_id == credential.tenant_id, TotpFactor.principal_id == credential.principal_id
    )
    return session.scalar(query.with_for_update() if lock else query)


def identity_with_step_up(
    session: Session, settings: Settings, token: str, identity: VerifiedIdentity
) -> VerifiedIdentity:
    if not settings.local_totp_enabled:
        return identity
    require_enabled(settings)
    login, credential = _login(session, token)
    now = datetime.now(UTC)
    grant = session.scalar(
        select(TotpGrant)
        .join(
            TotpFactor, (TotpFactor.id == TotpGrant.factor_id) & (TotpFactor.tenant_id == TotpGrant.tenant_id)
        )
        .where(
            TotpGrant.tenant_id == credential.tenant_id,
            TotpGrant.password_session_id == login.id,
            TotpFactor.principal_id == credential.principal_id,
            TotpFactor.enabled.is_(True),
            TotpFactor.version == TotpGrant.factor_version,
            TotpGrant.expires_at > now,
            TotpGrant.verified_at <= now,
            TotpGrant.expires_at <= login.expires_at,
        )
    )
    return replace(identity, mfa=grant is not None)


def verify(session: Session, settings: Settings, token: str, code: str) -> datetime | None:
    """Caller commits DENIED counters before returning an error."""
    require_enabled(settings)
    login, credential = _login(session, token)
    factor = factor_for(session, credential, lock=True)
    now = datetime.now(UTC)
    if factor is None or not factor.enabled:
        return None
    if factor.locked_until and factor.locked_until > now:
        return None
    counter = matching_counter(unseal(settings, factor), code, now, factor.last_counter)
    if counter is None:
        factor.failed_attempts += 1
        if factor.failed_attempts >= 5:
            factor.locked_until, factor.failed_attempts = now + timedelta(minutes=15), 0
    else:
        factor.last_counter, factor.failed_attempts, factor.locked_until = counter, 0, None
    audit.record(
        session,
        tenant_id=credential.tenant_id,
        action="totp_step_up",
        object_type="totp_factor",
        object_id=str(factor.id),
        outcome="SUCCESS" if counter is not None else "DENIED",
        actor_principal_id=credential.principal_id,
        actor_kind="USER",
    )
    if counter is None:
        return None
    expires = min(now + timedelta(seconds=GRANT_SECONDS), login.expires_at)
    grant = session.scalar(
        select(TotpGrant).where(
            TotpGrant.tenant_id == login.tenant_id, TotpGrant.password_session_id == login.id
        )
    )
    if grant is None:
        grant = TotpGrant(
            tenant_id=login.tenant_id,
            password_session_id=login.id,
            factor_id=factor.id,
            factor_version=factor.version,
            verified_at=now,
            expires_at=expires,
        )
        session.add(grant)
    else:
        grant.factor_id, grant.factor_version = factor.id, factor.version
        grant.verified_at, grant.expires_at = now, expires
    return expires
