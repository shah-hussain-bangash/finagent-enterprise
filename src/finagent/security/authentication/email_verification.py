"""Email possession proof for existing password accounts; never an MFA authenticator."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import smtplib
import ssl
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from typing import Protocol
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from finagent.core.config import LOCAL_ONLY_ENVIRONMENTS, Settings
from finagent.core.errors import AppError, ErrorCode
from finagent.database.models import EmailVerificationChallenge, PasswordCredential, PasswordSession
from finagent.security.authentication.passwords import token_digest, validate_session
from finagent.services.audit_service import writer as audit

PURPOSE = "verify_email"
CODE_SECONDS = 300


class VerificationMailer(Protocol):
    def send_code(self, recipient: str, code: str) -> None: ...


class GmailMailer:
    def __init__(self, settings: Settings):
        self.settings = settings

    def send_code(self, recipient: str, code: str) -> None:
        require_enabled(self.settings)
        sender = self.settings.gmail_sender
        password = self.settings.gmail_app_password
        if not sender or password is None:
            raise AppError(ErrorCode.DEPENDENCY_UNAVAILABLE)
        message = EmailMessage()
        message["From"], message["To"] = sender, recipient
        message["Subject"] = "FinAgent email verification code"
        message.set_content(
            "Your FinAgent email verification code is: " + code + "\n\n"
            "This code expires in five minutes and can be used once in the requesting sign-in session. "
            "It does not approve an investigation or replace MFA. "
            "If you did not request this message, ignore it."
        )
        # Fixed Gmail endpoint; verified implicit TLS, no STARTTLS downgrade/debug log.
        with smtplib.SMTP_SSL(
            "smtp.gmail.com", 465, timeout=15, context=ssl.create_default_context()
        ) as client:
            client.login(sender, password.get_secret_value())
            refused = client.send_message(message)
            if refused:
                raise AppError(ErrorCode.DEPENDENCY_UNAVAILABLE)


def require_enabled(settings: Settings) -> None:
    if not settings.email_verification_enabled or settings.environment not in LOCAL_ONLY_ENVIRONMENTS:
        raise AppError(ErrorCode.FEATURE_DISABLED)


def binding(session: Session, token: str) -> tuple[PasswordSession, PasswordCredential]:
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


def digest(settings: Settings, *parts: str) -> str:
    require_enabled(settings)
    if settings.email_verification_key is None:
        raise AppError(ErrorCode.DEPENDENCY_UNAVAILABLE)
    key = base64.b64decode(settings.email_verification_key.get_secret_value(), validate=True)
    return hmac.new(
        key, "\x00".join(("finagent-email-proof-v1", *parts)).encode(), hashlib.sha256
    ).hexdigest()


def code_digest(settings: Settings, row: EmailVerificationChallenge, code: str) -> str:
    return digest(
        settings,
        str(row.tenant_id),
        str(row.id),
        str(row.password_session_id),
        str(row.credential_id),
        str(row.credential_version),
        row.email_digest,
        PURPOSE,
        code,
    )


def record_event(session: Session, row: EmailVerificationChallenge, action: str, outcome: str) -> None:
    credential = session.get(PasswordCredential, row.credential_id)
    audit.record(
        session,
        tenant_id=row.tenant_id,
        action=action,
        object_type="email_verification",
        object_id=str(row.id),
        outcome=outcome,
        actor_principal_id=credential.principal_id if credential else None,
        actor_kind="USER",
    )


def issue(session: Session, settings: Settings, token: str) -> tuple[EmailVerificationChallenge, str, str]:
    require_enabled(settings)
    login, credential = binding(session, token)
    # Lock the shared account, not just a browser session, to serialize resends.
    session.scalar(select(PasswordCredential).where(PasswordCredential.id == credential.id).with_for_update())
    now = datetime.now(UTC)
    recent = session.scalar(
        select(EmailVerificationChallenge.id).where(
            EmailVerificationChallenge.tenant_id == credential.tenant_id,
            EmailVerificationChallenge.credential_id == credential.id,
            EmailVerificationChallenge.created_at > now - timedelta(seconds=60),
        )
    )
    if recent is not None:
        raise AppError(ErrorCode.RATE_LIMITED, headers={"Retry-After": "60"})
    session.execute(
        update(EmailVerificationChallenge)
        .where(
            EmailVerificationChallenge.tenant_id == credential.tenant_id,
            EmailVerificationChallenge.credential_id == credential.id,
            EmailVerificationChallenge.consumed_at.is_(None),
            EmailVerificationChallenge.cancelled_at.is_(None),
        )
        .values(cancelled_at=now)
    )
    code = str(secrets.randbelow(1_000_000)).zfill(6)
    row = EmailVerificationChallenge(
        id=uuid4(),
        tenant_id=credential.tenant_id,
        credential_id=credential.id,
        credential_version=credential.version,
        password_session_id=login.id,
        email_digest=digest(settings, "address", credential.email),
        code_hash="pending",
        created_at=now,
        expires_at=min(now + timedelta(seconds=CODE_SECONDS), login.expires_at),
        delivery_status="PENDING",
        failed_attempts=0,
    )
    row.code_hash = code_digest(settings, row, code)
    session.add(row)
    session.flush()
    record_event(session, row, "email_verification_requested", "SUCCESS")
    return row, credential.email, code


def mark_delivery(session: Session, challenge_id: UUID, tenant_id: UUID, *, sent: bool) -> bool:
    row = session.scalar(
        select(EmailVerificationChallenge)
        .where(
            EmailVerificationChallenge.id == challenge_id, EmailVerificationChallenge.tenant_id == tenant_id
        )
        .with_for_update()
    )
    if row is None or row.cancelled_at is not None or row.expires_at <= datetime.now(UTC):
        return False
    row.delivery_status = "SENT" if sent else "FAILED"
    if not sent:
        row.cancelled_at = datetime.now(UTC)
    record_event(session, row, "email_verification_delivery", "SUCCESS" if sent else "DENIED")
    return sent


def verify(session: Session, settings: Settings, token: str, challenge_id: UUID, code: str) -> bool:
    require_enabled(settings)
    login, credential = binding(session, token)
    row = session.scalar(
        select(EmailVerificationChallenge)
        .where(
            EmailVerificationChallenge.id == challenge_id,
            EmailVerificationChallenge.tenant_id == login.tenant_id,
            EmailVerificationChallenge.password_session_id == login.id,
        )
        .with_for_update()
    )
    now = datetime.now(UTC)
    if (
        row is None
        or row.credential_id != credential.id
        or row.credential_version != credential.version
        or row.cancelled_at is not None
        or row.consumed_at is not None
        or row.expires_at <= now
        or row.delivery_status != "SENT"
        or row.failed_attempts >= 5
        or not hmac.compare_digest(row.email_digest, digest(settings, "address", credential.email))
    ):
        return False
    valid = (
        len(code) == 6
        and code.isascii()
        and code.isdigit()
        and hmac.compare_digest(row.code_hash, code_digest(settings, row, code))
    )
    if valid:
        row.consumed_at = now
    else:
        row.failed_attempts += 1
        if row.failed_attempts >= 5:
            row.cancelled_at = now
    record_event(
        session,
        row,
        "email_verified" if valid else "email_verification_attempt",
        "SUCCESS" if valid else "DENIED",
    )
    return valid


def recently_verified(session: Session, settings: Settings, token: str) -> bool:
    require_enabled(settings)
    login, credential = binding(session, token)
    return (
        session.scalar(
            select(EmailVerificationChallenge.id).where(
                EmailVerificationChallenge.tenant_id == login.tenant_id,
                EmailVerificationChallenge.password_session_id == login.id,
                EmailVerificationChallenge.credential_id == credential.id,
                EmailVerificationChallenge.credential_version == credential.version,
                EmailVerificationChallenge.email_digest == digest(settings, "address", credential.email),
                EmailVerificationChallenge.consumed_at > datetime.now(UTC) - timedelta(minutes=10),
            )
        )
        is not None
    )
