"""Owner-only enrollment. Run in a private interactive terminal, never capture output."""

from __future__ import annotations

import base64
import getpass
import os
import secrets
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select

from finagent.core.config import get_settings
from finagent.database.models import PasswordCredential, Principal, Tenant, TotpFactor
from finagent.database.session import session_factory, transaction
from finagent.security.authentication import totp
from finagent.services.audit_service import writer as audit


def main() -> None:
    settings = get_settings()
    totp.require_enabled(settings)
    url = os.environ.get("FINAGENT_MIGRATION_DATABASE_URL")
    if not url:
        raise SystemExit("Owner configuration required")
    principal_id = UUID(input("Existing human principal ID: ").strip())
    factory = session_factory(url)
    # Check before showing a secret; never silently replace an enrolled authenticator.
    with transaction(factory) as session:
        principal = session.get(Principal, principal_id)
        if principal is None or principal.kind != "USER" or principal.status != "ACTIVE":
            raise SystemExit("Active human principal required")
        tenant = session.get(Tenant, principal.tenant_id)
        credential = session.scalar(
            select(PasswordCredential).where(PasswordCredential.principal_id == principal.id)
        )
        if (
            tenant is None
            or not tenant.is_synthetic
            or tenant.status != "ACTIVE"
            or credential is None
            or not credential.enabled
        ):
            raise SystemExit("Active synthetic tenant and password account required")
        if totp.factor_for(session, credential) is not None:
            raise SystemExit("Authenticator already enrolled; no replacement made")
        tenant_id = tenant.id
    secret = secrets.token_bytes(20)
    encoded = base64.b32encode(secret).decode("ascii")
    print("PRIVATE AUTHENTICATOR SETUP: do not paste this screen into chat or save it in reports.")
    print("Add a manual account in your authenticator app: FinAgent local synthetic reviewer.")
    print("Type: time-based; SHA1; 6 digits; 30 seconds.")
    print("Setup key: " + encoded)
    code = getpass.getpass("Authenticator code (hidden): ").strip()
    counter = totp.matching_counter(secret, code, datetime.now(UTC))
    if counter is None:
        raise SystemExit("Enrollment code not accepted; no factor saved")
    with transaction(factory) as session:
        # Lock the principal to serialize competing owner enrollment attempts.
        principal = session.scalar(select(Principal).where(Principal.id == principal_id).with_for_update())
        tenant = session.get(Tenant, tenant_id)
        credential = session.scalar(
            select(PasswordCredential).where(PasswordCredential.principal_id == principal_id)
        )
        if (
            principal is None
            or principal.tenant_id != tenant_id
            or principal.kind != "USER"
            or principal.status != "ACTIVE"
            or tenant is None
            or tenant.status != "ACTIVE"
            or not tenant.is_synthetic
            or credential is None
            or not credential.enabled
            or totp.factor_for(session, credential) is not None
        ):
            raise SystemExit("Account changed during enrollment; no factor saved")
        factor = TotpFactor(
            tenant_id=tenant_id,
            principal_id=principal_id,
            encrypted_secret=totp.seal(settings, tenant_id, principal_id, secret),
            last_counter=counter,
        )
        session.add(factor)
        session.flush()
        audit.record(
            session,
            tenant_id=tenant_id,
            action="totp_enrolled",
            object_type="totp_factor",
            object_id=str(factor.id),
            outcome="SUCCESS",
            actor_principal_id=None,
            actor_kind="SYSTEM",
            details={"reason": "Owner-supervised authenticator possession verified; no session elevated"},
        )
    print("AUTHENTICATOR_ENROLLED; NO_SESSION_ELEVATED; EXISTING_ROLES_UNCHANGED")


if __name__ == "__main__":
    main()
