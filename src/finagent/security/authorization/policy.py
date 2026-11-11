"""Deterministic, deny-by-default authorization (FR-002, SPR-009, prompt §17).

Roles and tenant come only from the server-side PrincipalContext built from our database. This
module contains no I/O so every rule is unit-testable. Row-level scoping is applied in queries via
CaseScope so concealed and absent records are indistinguishable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from uuid import UUID

from finagent.core.errors import AppError, ErrorCode


class Role(StrEnum):
    ANALYST = "ANALYST"
    REVIEWER = "REVIEWER"
    AML_SUPERVISOR = "AML_SUPERVISOR"
    TENANT_ADMIN = "TENANT_ADMIN"
    POLICY_ADMIN = "POLICY_ADMIN"
    AUDITOR = "AUDITOR"
    PLATFORM_OPERATOR = "PLATFORM_OPERATOR"
    INTEGRATION = "INTEGRATION"
    BREAK_GLASS = "BREAK_GLASS"  # eligibility only: grants nothing until a grant is activated
    EMERGENCY_READ = "EMERGENCY_READ"  # set ONLY by the resolver from an active break-glass grant


# Roles that can never come from a role_assignments row.
RESOLVER_ONLY_ROLES = frozenset({Role.EMERGENCY_READ})


class PrincipalKind(StrEnum):
    USER = "USER"
    INTEGRATION = "INTEGRATION"


class Permission(StrEnum):
    ALERT_INGEST = "alert:ingest"
    CASE_READ = "case:read"
    CASE_UPDATE = "case:update"
    CASE_ASSIGN = "case:assign"
    WORKFLOW_START = "workflow:start"
    WORKFLOW_CANCEL = "workflow:cancel"
    REPORT_EDIT = "report:edit"
    REPORT_SUBMIT = "report:submit"
    APPROVAL_DECIDE = "approval:decide"
    KNOWLEDGE_SEARCH = "knowledge:search"
    POLICY_PUBLISH = "policy:publish"
    DOCUMENT_UPLOAD = "document:upload"
    DOCUMENT_DOWNLOAD = "document:download"
    EXPORT_CREATE = "export:create"
    AUDIT_READ = "audit:read"
    TENANT_ADMIN = "tenant:admin"
    PLATFORM_OPERATE = "platform:operate"
    OPERATION_RECONCILE = "operation:reconcile"
    ESCALATION_READ = "escalation:read"
    BREAK_GLASS_ACTIVATE = "breakglass:activate"
    EVALUATION_RUN = "evaluation:run"


# SRS §2 actor table. Note: TENANT_ADMIN, POLICY_ADMIN, AUDITOR and PLATFORM_OPERATOR get
# no case-content permission (SPR §3 "Permission separation", DIR-003).
ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.ANALYST: frozenset(
        {
            Permission.CASE_READ,
            Permission.CASE_UPDATE,
            Permission.WORKFLOW_START,
            Permission.WORKFLOW_CANCEL,
            Permission.REPORT_EDIT,
            Permission.REPORT_SUBMIT,
            Permission.KNOWLEDGE_SEARCH,
            Permission.DOCUMENT_UPLOAD,
            Permission.DOCUMENT_DOWNLOAD,
        }
    ),
    Role.REVIEWER: frozenset(
        {
            Permission.CASE_READ,
            Permission.APPROVAL_DECIDE,
            Permission.KNOWLEDGE_SEARCH,
            Permission.DOCUMENT_DOWNLOAD,
            Permission.EXPORT_CREATE,
        }
    ),
    Role.AML_SUPERVISOR: frozenset(
        {
            Permission.CASE_READ,
            Permission.CASE_ASSIGN,
            Permission.WORKFLOW_CANCEL,
            Permission.KNOWLEDGE_SEARCH,
            Permission.EXPORT_CREATE,
            Permission.ESCALATION_READ,
        }
    ),
    Role.TENANT_ADMIN: frozenset({Permission.TENANT_ADMIN}),
    Role.POLICY_ADMIN: frozenset(
        {Permission.POLICY_PUBLISH, Permission.KNOWLEDGE_SEARCH, Permission.EVALUATION_RUN}
    ),
    Role.AUDITOR: frozenset({Permission.AUDIT_READ}),  # DEC-15: metadata only
    Role.PLATFORM_OPERATOR: frozenset(
        {Permission.PLATFORM_OPERATE, Permission.OPERATION_RECONCILE, Permission.EVALUATION_RUN}
    ),
    Role.INTEGRATION: frozenset({Permission.ALERT_INGEST}),
    Role.BREAK_GLASS: frozenset({Permission.BREAK_GLASS_ACTIVATE}),
    # SPR-008: emergency access is READ-ONLY. It never carries approval, submission, update or
    # export permissions, so it cannot bypass or create approval evidence.
    Role.EMERGENCY_READ: frozenset({Permission.CASE_READ}),
}

# SPR-005 default step-up set (overridable via Settings.mfa_step_up_permissions).
DEFAULT_STEP_UP = frozenset(
    {
        Permission.APPROVAL_DECIDE,
        Permission.CASE_ASSIGN,
        Permission.TENANT_ADMIN,
        Permission.POLICY_PUBLISH,
        Permission.EXPORT_CREATE,
        Permission.OPERATION_RECONCILE,
        Permission.BREAK_GLASS_ACTIVATE,
    }
)


@dataclass(frozen=True)
class DelegatedRole:
    """A role held through an active delegation (FR-003). Both identities are kept for audit."""

    role: Role
    delegator_id: UUID
    delegation_id: UUID


@dataclass(frozen=True)
class PrincipalContext:
    """Trusted identity for one request. Built only from validated token + database membership."""

    principal_id: UUID
    tenant_id: UUID
    kind: PrincipalKind
    roles: frozenset[Role]
    display_name: str = ""
    mfa_satisfied: bool = False
    source_system: str | None = None  # integration identities only
    permitted_alert_types: frozenset[str] = field(default_factory=frozenset)
    step_up: frozenset[Permission] = DEFAULT_STEP_UP
    delegations: tuple[DelegatedRole, ...] = ()
    break_glass_grant_id: UUID | None = None
    # Roles held through the principal's own role assignments. None means "all of `roles`".
    assigned_roles: frozenset[Role] | None = None

    @property
    def permissions(self) -> frozenset[Permission]:
        perms: set[Permission] = set()
        for role in self.roles:
            perms |= ROLE_PERMISSIONS.get(role, frozenset())
        return frozenset(perms)

    @property
    def direct_roles(self) -> frozenset[Role]:
        return self.roles if self.assigned_roles is None else self.assigned_roles

    def acting_for(self, role: Role) -> frozenset[UUID]:
        """Principals whose `role` work this principal may perform: itself when it holds the role
        directly, plus the delegators of its active delegations of that role (row scoping)."""
        ids = {d.delegator_id for d in self.delegations if d.role is role}
        if role in self.direct_roles:
            ids.add(self.principal_id)
        return frozenset(ids)

    def delegation_for(self, role: Role) -> DelegatedRole | None:
        """The delegation in use when `role` is held ONLY through delegation (recorded in audit)."""
        if role in self.direct_roles:
            return None
        return next((d for d in self.delegations if d.role is role), None)


def require(ctx: PrincipalContext, permission: Permission) -> None:
    if permission not in ctx.permissions:
        raise AppError(ErrorCode.FORBIDDEN)
    if permission in ctx.step_up and not ctx.mfa_satisfied:
        raise AppError(ErrorCode.MFA_REQUIRED)  # SPR-005 step-up; never silently downgraded


class CaseScope(StrEnum):
    NONE = "NONE"
    ASSIGNED = "ASSIGNED"  # analyst: cases actively assigned to them (DEC-14)
    REVIEW_ASSIGNED = "REVIEW_ASSIGNED"  # reviewer: cases with an active reviewer assignment
    TENANT = "TENANT"  # supervisor: every case in their own tenant


def case_scope(ctx: PrincipalContext) -> CaseScope:
    """Widest case-row scope the principal holds. Tenant filtering is always applied in addition."""
    if Permission.CASE_READ not in ctx.permissions:
        return CaseScope.NONE
    if Role.AML_SUPERVISOR in ctx.roles or Role.EMERGENCY_READ in ctx.roles:
        return CaseScope.TENANT
    if Role.ANALYST in ctx.roles:
        return CaseScope.ASSIGNED
    if Role.REVIEWER in ctx.roles:
        return CaseScope.REVIEW_ASSIGNED
    return CaseScope.NONE


def check_separation_of_duties(
    submitter_id: UUID, reviewer: PrincipalContext, allow_self: bool = False
) -> None:
    """Default reviewer matrix: the submitting analyst cannot approve the same report (FR-003)."""
    if submitter_id == reviewer.principal_id and not allow_self:
        raise AppError(ErrorCode.FORBIDDEN, "Separation of duties: you cannot review your own submission.")
