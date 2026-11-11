"""Read-only self-access explanation. Not an authorization decision for a resource."""

from typing import Any

from finagent.core.errors import AppError, ErrorCode
from finagent.security.authorization.policy import (
    ROLE_PERMISSIONS,
    Permission,
    PrincipalContext,
    PrincipalKind,
    case_scope,
    require,
)


def explain_access(ctx: PrincipalContext) -> dict[str, Any]:
    """Use only a server-resolved principal; never construct it from client claims."""
    if ctx.kind is not PrincipalKind.USER:
        raise AppError(ErrorCode.UNAUTHENTICATED)

    permissions = []
    for permission in sorted(Permission, key=lambda item: item.value):
        try:
            require(ctx, permission)
            gate = "SATISFIED"
        except AppError as exc:
            if exc.code not in {ErrorCode.FORBIDDEN, ErrorCode.MFA_REQUIRED}:
                raise
            gate = exc.code.value

        permissions.append(
            {
                "name": permission.name,
                "value": permission.value,
                "permission_gate": gate,
                "mfa_required": permission in ctx.step_up,
                "source_roles": sorted(
                    role.value for role in ctx.roles if permission in ROLE_PERMISSIONS.get(role, frozenset())
                ),
            }
        )

    return {
        "schema_version": 1,
        "scope": "CURRENT_PRINCIPAL_ONLY",
        "principal_id": str(ctx.principal_id),
        "tenant_id": str(ctx.tenant_id),
        "effective_roles": sorted(role.value for role in ctx.roles),
        "direct_roles": sorted(role.value for role in ctx.direct_roles),
        "mfa_satisfied": ctx.mfa_satisfied,
        "case_row_scope": case_scope(ctx).value,
        "permissions": permissions,
        "resource_authorization_required": True,
        "notice": (
            "Informational snapshot only. Resource scope, separation of duties, "
            "feature gates and current authorization still apply to each operation."
        ),
    }
