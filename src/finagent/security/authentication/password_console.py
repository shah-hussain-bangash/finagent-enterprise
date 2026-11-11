"""Schema-owner provisioning console. Passwords are prompted, never command arguments.

No signup, automatic email-account linking or role grants. The operator must
select an existing human principal with the intended tenant and roles.
"""

from __future__ import annotations

import getpass
import os
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select

from finagent.database.models import PasswordCredential, Principal, RoleAssignment, Tenant
from finagent.database.session import session_factory, transaction
from finagent.security.authentication.passwords import hash_password, normalize_email
from finagent.services.audit_service import writer as audit


def main() -> None:
    # Credentials are managed with schema-owner privileges; the API role cannot
    # insert credentials or change their email, principal, enabled flag or hash.
    url = os.environ.get("FINAGENT_MIGRATION_DATABASE_URL")
    if not url:
        raise SystemExit("Set FINAGENT_MIGRATION_DATABASE_URL for the credential administrator.")
    factory = session_factory(url)
    with transaction(factory) as session:
        rows = session.execute(
            select(Principal, Tenant.slug)
            .join(Tenant, Tenant.id == Principal.tenant_id)
            .where(Principal.kind == "USER", Principal.status == "ACTIVE", Tenant.status == "ACTIVE")
            .order_by(Tenant.slug, Principal.display_name)
        ).all()
        for principal, tenant in rows:
            roles = session.scalars(
                select(RoleAssignment.role).where(
                    RoleAssignment.tenant_id == principal.tenant_id,
                    RoleAssignment.principal_id == principal.id,
                    RoleAssignment.valid_from <= datetime.now(UTC),
                    (RoleAssignment.valid_to.is_(None)) | (RoleAssignment.valid_to > datetime.now(UTC)),
                )
            ).all()
            print(f"{principal.id} | tenant={tenant} | {principal.display_name} | {','.join(roles)}")
    principal_id = UUID(input("Existing human principal ID: ").strip())
    email = normalize_email(input("FinAgent login email (not automatically linked to Google): "))
    if email is None:
        raise SystemExit("Invalid email address.")
    action = input("Action [set-password/disable]: ").strip()
    if action not in {"set-password", "disable"}:
        raise SystemExit("Unsupported action.")
    encoded = None
    if action == "set-password":
        first = getpass.getpass("New FinAgent password (15-128 characters; do not reuse Google password): ")
        if first != getpass.getpass("Confirm password: "):
            raise SystemExit("Passwords did not match.")
        encoded = hash_password(first)
        del first
    reason = input("Reason (10-300 characters): ").strip()
    if not 10 <= len(reason) <= 300:
        raise SystemExit("A reason of 10-300 characters is required.")
    with transaction(factory) as session:
        principal = session.get(Principal, principal_id)
        if principal is None or principal.kind != "USER" or principal.status != "ACTIVE":
            raise SystemExit("An active human principal is required.")
        tenant = session.get(Tenant, principal.tenant_id)
        if tenant is None or tenant.status != "ACTIVE":
            raise SystemExit("An active tenant is required.")
        row = session.scalar(
            select(PasswordCredential)
            .where(PasswordCredential.principal_id == principal.id)
            .with_for_update()
        )
        collision = session.scalar(select(PasswordCredential).where(PasswordCredential.email == email))
        if collision is not None and (row is None or collision.id != row.id):
            raise SystemExit("This email is already assigned to another login.")
        if row is None:
            if action == "disable" or encoded is None:
                raise SystemExit("No password login exists for this principal.")
            row = PasswordCredential(
                tenant_id=principal.tenant_id, principal_id=principal.id, email=email, password_hash=encoded
            )
            session.add(row)
            session.flush()
        else:
            if row.email != email:
                raise SystemExit("The selected principal's login email differs; no change made.")
            row.version += 1  # revokes every previously issued password session
            row.enabled = action != "disable"
            row.failed_attempts, row.locked_until = 0, None
            if encoded:
                row.password_hash = encoded
        audit.record(
            session,
            tenant_id=principal.tenant_id,
            action="password_credential_" + action,
            object_type="password_credential",
            object_id=str(row.id),
            outcome="SUCCESS",
            actor_principal_id=None,
            actor_kind="SYSTEM",
            details={"reason": reason},
        )
    print("PASSWORD_CREDENTIAL_UPDATED; EXISTING_ROLES_UNCHANGED; NO_MFA_GRANTED")


if __name__ == "__main__":
    main()
