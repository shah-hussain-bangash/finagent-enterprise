"""Deterministic SYNTHETIC seed data: two tenants, every role, integration identities (prompt §32).

IDs are uuid5-derived so tests and docs can reference them. No real customer data. Re-runnable.
Usage: python -m finagent.database.seeds
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from finagent.core.config import LOCAL_ISSUER
from finagent.database.models import EscalationRule, IntegrationConnection, Principal, RoleAssignment, Tenant
from finagent.database.session import session_factory, transaction

NS = uuid.UUID("6f1c6b7e-1d5a-4b8e-9c0e-5a1f00000000")
ALERT_FAMILY = "TM_STRUCTURING"  # DEC-02 local default; synthetic


def sid(name: str) -> uuid.UUID:
    return uuid.uuid5(NS, name)


@dataclass(frozen=True)
class SeedUser:
    key: str  # e.g. "a.analyst1"; token subject is "<key>@synthetic"
    roles: tuple[str, ...]
    kind: str = "USER"


TENANTS = {"a": "Synthetic Bank A", "b": "Synthetic Bank B"}
USERS = (
    SeedUser("analyst1", ("ANALYST",)),
    SeedUser("analyst2", ("ANALYST",)),
    SeedUser("reviewer1", ("REVIEWER",)),
    SeedUser("supervisor1", ("AML_SUPERVISOR",)),
    SeedUser("tenantadmin1", ("TENANT_ADMIN",)),
    SeedUser("policyadmin1", ("POLICY_ADMIN",)),
    SeedUser("auditor1", ("AUDITOR",)),
    SeedUser("operator1", ("PLATFORM_OPERATOR",)),
    SeedUser("responder1", ("BREAK_GLASS",)),  # eligible only; access needs an activated grant
    SeedUser("tm-connector", ("INTEGRATION",), kind="INTEGRATION"),
)

# FR-011 due times. PROPOSED values only - the SLA matrix is an OPEN decision (DEC-06).
ESCALATION_RULES = (
    ("unassigned-in-queue", "RECEIVED", 240, "AML_SUPERVISOR"),
    ("assigned-not-started", "ASSIGNED", 1440, "AML_SUPERVISOR"),
    ("blocked-too-long", "BLOCKED", 2880, "AML_SUPERVISOR"),
    ("review-overdue", "AWAITING_REVIEW", 4320, "AML_SUPERVISOR"),
    ("finalization-stuck", "FINALIZING", 60, "PLATFORM_OPERATOR"),
)


def subject(tenant: str, key: str) -> str:
    return f"{tenant}.{key}@synthetic"


def seed(session: Session) -> None:
    for slug, name in TENANTS.items():
        tenant_id = sid(f"tenant:{slug}")
        if session.get(Tenant, tenant_id) is None:
            session.add(
                Tenant(id=tenant_id, slug=f"synthetic-bank-{slug}", display_name=name, is_synthetic=True)
            )
            session.flush()
        for user in USERS:
            pid = sid(f"principal:{slug}:{user.key}")
            if session.get(Principal, pid) is None:
                session.add(
                    Principal(
                        id=pid,
                        tenant_id=tenant_id,
                        kind=user.kind,
                        issuer=LOCAL_ISSUER,
                        subject=subject(slug, user.key),
                        display_name=f"{name} {user.key}",
                    )
                )
                session.flush()
            for role in user.roles:
                exists = session.scalar(
                    select(RoleAssignment.id).where(
                        RoleAssignment.tenant_id == tenant_id,
                        RoleAssignment.principal_id == pid,
                        RoleAssignment.role == role,
                    )
                )
                if exists is None:
                    session.add(
                        RoleAssignment(
                            tenant_id=tenant_id, principal_id=pid, role=role, granted_by="seed:synthetic"
                        )
                    )
            if (
                user.kind == "INTEGRATION"
                and session.scalar(
                    select(IntegrationConnection.id).where(IntegrationConnection.principal_id == pid)
                )
                is None
            ):
                session.add(
                    IntegrationConnection(
                        id=sid(f"conn:{slug}"),
                        tenant_id=tenant_id,
                        principal_id=pid,
                        source_system=f"tm-synthetic-{slug}",
                        permitted_alert_types=[ALERT_FAMILY],
                    )
                )
        for key, status, minutes, owner in ESCALATION_RULES:
            rid = sid(f"escalation-rule:{slug}:{key}")
            if session.get(EscalationRule, rid) is None:
                session.add(
                    EscalationRule(
                        id=rid,
                        tenant_id=tenant_id,
                        rule_key=key,
                        applies_to_status=status,
                        due_after_minutes=minutes,
                        owner_role=owner,
                    )
                )
        session.flush()


def main() -> None:
    url = os.environ["FINAGENT_DATABASE_URL"]
    with transaction(session_factory(url)) as session:
        seed(session)
    print("seeded synthetic tenants:", ", ".join(TENANTS.values()))


if __name__ == "__main__":
    main()
