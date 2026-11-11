"""Input and authority rules for critical incident response."""

import uuid
from dataclasses import replace

import pytest
from pydantic import ValidationError

from finagent.core.errors import AppError
from finagent.security.authorization.policy import PrincipalContext, PrincipalKind, Role
from finagent.services.governance_service import capability_incidents as incidents


def context(role: Role, *, mfa: bool = True) -> PrincipalContext:
    return PrincipalContext(
        uuid.uuid4(),
        uuid.uuid4(),
        PrincipalKind.USER,
        frozenset({role}),
        mfa_satisfied=mfa,
    )


@pytest.mark.parametrize("role", [Role.ANALYST, Role.REVIEWER, Role.POLICY_ADMIN, Role.AUDITOR])
def test_unprivileged_roles_cannot_declare_incidents(role: Role) -> None:
    with pytest.raises(AppError):
        incidents._authorize(context(role))


@pytest.mark.parametrize("role", [Role.TENANT_ADMIN, Role.PLATFORM_OPERATOR])
def test_incident_commands_require_mfa_even_without_step_up_configuration(
    role: Role,
) -> None:
    ctx = replace(context(role, mfa=False), step_up=frozenset())
    with pytest.raises(AppError):
        incidents._authorize(ctx)


def test_delegated_admin_role_cannot_restore() -> None:
    ctx = replace(context(Role.TENANT_ADMIN), assigned_roles=frozenset())
    with pytest.raises(AppError):
        incidents._authorize(ctx, restore=True)


def test_platform_operator_cannot_restore() -> None:
    with pytest.raises(AppError):
        incidents._authorize(context(Role.PLATFORM_OPERATOR), restore=True)


@pytest.mark.parametrize("field", ["findings", "remediation_ref", "verification_ref"])
def test_investigation_fields_cannot_be_whitespace(field: str) -> None:
    data = {
        "expected_version": 1,
        "findings": "Root cause was investigated.",
        "remediation_ref": "change-123",
        "verification_ref": "test-run-456",
    }
    data[field] = " " * 20
    with pytest.raises(ValidationError):
        incidents.InvestigationRequest.model_validate(data)


def test_declaration_requires_evidence_and_a_known_owner_role() -> None:
    with pytest.raises(ValidationError):
        incidents.IncidentRequest(
            capability_id="AG-AML",
            owner_role="UNASSIGNED",  # type: ignore[arg-type]
            reason="Confirmed critical control incident.",
            evidence_refs=[],
        )
