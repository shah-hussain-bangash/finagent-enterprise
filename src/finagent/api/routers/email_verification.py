"""Email possession verification for a DB-provisioned password identity; never MFA."""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

import redis
from fastapi import APIRouter, Header, Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlalchemy import select

from finagent.api.deps import AppSettings, Factory, Principal
from finagent.api.routers.password_auth import _LIMIT
from finagent.api.routers.totp_auth import password_token
from finagent.core.errors import AppError, ErrorCode
from finagent.database.models import EmailVerificationChallenge
from finagent.database.session import transaction
from finagent.security.authentication import email_verification as service

email_verification_api = APIRouter(prefix="/api/v1/auth/email", tags=["authentication"])
log = logging.getLogger("finagent.authentication")


class EmailCode(BaseModel):
    model_config = ConfigDict(extra="forbid")
    challenge_id: UUID
    code: SecretStr = Field(min_length=6, max_length=6)


def throttle(settings: AppSettings, principal: Principal, operation: str) -> None:
    if settings.redis_url is None:
        raise AppError(ErrorCode.DEPENDENCY_UNAVAILABLE)
    client = redis.Redis.from_url(
        settings.redis_url.get_secret_value(), socket_timeout=1, socket_connect_timeout=1
    )
    key = (
        "finagent:email-proof:v1:"
        + operation
        + ":"
        + hashlib.sha256(f"{principal.tenant_id}:{principal.principal_id}".encode()).hexdigest()
    )
    try:
        count = client.eval(_LIMIT, 1, key)
        if not isinstance(count, int):
            raise AppError(ErrorCode.DEPENDENCY_UNAVAILABLE)
        if count > 5:
            raise AppError(ErrorCode.RATE_LIMITED, headers={"Retry-After": "60"})
    except redis.RedisError as error:
        raise AppError(ErrorCode.DEPENDENCY_UNAVAILABLE) from error
    finally:
        client.close()


@email_verification_api.get("/status")
def status(
    ctx: Principal,
    request: Request,
    settings: AppSettings,
    factory: Factory,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    service.require_enabled(settings)
    token = password_token(request, authorization)
    with transaction(factory) as session:
        login, _ = service.binding(session, token)
        pending = session.scalar(
            select(EmailVerificationChallenge)
            .where(
                EmailVerificationChallenge.tenant_id == ctx.tenant_id,
                EmailVerificationChallenge.password_session_id == login.id,
                EmailVerificationChallenge.delivery_status == "SENT",
                EmailVerificationChallenge.consumed_at.is_(None),
                EmailVerificationChallenge.cancelled_at.is_(None),
                EmailVerificationChallenge.expires_at > datetime.now(UTC),
            )
            .order_by(EmailVerificationChallenge.created_at.desc())
        )
        return {
            "email_verified": service.recently_verified(session, settings, token),
            "challenge_id": str(pending.id) if pending else None,
            "grants_mfa": False,
        }


@email_verification_api.post("/send")
def send_code(
    ctx: Principal,
    request: Request,
    settings: AppSettings,
    factory: Factory,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, str | bool]:
    service.require_enabled(settings)
    token = password_token(request, authorization)
    throttle(settings, ctx, "send")
    with transaction(factory) as session:
        row, recipient, code = service.issue(session, settings, token)
        challenge_id, tenant_id = row.id, row.tenant_id
    # Commit before the external side effect. A failed or ambiguous delivery is
    # never reported as proof, and no raw code is stored for background retries.
    sent = False
    try:
        mailer: service.VerificationMailer = getattr(
            request.app.state, "verification_mailer", service.GmailMailer(settings)
        )
        mailer.send_code(recipient, code)
        sent = True
    except Exception as error:
        log.warning("email_verification_delivery_failed", extra={"error_type": type(error).__name__})
    finally:
        del code
    with transaction(factory) as session:
        delivered = service.mark_delivery(session, challenge_id, tenant_id, sent=sent)
    if not delivered:
        raise AppError(ErrorCode.DEPENDENCY_UNAVAILABLE, "Email delivery was not confirmed. Try again later.")
    return {"delivery": "accepted_by_sender", "challenge_id": str(challenge_id), "grants_mfa": False}


@email_verification_api.post("/verify")
def verify_code(
    body: EmailCode,
    ctx: Principal,
    request: Request,
    settings: AppSettings,
    factory: Factory,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, bool]:
    service.require_enabled(settings)
    token = password_token(request, authorization)
    throttle(settings, ctx, "verify")
    with transaction(factory) as session:
        accepted = service.verify(session, settings, token, body.challenge_id, body.code.get_secret_value())
    if not accepted:
        raise AppError(ErrorCode.FORBIDDEN, "Code not accepted. Check the latest code and its expiry.")
    return {"email_verified": True, "grants_mfa": False}
