"""Password verification and revocable opaque sessions. Never establishes roles.

Credentials are provisioned by a schema-owner console, never by public signup or
an email claim. Password login alone is never evidence of MFA.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from finagent.core.errors import AppError, ErrorCode
from finagent.database.models import PasswordCredential, PasswordSession, Principal
from finagent.security.authentication.tokens import VerifiedIdentity
from finagent.services.audit_service import writer as audit

SESSION_MARKER = "fa_pwd_"
_TOKEN = re.compile(r"^fa_pwd_[A-Za-z0-9_-]{43}$")
_EMAIL = re.compile(
    r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,63}$"
)
_PARAMETERS = "scrypt-v1$32768$8$3"
SESSION_SECONDS = 1800


def normalize_email(value: str) -> str | None:
    email = value.strip().lower()
    if len(email) > 254 or not _EMAIL.fullmatch(email) or ".." in email:
        return None
    return email


def _derive(password: str, salt: bytes) -> bytes:
    # OWASP scrypt alternative: N=2^15, r=8, p=3. Fixed parameters bound memory/work.
    return hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=32768, r=8, p=3, maxmem=64 * 1024 * 1024, dklen=32
    )


def hash_password(password: str) -> str:
    if not 15 <= len(password) <= 128 or len(password.encode("utf-8")) > 512:
        raise ValueError("Use a password of 15 to 128 characters.")
    salt = secrets.token_bytes(16)
    return f"{_PARAMETERS}${salt.hex()}${_derive(password, salt).hex()}"


def verify_password(password: str, encoded: str) -> bool:
    if not 1 <= len(password) <= 128 or len(password.encode("utf-8")) > 512:
        return False
    try:
        algorithm, n, r, p, salt_hex, expected_hex = encoded.split("$")
        if "$".join((algorithm, n, r, p)) != _PARAMETERS:
            return False
        salt, expected = bytes.fromhex(salt_hex), bytes.fromhex(expected_hex)
        if len(salt) != 16 or len(expected) != 32:
            return False
        return hmac.compare_digest(_derive(password, salt), expected)
    except (ValueError, TypeError):
        return False


# Unknown addresses pay the same password derivation cost as known ones.
# A fixed, unreachable verifier is sufficient; it can never authenticate a user.
_DUMMY_HASH = _PARAMETERS + "$" + "00" * 16 + "$" + "00" * 32


def _event(session: Session, row: PasswordCredential, action: str, outcome: str) -> None:
    audit.record(
        session,
        tenant_id=row.tenant_id,
        action=action,
        object_type="password_credential",
        object_id=str(row.id),
        outcome=outcome,
        actor_principal_id=row.principal_id,
        actor_kind="USER",
    )


def authenticate(session: Session, email: str, password: str) -> tuple[str, Principal, datetime] | None:
    """Caller commits failed-attempt counters before returning a generic failure."""
    normalized = normalize_email(email)
    row = (
        session.scalar(
            select(PasswordCredential).where(PasswordCredential.email == normalized).with_for_update()
        )
        if normalized
        else None
    )
    valid = verify_password(password, row.password_hash if row else _DUMMY_HASH)
    now = datetime.now(UTC)
    if row is None:
        return None
    principal = session.get(Principal, row.principal_id)
    if (
        not valid
        or not row.enabled
        or principal is None
        or principal.kind != "USER"
        or principal.status != "ACTIVE"
        or (row.locked_until is not None and row.locked_until > now)
    ):
        if row.enabled and (row.locked_until is None or row.locked_until <= now):
            row.failed_attempts += 1
            if row.failed_attempts >= 5:
                row.locked_until = now + timedelta(minutes=15)
                row.failed_attempts = 0
        _event(session, row, "password_login", "DENIED")
        return None
    row.failed_attempts, row.locked_until = 0, None
    token = SESSION_MARKER + secrets.token_urlsafe(32)
    expires = now + timedelta(seconds=SESSION_SECONDS)
    session.add(
        PasswordSession(
            tenant_id=row.tenant_id,
            credential_id=row.id,
            credential_version=row.version,
            token_hash=token_digest(token),
            expires_at=expires,
        )
    )
    _event(session, row, "password_login", "SUCCESS")
    return token, principal, expires


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def validate_session(session: Session, token: str) -> VerifiedIdentity:
    if not _TOKEN.fullmatch(token):
        raise AppError(ErrorCode.UNAUTHENTICATED)
    result = session.execute(
        select(PasswordSession, PasswordCredential, Principal)
        .join(
            PasswordCredential,
            (PasswordCredential.id == PasswordSession.credential_id)
            & (PasswordCredential.tenant_id == PasswordSession.tenant_id),
        )
        .join(
            Principal,
            (Principal.id == PasswordCredential.principal_id)
            & (Principal.tenant_id == PasswordCredential.tenant_id),
        )
        .where(PasswordSession.token_hash == token_digest(token))
    ).first()
    if result is None:
        raise AppError(ErrorCode.UNAUTHENTICATED)
    login, credential, principal = result
    if (
        login.revoked_at is not None
        or login.expires_at <= datetime.now(UTC)
        or not credential.enabled
        or credential.version != login.credential_version
        or principal.kind != "USER"
        or principal.status != "ACTIVE"
    ):
        raise AppError(ErrorCode.UNAUTHENTICATED)
    return VerifiedIdentity(issuer=principal.issuer, subject=principal.subject, mfa=False)


def revoke_session(session: Session, token: str) -> None:
    if not _TOKEN.fullmatch(token):
        return
    login = session.scalar(
        select(PasswordSession).where(PasswordSession.token_hash == token_digest(token)).with_for_update()
    )
    if login and login.revoked_at is None:
        login.revoked_at = datetime.now(UTC)
        credential = session.get(PasswordCredential, login.credential_id)
        if credential:
            _event(session, credential, "password_logout", "SUCCESS")
