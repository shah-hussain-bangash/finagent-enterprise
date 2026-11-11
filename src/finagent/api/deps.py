"""Request dependencies. Transactions are explicit inside endpoints (commit BEFORE responding, so
no success is ever reported for an uncommitted change - prompt §23, NQ-006)."""

from __future__ import annotations

import logging
import re
from typing import Annotated

from fastapi import Depends, Header, Request
from sqlalchemy.orm import Session, sessionmaker

from finagent.core.config import AuthMode, Settings
from finagent.core.errors import AppError, ErrorCode
from finagent.database.session import transaction
from finagent.security.authentication import connector_credentials, passkeys, totp
from finagent.security.authentication.passwords import SESSION_MARKER, validate_session
from finagent.security.authentication.resolver import ResolverPolicy, resolve_principal
from finagent.security.authentication.tokens import TokenValidator
from finagent.security.authorization.policy import PrincipalContext, PrincipalKind
from finagent.security.rate_limit import Bucket, RateLimiter, principal_key, tenant_key

_IDEMPOTENCY = re.compile(r"^[A-Za-z0-9._:-]{8,200}$")
_IF_MATCH = re.compile(r'^(?:W/)?"?(\d{1,9})"?$')
log = logging.getLogger("finagent.api")


def get_settings_dep(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_session_factory(request: Request) -> sessionmaker[Session]:
    factory: sessionmaker[Session] = request.app.state.session_factory
    return factory


def charge(request: Request, ctx: PrincipalContext, cost: int = 1) -> None:
    """Consume quota for the principal and its tenant (NQ-028). Expensive operations call this
    again with a higher cost. A noisy principal or tenant gets 429 + Retry-After."""
    settings: Settings = request.app.state.settings
    limiter: RateLimiter = request.app.state.rate_limiter
    checks = (
        (
            principal_key(ctx.tenant_id, ctx.principal_id),
            Bucket(
                settings.rate_limit_principal_per_minute,
                settings.rate_limit_principal_burst,
            ),
        ),
        (
            tenant_key(ctx.tenant_id),
            Bucket(settings.rate_limit_tenant_per_minute, settings.rate_limit_tenant_burst),
        ),
    )
    for key, bucket in checks:
        decision = limiter.hit(key, bucket, cost)
        if not decision.allowed:
            log.warning(
                "rate_limited",
                extra={
                    "scope": key.split(":")[2],
                    "retry_after_s": decision.retry_after_s,
                },
            )
            raise AppError(
                ErrorCode.RATE_LIMITED,
                headers={"Retry-After": str(decision.retry_after_s)},
            )


def get_principal(
    request: Request, authorization: Annotated[str | None, Header()] = None
) -> PrincipalContext:
    if not authorization or not authorization.startswith("Bearer "):
        raise AppError(ErrorCode.UNAUTHENTICATED)
    validator: TokenValidator = request.app.state.token_validator
    token = authorization.removeprefix("Bearer ").strip()
    password_session = token.startswith(SESSION_MARKER)
    connector_session = token.startswith(connector_credentials.SESSION_MARKER)
    if connector_session:
        if request.method != "POST" or request.url.path != "/api/v1/alerts":
            raise AppError(ErrorCode.UNAUTHENTICATED)
        with transaction(request.app.state.session_factory) as session:
            identity = connector_credentials.validate_credential(session, request.app.state.settings, token)
    elif password_session:
        if not request.app.state.settings.password_login_enabled:
            raise AppError(ErrorCode.UNAUTHENTICATED)
        with transaction(request.app.state.session_factory) as session:
            identity = validate_session(session, token)
            identity = totp.identity_with_step_up(session, request.app.state.settings, token, identity)
            identity = passkeys.identity_with_step_up(session, request.app.state.settings, token, identity)
    else:
        identity = validator.validate(token)
    request.state.password_session = password_session
    request.state.verified_identity = identity
    policy: ResolverPolicy = request.app.state.resolver_policy
    with transaction(request.app.state.session_factory) as session:
        ctx = resolve_principal(session, identity, policy)
    if connector_session:
        if ctx.kind is not PrincipalKind.INTEGRATION:
            raise AppError(ErrorCode.UNAUTHENTICATED)
    elif request.app.state.settings.auth_mode is AuthMode.GOOGLE and ctx.kind is not PrincipalKind.USER:
        raise AppError(ErrorCode.UNAUTHENTICATED)
    charge(request, ctx)
    return ctx


def require_idempotency_key(
    idempotency_key: Annotated[str | None, Header()] = None,
) -> str:
    if not idempotency_key or not _IDEMPOTENCY.fullmatch(idempotency_key):
        raise AppError(
            ErrorCode.IDEMPOTENCY_KEY_REQUIRED,
            "A valid Idempotency-Key header is required.",
        )
    return idempotency_key


def optional_if_match(if_match: Annotated[str | None, Header()] = None) -> int | None:
    """Parse `If-Match: "<record_version>"` (strong or weak form). Absent -> None."""
    if if_match is None:
        return None
    m = _IF_MATCH.fullmatch(if_match.strip())
    if m is None:
        raise AppError(
            ErrorCode.MALFORMED_REQUEST,
            'If-Match must carry the record version, e.g. "3".',
        )
    return int(m.group(1))


Principal = Annotated[PrincipalContext, Depends(get_principal)]
Factory = Annotated[sessionmaker[Session], Depends(get_session_factory)]
AppSettings = Annotated[Settings, Depends(get_settings_dep)]
IdempotencyKey = Annotated[str, Depends(require_idempotency_key)]
IfMatch = Annotated[int | None, Depends(optional_if_match)]
