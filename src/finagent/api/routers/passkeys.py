"""Authenticated password-session passkey enrollment and step-up, local/test only."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Header, Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from finagent.api.deps import AppSettings, Factory, Principal
from finagent.api.routers.email_verification import throttle
from finagent.api.routers.totp_auth import password_token
from finagent.core.errors import AppError, ErrorCode
from finagent.database.session import transaction
from finagent.security.authentication import email_verification, passkeys

passkey_api = APIRouter(prefix="/api/v1/auth/passkeys", tags=["authentication"])


class EnrollmentPassword(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: SecretStr = Field(min_length=1, max_length=128)


class PasskeyResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    challenge_id: UUID
    credential: dict[str, Any]


@passkey_api.get("/status")
def status(
    ctx: Principal,
    request: Request,
    settings: AppSettings,
    factory: Factory,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, bool]:
    passkeys.require_enabled(settings)
    token = password_token(request, authorization)
    with transaction(factory) as session:
        _, credential = email_verification.binding(session, token)
        factor = passkeys.factor_for(session, credential)
        return {
            "enrolled": factor is not None and factor.enabled,
            "enrollment_allowed": factor is None
            and str(credential.principal_id) in settings.passkey_enrollment_principals,
            "email_verified": email_verification.recently_verified(session, settings, token),
            "mfa_satisfied": ctx.mfa_satisfied,
        }


@passkey_api.post("/register/options")
def registration_options(
    body: EnrollmentPassword,
    ctx: Principal,
    request: Request,
    settings: AppSettings,
    factory: Factory,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    passkeys.require_enabled(settings)
    token = password_token(request, authorization)
    throttle(settings, ctx, "passkey-register")
    with transaction(factory) as session:
        result = passkeys.begin(session, settings, token, "REGISTER", body.password.get_secret_value())
    if result is None:
        raise AppError(
            ErrorCode.FORBIDDEN,
            "Enrollment unavailable. Check password, recent email verification and enrollment authorization.",
        )
    return result


@passkey_api.post("/verify/options")
def verification_options(
    ctx: Principal,
    request: Request,
    settings: AppSettings,
    factory: Factory,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    passkeys.require_enabled(settings)
    token = password_token(request, authorization)
    throttle(settings, ctx, "passkey-options")
    with transaction(factory) as session:
        result = passkeys.begin(session, settings, token, "VERIFY")
    if result is None:
        raise AppError(ErrorCode.FORBIDDEN, "No active passkey is available for this account.")
    return result


def finish_response(
    purpose: str,
    body: PasskeyResponse,
    ctx: Principal,
    request: Request,
    settings: AppSettings,
    factory: Factory,
    authorization: str | None,
) -> dict[str, bool | int]:
    passkeys.require_enabled(settings)
    token = password_token(request, authorization)
    throttle(settings, ctx, "passkey-finish")
    with transaction(factory) as session:
        result = passkeys.finish(session, settings, token, body.challenge_id, purpose, body.credential)
    if result is None:
        raise AppError(
            ErrorCode.FORBIDDEN,
            "Passkey response not accepted. Start a fresh request on the registered phone website.",
        )
    if isinstance(result, datetime):
        return {"mfa_satisfied": True, "expires_at": int(result.timestamp())}
    return {"registered": True, "grants_mfa": False}


@passkey_api.post("/register/finish")
def registration_finish(
    body: PasskeyResponse,
    ctx: Principal,
    request: Request,
    settings: AppSettings,
    factory: Factory,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, bool | int]:
    return finish_response("REGISTER", body, ctx, request, settings, factory, authorization)


@passkey_api.post("/verify/finish")
def verification_finish(
    body: PasskeyResponse,
    ctx: Principal,
    request: Request,
    settings: AppSettings,
    factory: Factory,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, bool | int]:
    return finish_response("VERIFY", body, ctx, request, settings, factory, authorization)


@passkey_api.post("/revoke")
def revoke(
    ctx: Principal,
    request: Request,
    settings: AppSettings,
    factory: Factory,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, bool]:
    passkeys.require_enabled(settings)
    token = password_token(request, authorization)
    throttle(settings, ctx, "passkey-revoke")
    with transaction(factory) as session:
        accepted = passkeys.revoke(session, settings, token)
    if not accepted:
        raise AppError(
            ErrorCode.FORBIDDEN,
            "Verify this passkey before revoking it. Recovery requires the credential administrator.",
        )
    return {"revoked": True}
