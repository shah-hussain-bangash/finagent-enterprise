"""Local password-session TOTP step-up. No enrollment or identity claims in HTTP."""

from __future__ import annotations

import hashlib
from typing import Annotated

import redis
from fastapi import APIRouter, Header, Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from finagent.api.deps import AppSettings, Factory, Principal
from finagent.api.routers.password_auth import _LIMIT
from finagent.core.errors import AppError, ErrorCode
from finagent.database.session import transaction
from finagent.security.authentication import totp
from finagent.security.authentication.passwords import SESSION_MARKER

totp_auth = APIRouter(prefix="/api/v1/auth/totp", tags=["authentication"])


class TotpCode(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: SecretStr = Field(min_length=6, max_length=6)


def password_token(request: Request, authorization: str | None) -> str:
    if not getattr(request.state, "password_session", False) or not authorization:
        raise AppError(ErrorCode.FORBIDDEN)
    token = authorization.removeprefix("Bearer ").strip()
    if not token.startswith(SESSION_MARKER):
        raise AppError(ErrorCode.FORBIDDEN)
    return token


@totp_auth.get("/status")
def status(
    ctx: Principal,
    request: Request,
    settings: AppSettings,
    factory: Factory,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, bool]:
    totp.require_enabled(settings)
    token = password_token(request, authorization)
    with transaction(factory) as session:
        _, credential = totp._login(session, token)
        factor = totp.factor_for(session, credential)
        return {"enrolled": factor is not None and factor.enabled, "mfa_satisfied": ctx.mfa_satisfied}


@totp_auth.post("/verify")
def verify_code(
    body: TotpCode,
    ctx: Principal,
    request: Request,
    settings: AppSettings,
    factory: Factory,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, int | bool]:
    totp.require_enabled(settings)
    token = password_token(request, authorization)
    if settings.redis_url is None:
        raise AppError(ErrorCode.DEPENDENCY_UNAVAILABLE)
    limiter = redis.Redis.from_url(
        settings.redis_url.get_secret_value(), socket_timeout=1, socket_connect_timeout=1
    )
    key = (
        "finagent:totp-step-up:v1:"
        + hashlib.sha256(f"{ctx.tenant_id}:{ctx.principal_id}".encode()).hexdigest()
    )
    try:
        count = limiter.eval(_LIMIT, 1, key)
        if not isinstance(count, int):
            raise AppError(ErrorCode.DEPENDENCY_UNAVAILABLE)
        if count > 5:
            raise AppError(ErrorCode.RATE_LIMITED, headers={"Retry-After": "60"})
    except redis.RedisError as error:
        raise AppError(ErrorCode.DEPENDENCY_UNAVAILABLE) from error
    finally:
        limiter.close()
    with transaction(factory) as session:
        expires = totp.verify(session, settings, token, body.code.get_secret_value())
    if expires is None:
        # This isn't session expiry: preserve the authenticated first factor in the BFF.
        raise AppError(
            ErrorCode.FORBIDDEN, "Authenticator code was not accepted. Check enrollment, code and lockout."
        )
    return {"mfa_satisfied": True, "expires_at": int(expires.timestamp())}
