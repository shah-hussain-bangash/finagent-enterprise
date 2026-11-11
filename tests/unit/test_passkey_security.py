"""Real library signature checks using a virtual authenticator; no database claims."""

import base64
import copy
from uuid import uuid4

import pytest
from pydantic import ValidationError
from webauthn.helpers import bytes_to_base64url as encode
from webauthn.helpers.exceptions import WebAuthnException

from finagent.core.config import Settings
from finagent.database.models import PasskeyFactor
from finagent.security.authentication import passkeys
from tests.passkey_helpers import ORIGIN, RP, VirtualAuthenticator

pytestmark = [pytest.mark.release_blocker, pytest.mark.req("FR-001", "SPR-005")]
PRINCIPAL = str(uuid4())
CHALLENGE = b"test-only-passkey-challenge-32bytes"


def config(**changes):  # type: ignore[no-untyped-def]
    values = dict(
        environment="test",
        auth_mode="google",
        google_web_client_id="test.apps.googleusercontent.com",
        database_url="postgresql+psycopg://unused@localhost/unused",
        redis_url="redis://localhost/0",
        password_login_enabled=True,
        email_verification_enabled=True,
        gmail_sender="synthetic@gmail.com",
        gmail_app_password="abcdefghijklmnop",
        email_verification_key=base64.b64encode(b"K" * 32).decode(),
        local_passkeys_enabled=True,
        passkey_rp_id=RP,
        passkey_origin=ORIGIN,
        passkey_enrollment_principals=[PRINCIPAL],
    )
    values.update(changes)
    return Settings(_env_file=None, **values)


def test_real_registration_and_signature():
    auth = VirtualAuthenticator()
    registration = passkeys.verify_registration(config(), CHALLENGE, auth.registration(encode(CHALLENGE)))
    factor = PasskeyFactor(
        tenant_id=uuid4(),
        principal_id=uuid4(),
        credential_id=encode(registration.credential_id),
        public_key=encode(registration.credential_public_key),
        sign_count=0,
    )
    result = passkeys.verify_assertion(config(), CHALLENGE, auth.assertion(encode(CHALLENGE)), factor)
    assert result.user_verified and result.new_sign_count == 1


@pytest.mark.parametrize(
    "change",
    [
        {"origin": "https://untrusted.invalid"},
        {"rp": "untrusted.invalid"},
        {"flags": 0x41},
        {"flags": 0x44},
        {"crossOrigin": True},
        {"topOrigin": "https://untrusted.invalid"},
    ],
)
def test_registration_rejects_boundaries(change):  # type: ignore[no-untyped-def]
    auth = VirtualAuthenticator()
    with pytest.raises((WebAuthnException, ValueError)):
        passkeys.verify_registration(config(), CHALLENGE, auth.registration(encode(CHALLENGE), **change))


def test_registration_rejects_wrong_challenge():
    with pytest.raises(WebAuthnException):
        passkeys.verify_registration(
            config(), CHALLENGE, VirtualAuthenticator().registration(encode(b"other"))
        )


@pytest.mark.parametrize(
    "change",
    [
        {"origin": "https://untrusted.invalid"},
        {"rp": "untrusted.invalid"},
        {"flags": 1},
        {"flags": 4},
        {"handle": encode(b"other account")},
        {"crossOrigin": True},
        {"count": 0},
    ],
)
def test_assertion_rejects_boundaries(change):  # type: ignore[no-untyped-def]
    auth = VirtualAuthenticator()
    registered = passkeys.verify_registration(config(), CHALLENGE, auth.registration(encode(CHALLENGE)))
    factor = PasskeyFactor(
        tenant_id=uuid4(),
        principal_id=uuid4(),
        credential_id=encode(auth.credential_id),
        public_key=encode(registered.credential_public_key),
        sign_count=1,
    )
    with pytest.raises((WebAuthnException, ValueError)):
        passkeys.verify_assertion(config(), CHALLENGE, auth.assertion(encode(CHALLENGE), **change), factor)


@pytest.mark.parametrize("mutation", ["signature", "credential", "challenge"])
def test_assertion_rejects_forgery(mutation):  # type: ignore[no-untyped-def]
    auth = VirtualAuthenticator()
    registered = passkeys.verify_registration(config(), CHALLENGE, auth.registration(encode(CHALLENGE)))
    factor = PasskeyFactor(
        tenant_id=uuid4(),
        principal_id=uuid4(),
        credential_id=encode(auth.credential_id),
        public_key=encode(registered.credential_public_key),
        sign_count=0,
    )
    response = copy.deepcopy(auth.assertion(encode(CHALLENGE)))
    if mutation == "signature":
        response["response"]["signature"] = encode(b"forged")
    if mutation == "credential":
        response["id"] = response["rawId"] = encode(b"another")
    if mutation == "challenge":
        response = auth.assertion(encode(b"other"))
    with pytest.raises((WebAuthnException, ValueError)):
        passkeys.verify_assertion(config(), CHALLENGE, response, factor)


def test_zero_counter_synced_passkey_uses_fresh_challenge():
    auth = VirtualAuthenticator()
    registered = passkeys.verify_registration(config(), CHALLENGE, auth.registration(encode(CHALLENGE)))
    factor = PasskeyFactor(
        tenant_id=uuid4(),
        principal_id=uuid4(),
        credential_id=encode(auth.credential_id),
        public_key=encode(registered.credential_public_key),
        sign_count=0,
    )
    assert (
        passkeys.verify_assertion(
            config(), CHALLENGE, auth.assertion(encode(CHALLENGE), count=0), factor
        ).new_sign_count
        == 0
    )


@pytest.mark.parametrize(
    "change",
    [
        {"environment": "production"},
        {"passkey_origin": "http://" + RP},
        {"passkey_origin": ORIGIN + "/path"},
        {"passkey_rp_id": "127.0.0.1"},
        {"passkey_enrollment_principals": []},
        {"passkey_enrollment_principals": ["bad"]},
        {"email_verification_enabled": False},
    ],
)
def test_fail_closed_configuration(change):  # type: ignore[no-untyped-def]
    with pytest.raises(ValidationError):
        config(**change)
