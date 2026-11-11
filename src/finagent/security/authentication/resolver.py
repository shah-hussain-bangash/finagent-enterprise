"""Map a verified identity to a trusted PrincipalContext (FR-001, FR-003, SPR-004/005/008, DIR-002).

This is the ONLY place tenant and roles are established. Unknown, suspended or removed principals,
suspended tenants and inactive integrations all fail as UNAUTHENTICATED without detail.
Membership, delegations and break-glass grants are read per request (no cache) so revocation and
expiry take effect on the next request (DATA-D08 local default).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session, aliased

from finagent.core.errors import AppError, ErrorCode
from finagent.database.models import (
    BreakGlassGrant,
    Delegation,
    IntegrationConnection,
    Principal,
    RoleAssignment,
    Tenant,
)
from finagent.security.authentication.tokens import VerifiedIdentity
from finagent.security.authorization.policy import (
    DEFAULT_STEP_UP,
    RESOLVER_ONLY_ROLES,
    DelegatedRole,
    Permission,
    PrincipalContext,
    PrincipalKind,
    Role,
)


@dataclass(frozen=True)
class ResolverPolicy:
    step_up: frozenset[Permission] = DEFAULT_STEP_UP
    delegable_roles: frozenset[Role] = field(default_factory=lambda: frozenset({Role.ANALYST, Role.REVIEWER}))
    break_glass_enabled: bool = False


def _active_roles(session: Session, tenant_id: UUID, principal_id: UUID, now: datetime) -> frozenset[Role]:
    rows = session.scalars(
        select(RoleAssignment.role).where(
            RoleAssignment.tenant_id == tenant_id,
            RoleAssignment.principal_id == principal_id,
            RoleAssignment.valid_from <= now,
            or_(RoleAssignment.valid_to.is_(None), RoleAssignment.valid_to > now),
        )
    )
    roles = frozenset(Role(r) for r in rows if r in Role.__members__)
    return roles - RESOLVER_ONLY_ROLES  # a database row can never grant emergency roles


def _active_delegations(
    session: Session, principal: Principal, now: datetime, delegable: frozenset[Role]
) -> tuple[DelegatedRole, ...]:
    """Delegations count only while the window is open, they are not revoked, and the delegator
    is still active and still holds the delegated role directly (no transitive delegation)."""
    delegator = aliased(Principal)
    rows = session.execute(
        select(Delegation.id, Delegation.role, Delegation.delegator_id)
        .join(
            delegator,
            and_(delegator.tenant_id == Delegation.tenant_id, delegator.id == Delegation.delegator_id),
        )
        .join(
            RoleAssignment,
            and_(
                RoleAssignment.tenant_id == Delegation.tenant_id,
                RoleAssignment.principal_id == Delegation.delegator_id,
                RoleAssignment.role == Delegation.role,
            ),
        )
        .where(
            Delegation.tenant_id == principal.tenant_id,
            Delegation.delegate_id == principal.id,
            Delegation.revoked_at.is_(None),
            Delegation.valid_from <= now,
            Delegation.valid_to > now,
            delegator.status == "ACTIVE",
            RoleAssignment.valid_from <= now,
            or_(RoleAssignment.valid_to.is_(None), RoleAssignment.valid_to > now),
        )
        .order_by(Delegation.valid_from, Delegation.id)
    ).all()
    return tuple(
        DelegatedRole(Role(role), delegator_id, did)
        for did, role, delegator_id in rows
        if role in Role.__members__ and Role(role) in delegable
    )


def _active_break_glass(session: Session, principal: Principal, now: datetime) -> UUID | None:
    return session.scalar(
        select(BreakGlassGrant.id)
        .where(
            BreakGlassGrant.tenant_id == principal.tenant_id,
            BreakGlassGrant.principal_id == principal.id,
            BreakGlassGrant.revoked_at.is_(None),
            BreakGlassGrant.activated_at <= now,
            BreakGlassGrant.expires_at > now,  # auto-expiry: checked on every request
        )
        .order_by(BreakGlassGrant.expires_at.desc())
        .limit(1)
    )


def resolve_principal(
    session: Session, identity: VerifiedIdentity, policy: ResolverPolicy | None = None
) -> PrincipalContext:
    row = session.execute(
        select(Principal, Tenant)
        .join(Tenant, Tenant.id == Principal.tenant_id)
        .where(Principal.issuer == identity.issuer, Principal.subject == identity.subject)
    ).first()
    if row is None:
        raise AppError(ErrorCode.UNAUTHENTICATED)
    return _build(session, row[0], row[1], identity.mfa, policy or ResolverPolicy())


def resolve_principal_by_id(
    session: Session, tenant_id: UUID, principal_id: UUID, policy: ResolverPolicy | None = None
) -> PrincipalContext:
    """Background re-authorization (worker resume, FR-034/SPR-011): the CURRENT rights of the
    principal who requested the work. No token, so never MFA-satisfied and never break-glass."""
    row = session.execute(
        select(Principal, Tenant)
        .join(Tenant, Tenant.id == Principal.tenant_id)
        .where(Principal.tenant_id == tenant_id, Principal.id == principal_id)
    ).first()
    if row is None:
        raise AppError(ErrorCode.UNAUTHENTICATED)
    return _build(session, row[0], row[1], False, policy or ResolverPolicy())


def _build(
    session: Session, principal: Principal, tenant: Tenant, mfa: bool, policy: ResolverPolicy
) -> PrincipalContext:
    now = datetime.now(UTC)
    if principal.status != "ACTIVE" or tenant.status != "ACTIVE":
        raise AppError(ErrorCode.UNAUTHENTICATED)

    assigned = _active_roles(session, principal.tenant_id, principal.id, now)
    kind = PrincipalKind(principal.kind)
    source_system: str | None = None
    alert_types: frozenset[str] = frozenset()
    delegations: tuple[DelegatedRole, ...] = ()
    grant_id: UUID | None = None

    if kind is PrincipalKind.INTEGRATION:
        conn = session.scalar(
            select(IntegrationConnection).where(
                IntegrationConnection.tenant_id == principal.tenant_id,
                IntegrationConnection.principal_id == principal.id,
                IntegrationConnection.status == "ACTIVE",
            )
        )
        if conn is None:
            raise AppError(ErrorCode.UNAUTHENTICATED)
        source_system, alert_types = conn.source_system, frozenset(conn.permitted_alert_types)
        assigned = assigned & {Role.INTEGRATION}  # integration identities never hold human roles
        roles = assigned
    else:
        assigned = assigned - {Role.INTEGRATION}  # and humans never hold the integration role
        delegations = _active_delegations(session, principal, now, policy.delegable_roles)
        roles = assigned | {d.role for d in delegations}
        # Emergency access is honoured only while the feature is enabled, the principal is still
        # eligible, and the session itself is MFA-satisfied (SPR-005/008).
        if policy.break_glass_enabled and Role.BREAK_GLASS in assigned and mfa:
            grant_id = _active_break_glass(session, principal, now)
            if grant_id is not None:
                roles = roles | {Role.EMERGENCY_READ}

    return PrincipalContext(
        principal_id=principal.id,
        tenant_id=principal.tenant_id,
        kind=kind,
        roles=frozenset(roles),
        display_name=principal.display_name,
        mfa_satisfied=mfa,
        source_system=source_system,
        permitted_alert_types=alert_types,
        step_up=policy.step_up,
        delegations=delegations,
        break_glass_grant_id=grant_id,
        assigned_roles=frozenset(assigned),
    )
