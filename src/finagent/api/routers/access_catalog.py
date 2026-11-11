"""Read-only role catalog for authorized human tenant administrators."""

from typing import Any

from fastapi import APIRouter, Request, Response

from finagent.api.deps import AppSettings, Factory, Principal
from finagent.api.routers.auth import sign_in_identity
from finagent.security.authorization.role_catalog import build_role_catalog

access_catalog = APIRouter(prefix="/api/v1/admin", tags=["admin"])


@access_catalog.get("/role-permission-catalog")
def get_role_permission_catalog(
    request: Request,
    response: Response,
    ctx: Principal,
    factory: Factory,
    settings: AppSettings,
) -> dict[str, Any]:
    sign_in_identity(request=request, ctx=ctx, factory=factory, settings=settings)
    catalog = build_role_catalog(ctx)
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Pragma"] = "no-cache"
    return catalog
