"""Sign-in mode and authenticated human identity; no role or tenant provisioning."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, Response
from sqlalchemy import select

from finagent.api.deps import AppSettings, Factory, Principal
from finagent.core.config import AuthMode
from finagent.core.errors import AppError, ErrorCode
from finagent.database.models import Principal as PrincipalRow
from finagent.database.session import transaction
from finagent.security.authorization.access_explanation import explain_access
from finagent.security.authorization.policy import PrincipalKind

auth = APIRouter(prefix="/api/v1/auth", tags=["authentication"])


@auth.get("/config")
def sign_in_config(settings: AppSettings) -> dict[str, str]:
    """Public mode only. Never return client secrets, tokens, or identity lists here."""
    return {"mode": settings.auth_mode.value}


@auth.get("/session")
def sign_in_identity(
    request: Request, ctx: Principal, factory: Factory, settings: AppSettings
) -> dict[str, Any]:
    """The callback presents its configured provider credential to the normal resolver."""
    if ctx.kind is not PrincipalKind.USER:
        raise AppError(ErrorCode.UNAUTHENTICATED)
    if settings.auth_mode is AuthMode.ENTRA and not getattr(request.state, "password_session", False):
        identity = request.state.verified_identity
        if settings.entra_required_scope not in identity.scopes:
            raise AppError(ErrorCode.UNAUTHENTICATED)
    with transaction(factory) as session:
        row = session.execute(
            select(PrincipalRow.issuer, PrincipalRow.subject, PrincipalRow.display_name).where(
                PrincipalRow.id == ctx.principal_id,
                PrincipalRow.tenant_id == ctx.tenant_id,
                PrincipalRow.status == "ACTIVE",
                PrincipalRow.kind == "USER",
            )
        ).one_or_none()
    if row is None:
        raise AppError(ErrorCode.UNAUTHENTICATED)
    return {
        "issuer": row.issuer,
        "subject": row.subject,
        "display_name": row.display_name,
        "roles": sorted(role.name for role in ctx.roles),
        "permissions": sorted(permission.name for permission in ctx.permissions),
        "mfa_satisfied": ctx.mfa_satisfied,
    }


# BEGIN FINAGENT SELF ACCESS EXPLANATION V1
@auth.get("/access")
def explain_current_access(
    request: Request,
    response: Response,
    ctx: Principal,
    factory: Factory,
    settings: AppSettings,
) -> dict[str, Any]:
    """Explain only the current human principal; never provision or grant access."""
    sign_in_identity(request=request, ctx=ctx, factory=factory, settings=settings)
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Pragma"] = "no-cache"
    return explain_access(ctx)


# END FINAGENT SELF ACCESS EXPLANATION V1
