"""Explicitly enabled email/password entry point; no public account provisioning."""

from __future__ import annotations

import hashlib
from typing import Annotated, Any

import redis
from fastapi import APIRouter, Header, Request, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from finagent.api.deps import AppSettings, Factory
from finagent.core.errors import AppError, ErrorCode
from finagent.database.session import transaction
from finagent.security.authentication import passwords
from finagent.security.authentication.resolver import resolve_principal

password_auth = APIRouter(prefix="/api/v1/auth/password", tags=["authentication"])
_LIMIT = """
local n = redis.call('INCR', KEYS[1])
if n == 1 then redis.call('EXPIRE', KEYS[1], 60) end
return n
"""


class PasswordLogin(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=1, max_length=254)
    password: SecretStr = Field(min_length=1, max_length=128)


def require_enabled(settings: AppSettings) -> None:
    if not settings.password_login_enabled:
        raise AppError(ErrorCode.FEATURE_DISABLED)


@password_auth.get("/config")
def password_config(settings: AppSettings) -> dict[str, bool]:
    """Separate capability discovery; preserve the existing provider contract."""
    return {"enabled": settings.password_login_enabled}


def throttle(request: Request, settings: AppSettings, email: str) -> None:
    # Fail closed: the general-purpose limiter's availability fallback is not
    # suitable for anonymous credential attempts. Never trust forwarded IPs.
    if settings.redis_url is None:
        raise AppError(ErrorCode.DEPENDENCY_UNAVAILABLE)
    client = redis.Redis.from_url(
        settings.redis_url.get_secret_value(), socket_timeout=1, socket_connect_timeout=1
    )
    source = request.client.host if request.client else "unknown"
    address = passwords.normalize_email(email) or email.strip().lower()
    try:
        for scope, value, limit in (("source", source, 100), ("account", address, 5)):
            key = "finagent:password-login:v1:" + scope + ":" + hashlib.sha256(value.encode()).hexdigest()
            count = client.eval(_LIMIT, 1, key)
            if not isinstance(count, int):
                raise AppError(ErrorCode.DEPENDENCY_UNAVAILABLE)
            if count > limit:
                raise AppError(ErrorCode.RATE_LIMITED, headers={"Retry-After": "60"})
    except redis.RedisError as exc:
        raise AppError(ErrorCode.DEPENDENCY_UNAVAILABLE) from exc
    finally:
        client.close()


@password_auth.post("/login")
def login(
    body: PasswordLogin, request: Request, response: Response, settings: AppSettings, factory: Factory
) -> dict[str, Any]:
    require_enabled(settings)
    throttle(request, settings, body.email)
    with transaction(factory) as session:
        result = passwords.authenticate(session, body.email, body.password.get_secret_value())
        if result:
            token, principal, expires = result
            # Suspended tenants/removed memberships still fail under the normal resolver.
            identity = passwords.validate_session(session, token)
            resolve_principal(session, identity, request.app.state.resolver_policy)
    if result is None:
        raise AppError(ErrorCode.UNAUTHENTICATED)
    response.headers["Cache-Control"] = "no-store"
    return {
        "access_token": token,
        # OAuth authorization scheme label, not a credential.
        "token_type": "Bearer",  # nosec B105
        "expires_at": int(expires.timestamp()),
        "subject": principal.subject,
        "mfa_satisfied": False,
    }


@password_auth.post("/logout", status_code=204)
def logout(factory: Factory, authorization: Annotated[str | None, Header()] = None) -> Response:
    # Idempotent even after expiry, suspension or revocation.
    if authorization and authorization.startswith("Bearer "):
        with transaction(factory) as session:
            passwords.revoke_session(session, authorization.removeprefix("Bearer ").strip())
    return Response(status_code=204, headers={"Cache-Control": "no-store"})
