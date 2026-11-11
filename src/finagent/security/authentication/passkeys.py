"""Local/test WebAuthn step-up; no passwordless sign-in or client authority claims."""

from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session
from webauthn import (
    base64url_to_bytes,
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import bytes_to_base64url
from webauthn.helpers.exceptions import WebAuthnException
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialCreationOptions,
    PublicKeyCredentialDescriptor,
    PublicKeyCredentialRequestOptions,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from finagent.core.config import LOCAL_ONLY_ENVIRONMENTS, Settings
from finagent.core.errors import AppError, ErrorCode
from finagent.database.models import (
    PasskeyChallenge,
    PasskeyFactor,
    PasskeyGrant,
    PasswordCredential,
    PasswordSession,
)
from finagent.security.authentication import email_verification
from finagent.security.authentication.passwords import validate_session, verify_password
from finagent.security.authentication.tokens import VerifiedIdentity
from finagent.services.audit_service import writer as audit


def require_enabled(settings: Settings) -> None:
    if not settings.local_passkeys_enabled or settings.environment not in LOCAL_ONLY_ENVIRONMENTS:
        raise AppError(ErrorCode.FEATURE_DISABLED)


def user_handle(tenant: UUID, principal: UUID) -> bytes:
    return hashlib.sha256(f"finagent-passkey-v1:{tenant}:{principal}".encode()).digest()


def event(session: Session, credential: PasswordCredential, action: str, outcome: str) -> None:
    audit.record(
        session,
        tenant_id=credential.tenant_id,
        action=action,
        object_type="passkey",
        object_id=str(credential.principal_id),
        outcome=outcome,
        actor_principal_id=credential.principal_id,
        actor_kind="USER",
    )


def factor_for(
    session: Session, credential: PasswordCredential, *, lock: bool = False
) -> PasskeyFactor | None:
    query = select(PasskeyFactor).where(
        PasskeyFactor.tenant_id == credential.tenant_id,
        PasskeyFactor.principal_id == credential.principal_id,
    )
    return session.scalar(query.with_for_update() if lock else query)


def _locked_login(session: Session, token: str) -> tuple[PasswordSession, PasswordCredential]:
    login, credential = email_verification.binding(session, token)
    # Serialize against logout and owner password reset; refresh identity-map values after waiting.
    session.scalar(
        select(PasswordSession)
        .where(
            PasswordSession.id == login.id,
            PasswordSession.tenant_id == login.tenant_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    session.scalar(
        select(PasswordCredential)
        .where(
            PasswordCredential.id == credential.id,
            PasswordCredential.tenant_id == credential.tenant_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    validate_session(session, token)
    return login, credential


def begin(
    session: Session,
    settings: Settings,
    token: str,
    purpose: str,
    password: str | None = None,
) -> dict[str, Any] | None:
    require_enabled(settings)
    login, credential = _locked_login(session, token)
    factor = factor_for(session, credential)
    now = datetime.now(UTC)
    if purpose == "REGISTER":
        allowed = (
            str(credential.principal_id) in settings.passkey_enrollment_principals
            and email_verification.recently_verified(session, settings, token)
            and factor is None
            and not (credential.locked_until and credential.locked_until > now)
            and verify_password(password or "", credential.password_hash)
        )
        if not allowed:
            event(session, credential, "passkey_enrollment_started", "DENIED")
            return None
    elif purpose != "VERIFY" or factor is None or not factor.enabled:
        return None
    session.execute(
        update(PasskeyChallenge)
        .where(
            PasskeyChallenge.tenant_id == login.tenant_id,
            PasskeyChallenge.password_session_id == login.id,
            PasskeyChallenge.purpose == purpose,
            PasskeyChallenge.consumed_at.is_(None),
        )
        .values(consumed_at=now)
    )
    challenge = secrets.token_bytes(32)
    row = PasskeyChallenge(
        tenant_id=login.tenant_id,
        password_session_id=login.id,
        credential_id=credential.id,
        credential_version=credential.version,
        purpose=purpose,
        challenge=bytes_to_base64url(challenge),
        rp_id=settings.passkey_rp_id,
        origin=settings.passkey_origin,
        created_at=now,
        expires_at=min(now + timedelta(seconds=120), login.expires_at),
    )
    session.add(row)
    session.flush()
    options: PublicKeyCredentialCreationOptions | PublicKeyCredentialRequestOptions
    if purpose == "REGISTER":
        options = generate_registration_options(
            rp_id=settings.passkey_rp_id,
            rp_name="FinAgent local synthetic",
            user_name=credential.email,
            user_id=user_handle(credential.tenant_id, credential.principal_id),
            challenge=challenge,
            timeout=120000,
            authenticator_selection=AuthenticatorSelectionCriteria(
                resident_key=ResidentKeyRequirement.REQUIRED,
                user_verification=UserVerificationRequirement.REQUIRED,
            ),
        )
    else:
        if factor is None:
            raise AppError(ErrorCode.FORBIDDEN)
        options = generate_authentication_options(
            rp_id=settings.passkey_rp_id,
            challenge=challenge,
            timeout=120000,
            allow_credentials=[PublicKeyCredentialDescriptor(id=base64url_to_bytes(factor.credential_id))],
            user_verification=UserVerificationRequirement.REQUIRED,
        )
    event(session, credential, "passkey_challenge_issued", "SUCCESS")
    return {"challenge_id": str(row.id), "publicKey": json.loads(options_to_json(options))}


def _client_boundary(response: dict[str, Any]) -> None:
    client = json.loads(base64url_to_bytes(response["response"]["clientDataJSON"]))
    if not isinstance(client, dict) or client.get("crossOrigin", False) is not False or "topOrigin" in client:
        raise ValueError("Embedded authentication is not permitted")


def verify_registration(settings: Settings, challenge: bytes, response: dict[str, Any]) -> Any:
    # Library validates challenge, origin, RP hash, UP, UV and credential structure/algorithm.
    _client_boundary(response)
    return verify_registration_response(
        credential=response,
        expected_challenge=challenge,
        expected_rp_id=settings.passkey_rp_id,
        expected_origin=settings.passkey_origin,
        require_user_presence=True,
        require_user_verification=True,
    )


def verify_assertion(
    settings: Settings,
    challenge: bytes,
    response: dict[str, Any],
    factor: PasskeyFactor,
) -> Any:
    _client_boundary(response)
    if response.get("id") != factor.credential_id:
        raise ValueError("Credential binding mismatch")
    supplied = response.get("response", {}).get("userHandle")
    if supplied is not None and supplied != bytes_to_base64url(
        user_handle(factor.tenant_id, factor.principal_id)
    ):
        raise ValueError("Account binding mismatch")
    return verify_authentication_response(
        credential=response,
        expected_challenge=challenge,
        expected_rp_id=settings.passkey_rp_id,
        expected_origin=settings.passkey_origin,
        credential_public_key=base64url_to_bytes(factor.public_key),
        credential_current_sign_count=factor.sign_count,
        require_user_verification=True,
    )


def finish(
    session: Session,
    settings: Settings,
    token: str,
    challenge_id: UUID,
    purpose: str,
    response: dict[str, Any],
) -> datetime | bool | None:
    """Denied matching challenges are consumed and audited; caller must commit before 403."""
    require_enabled(settings)
    login, credential = _locked_login(session, token)
    row = session.scalar(
        select(PasskeyChallenge)
        .where(
            PasskeyChallenge.id == challenge_id,
            PasskeyChallenge.tenant_id == login.tenant_id,
            PasskeyChallenge.password_session_id == login.id,
            PasskeyChallenge.credential_id == credential.id,
            PasskeyChallenge.credential_version == credential.version,
            PasskeyChallenge.purpose == purpose,
        )
        .with_for_update()
    )
    now = datetime.now(UTC)
    if row is None or row.consumed_at is not None:
        event(session, credential, "passkey_response", "DENIED")
        return None
    row.consumed_at = now
    if row.expires_at <= now or row.rp_id != settings.passkey_rp_id or row.origin != settings.passkey_origin:
        event(session, credential, "passkey_response", "DENIED")
        return None
    factor = factor_for(session, credential, lock=True)
    try:
        if purpose == "REGISTER":
            if (
                factor is not None
                or str(credential.principal_id) not in settings.passkey_enrollment_principals
                or not email_verification.recently_verified(session, settings, token)
            ):
                raise ValueError("Enrollment not permitted")
            registration = verify_registration(settings, base64url_to_bytes(row.challenge), response)
            credential_id = bytes_to_base64url(registration.credential_id)
            if session.scalar(select(PasskeyFactor.id).where(PasskeyFactor.credential_id == credential_id)):
                raise ValueError("Credential already registered")
            session.add(
                PasskeyFactor(
                    tenant_id=credential.tenant_id,
                    principal_id=credential.principal_id,
                    credential_id=credential_id,
                    public_key=bytes_to_base64url(registration.credential_public_key),
                    sign_count=registration.sign_count,
                    enabled=True,
                    version=1,
                )
            )
            event(session, credential, "passkey_registered", "SUCCESS")
            return True  # Enrollment never creates an MFA grant.
        if purpose != "VERIFY" or factor is None or not factor.enabled:
            raise ValueError("Passkey unavailable")
        verified = verify_assertion(settings, base64url_to_bytes(row.challenge), response, factor)
    except (WebAuthnException, ValueError, TypeError, KeyError, AttributeError):
        event(session, credential, "passkey_response", "DENIED")
        return None
    factor.sign_count = verified.new_sign_count
    expires = min(now + timedelta(seconds=300), login.expires_at)
    grant = session.scalar(
        select(PasskeyGrant).where(
            PasskeyGrant.tenant_id == login.tenant_id,
            PasskeyGrant.password_session_id == login.id,
        )
    )
    if grant is None:
        grant = PasskeyGrant(
            tenant_id=login.tenant_id,
            password_session_id=login.id,
            factor_id=factor.id,
            factor_version=factor.version,
            verified_at=now,
            expires_at=expires,
        )
        session.add(grant)
    else:
        grant.factor_id, grant.factor_version = factor.id, factor.version
        grant.verified_at, grant.expires_at = now, expires
    event(session, credential, "passkey_verified", "SUCCESS")
    return expires


def identity_with_step_up(
    session: Session,
    settings: Settings,
    token: str,
    identity: VerifiedIdentity,
) -> VerifiedIdentity:
    if not settings.local_passkeys_enabled:
        return identity
    require_enabled(settings)
    login, credential = email_verification.binding(session, token)
    now = datetime.now(UTC)
    grant = session.scalar(
        select(PasskeyGrant)
        .join(
            PasskeyFactor,
            (PasskeyFactor.id == PasskeyGrant.factor_id)
            & (PasskeyFactor.tenant_id == PasskeyGrant.tenant_id),
        )
        .where(
            PasskeyGrant.tenant_id == login.tenant_id,
            PasskeyGrant.password_session_id == login.id,
            PasskeyFactor.principal_id == credential.principal_id,
            PasskeyFactor.enabled.is_(True),
            PasskeyFactor.version == PasskeyGrant.factor_version,
            PasskeyGrant.verified_at <= now,
            PasskeyGrant.expires_at > now,
            PasskeyGrant.expires_at <= login.expires_at,
        )
    )
    return replace(identity, mfa=identity.mfa or grant is not None)


def revoke(session: Session, settings: Settings, token: str) -> bool:
    require_enabled(settings)
    login, credential = _locked_login(session, token)
    factor = factor_for(session, credential, lock=True)
    now = datetime.now(UTC)
    grant = session.scalar(
        select(PasskeyGrant).where(
            PasskeyGrant.tenant_id == login.tenant_id,
            PasskeyGrant.password_session_id == login.id,
            PasskeyGrant.expires_at > now,
            PasskeyGrant.verified_at <= now,
            PasskeyGrant.expires_at <= login.expires_at,
        )
    )
    if (
        factor is None
        or not factor.enabled
        or grant is None
        or grant.factor_id != factor.id
        or grant.factor_version != factor.version
    ):
        return False
    factor.enabled, factor.version = False, factor.version + 1
    event(session, credential, "passkey_revoked", "SUCCESS")
    return True
