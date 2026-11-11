"""Tenant administration endpoints (prompt §22 admin; FR-003, SPR-005, SPR-008).

Every route is Tenant-Administrator-only (MFA step-up) except break-glass activation/revocation by
the eligible holder. Admin roles never gain case content through these routes.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query

from finagent.api.deps import AppSettings, Factory, Principal
from finagent.core.errors import AppError, ErrorCode
from finagent.database.session import transaction
from finagent.security.authorization.policy import Role
from finagent.services.admin_service import break_glass, delegations, feature_flags
from finagent.services.admin_service.break_glass import ActivationRequest, ReviewRequest
from finagent.services.admin_service.delegations import DelegationRequest
from finagent.services.admin_service.feature_flags import FlagUpdate

admin = APIRouter(prefix="/api/v1/admin", tags=["admin"])
Limit = Annotated[int | None, Query(ge=1)]


@admin.post("/delegations", status_code=201)
def create_delegation(
    body: DelegationRequest, ctx: Principal, factory: Factory, settings: AppSettings
) -> dict[str, Any]:
    with transaction(factory) as session:
        return delegations.create(
            session,
            ctx,
            body,
            delegable_roles=frozenset(Role(r) for r in settings.delegable_roles),
            max_days=settings.delegation_max_days,
        )


@admin.get("/delegations")
def list_delegations(
    ctx: Principal, factory: Factory, settings: AppSettings, limit: Limit = None
) -> dict[str, Any]:
    with transaction(factory) as session:
        return delegations.list_all(
            session, ctx, limit=min(limit or settings.page_size_default, settings.page_size_max)
        )


@admin.post("/delegations/{delegation_id}/revoke")
def revoke_delegation(delegation_id: str, ctx: Principal, factory: Factory) -> dict[str, Any]:
    with transaction(factory) as session:
        return delegations.revoke(session, ctx, delegation_id)


@admin.put("/feature-flags/{flag_key}")
def put_feature_flag(flag_key: str, body: FlagUpdate, ctx: Principal, factory: Factory) -> dict[str, Any]:
    """Tenant-scoped flag (prompt §44). The case-status write also needs the deployment setting."""
    with transaction(factory) as session:
        return feature_flags.set_flag(session, ctx, flag_key, body)


@admin.post("/break-glass", status_code=201)
def activate_break_glass(
    body: ActivationRequest, ctx: Principal, factory: Factory, settings: AppSettings
) -> dict[str, Any]:
    if not settings.break_glass_enabled:
        with transaction(factory) as session:  # the denied attempt is committed before we answer
            break_glass.record_denied(session, ctx, "feature_disabled")
        raise AppError(ErrorCode.FEATURE_DISABLED)
    with transaction(factory) as session:
        return break_glass.activate(session, ctx, body, max_minutes=settings.break_glass_max_minutes)


@admin.get("/break-glass")
def list_break_glass(
    ctx: Principal, factory: Factory, settings: AppSettings, limit: Limit = None
) -> dict[str, Any]:
    with transaction(factory) as session:
        return break_glass.list_all(
            session, ctx, limit=min(limit or settings.page_size_default, settings.page_size_max)
        )


@admin.post("/break-glass/{grant_id}/revoke")
def revoke_break_glass(grant_id: str, ctx: Principal, factory: Factory) -> dict[str, Any]:
    with transaction(factory) as session:
        return break_glass.revoke(session, ctx, grant_id)


@admin.post("/break-glass/{grant_id}/review")
def review_break_glass(
    grant_id: str, body: ReviewRequest, ctx: Principal, factory: Factory
) -> dict[str, Any]:
    with transaction(factory) as session:
        return break_glass.review(session, ctx, grant_id, body)
