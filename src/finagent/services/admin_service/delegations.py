"""Explicit, time-bounded, audited delegation (FR-003, SPR-006; DEC-06 reviewer matrix OPEN).

Only a Tenant Administrator (MFA step-up) creates or revokes delegations. A delegation names the
delegator, the delegate, ONE delegable role, a reason and a closed window no longer than the
configured maximum. The delegate acts in addition to - never instead of - their own identity:
audit records carry the delegate as actor and the delegation id/delegator in details.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from pydantic import AwareDatetime, BaseModel, ConfigDict, StringConstraints
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from finagent.core.errors import AppError, ErrorCode, not_found
from finagent.database.models import Delegation, Principal, RoleAssignment
from finagent.security.authorization.policy import Permission, PrincipalContext, Role, require
from finagent.services.audit_service import writer as audit


class DelegationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    delegator_id: uuid.UUID
    delegate_id: uuid.UUID
    role: Role
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=10, max_length=300)]
    valid_from: AwareDatetime | None = None
    valid_to: AwareDatetime


def _holds_role_directly(session: Session, tenant_id: uuid.UUID, principal_id: uuid.UUID, role: str) -> bool:
    now = datetime.now(UTC)
    return (
        session.scalar(
            select(RoleAssignment.id)
            .join(Principal, Principal.id == RoleAssignment.principal_id)
            .where(
                RoleAssignment.tenant_id == tenant_id,
                RoleAssignment.principal_id == principal_id,
                RoleAssignment.role == role,
                RoleAssignment.valid_from <= now,
                or_(RoleAssignment.valid_to.is_(None), RoleAssignment.valid_to > now),
                Principal.tenant_id == tenant_id,
                Principal.status == "ACTIVE",
                Principal.kind == "USER",
            )
        )
        is not None
    )


def _active_user(session: Session, tenant_id: uuid.UUID, principal_id: uuid.UUID) -> bool:
    return (
        session.scalar(
            select(Principal.id).where(
                Principal.tenant_id == tenant_id,
                Principal.id == principal_id,
                Principal.status == "ACTIVE",
                Principal.kind == "USER",
            )
        )
        is not None
    )


def _view(d: Delegation) -> dict[str, Any]:
    return {
        "delegation_id": str(d.id),
        "delegator_id": str(d.delegator_id),
        "delegate_id": str(d.delegate_id),
        "role": d.role,
        "reason": d.reason,
        "valid_from": d.valid_from.isoformat(),
        "valid_to": d.valid_to.isoformat(),
        "revoked_at": d.revoked_at.isoformat() if d.revoked_at else None,
    }


def create(
    session: Session,
    ctx: PrincipalContext,
    req: DelegationRequest,
    *,
    delegable_roles: frozenset[Role],
    max_days: int,
) -> dict[str, Any]:
    require(ctx, Permission.TENANT_ADMIN)
    now = datetime.now(UTC)
    start = req.valid_from or now

    def invalid(field: str, message: str) -> AppError:
        return AppError(ErrorCode.SEMANTIC_VALIDATION_FAILED, message, {"field": field})

    if req.role not in delegable_roles:
        raise invalid("role", "This role cannot be delegated.")
    if req.delegator_id == req.delegate_id:
        raise invalid("delegate_id", "A principal cannot delegate to itself.")
    if not start < req.valid_to:
        raise invalid("valid_to", "valid_to must be after valid_from.")
    if req.valid_to <= now:
        raise invalid("valid_to", "valid_to must be in the future.")
    if req.valid_to - start > timedelta(days=max_days):
        raise invalid("valid_to", f"Delegations may last at most {max_days} days.")
    # Identical message for unknown / other-tenant / inactive principals: no enumeration.
    if not _holds_role_directly(session, ctx.tenant_id, req.delegator_id, req.role.value):
        raise invalid("delegator_id", "The delegator must currently hold this role directly.")
    if not _active_user(session, ctx.tenant_id, req.delegate_id):
        raise invalid("delegate_id", "The delegate must be an active user in this tenant.")

    delegation = Delegation(
        tenant_id=ctx.tenant_id,
        delegator_id=req.delegator_id,
        delegate_id=req.delegate_id,
        role=req.role.value,
        reason=req.reason,
        valid_from=start,
        valid_to=req.valid_to,
        created_by=ctx.principal_id,
    )
    session.add(delegation)
    session.flush()
    audit.record(
        session,
        tenant_id=ctx.tenant_id,
        action="DELEGATION_CREATED",
        object_type="delegation",
        object_id=str(delegation.id),
        outcome="SUCCESS",
        actor_principal_id=ctx.principal_id,
        actor_kind=ctx.kind.value,
        details={
            "delegator_id": str(req.delegator_id),
            "delegate_id": str(req.delegate_id),
            "role": req.role.value,
            "valid_from": start.isoformat(),
            "valid_to": req.valid_to.isoformat(),
        },
    )
    return _view(delegation)


def revoke(session: Session, ctx: PrincipalContext, raw_id: str) -> dict[str, Any]:
    require(ctx, Permission.TENANT_ADMIN)
    try:
        did = uuid.UUID(raw_id)
    except ValueError:
        raise not_found() from None
    delegation = session.scalar(
        select(Delegation)
        .where(Delegation.tenant_id == ctx.tenant_id, Delegation.id == did)
        .with_for_update()
    )
    if delegation is None:
        raise not_found()
    if delegation.revoked_at is None:
        delegation.revoked_at, delegation.revoked_by = datetime.now(UTC), ctx.principal_id
        audit.record(
            session,
            tenant_id=ctx.tenant_id,
            action="DELEGATION_REVOKED",
            object_type="delegation",
            object_id=str(delegation.id),
            outcome="SUCCESS",
            actor_principal_id=ctx.principal_id,
            actor_kind=ctx.kind.value,
            details={
                "delegator_id": str(delegation.delegator_id),
                "delegate_id": str(delegation.delegate_id),
            },
        )
    session.flush()
    return _view(delegation)


def list_all(session: Session, ctx: PrincipalContext, *, limit: int) -> dict[str, Any]:
    require(ctx, Permission.TENANT_ADMIN)
    rows = session.scalars(
        select(Delegation)
        .where(Delegation.tenant_id == ctx.tenant_id)
        .order_by(Delegation.created_at.desc(), Delegation.id)
        .limit(limit)
    ).all()
    return {"items": [_view(d) for d in rows]}
