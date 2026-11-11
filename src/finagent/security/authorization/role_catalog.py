"""Read-only authorization definitions; never grants or changes a role."""

import hashlib
import json
from typing import Any

from finagent.core.errors import AppError, ErrorCode
from finagent.security.authorization.policy import (
    DEFAULT_STEP_UP,
    RESOLVER_ONLY_ROLES,
    ROLE_PERMISSIONS,
    Permission,
    PrincipalContext,
    PrincipalKind,
    Role,
    require,
)


def build_role_catalog(ctx: PrincipalContext) -> dict[str, Any]:
    """Use a server-resolved administrator and the existing policy definitions."""
    if ctx.kind is not PrincipalKind.USER:
        raise AppError(ErrorCode.UNAUTHENTICATED)
    require(ctx, Permission.TENANT_ADMIN)

    result: dict[str, Any] = {
        "schema_version": 1,
        "scope": "ROLE_PERMISSION_DEFINITIONS",
        "roles": [
            {
                "name": role.name,
                "value": role.value,
                "resolver_only": role in RESOLVER_ONLY_ROLES,
                "permission_names": sorted(
                    permission.name for permission in ROLE_PERMISSIONS.get(role, frozenset())
                ),
            }
            for role in sorted(Role, key=lambda item: item.value)
        ],
        "permissions": [
            {
                "name": permission.name,
                "value": permission.value,
                "default_mfa_required": permission in DEFAULT_STEP_UP,
                "current_policy_mfa_required": permission in ctx.step_up,
            }
            for permission in sorted(Permission, key=lambda item: item.value)
        ],
        "catalog_supports_role_assignment": False,
        "resource_authorization_required": True,
        "notice": (
            "Policy definitions only, not user entitlements or a grantable-role list. "
            "Resource scope, separation of duties and feature restrictions still apply. "
            "Current MFA flags describe the server-resolved request policy."
        ),
    }
    canonical = json.dumps(result, sort_keys=True, separators=(",", ":"))
    result["catalog_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return result
