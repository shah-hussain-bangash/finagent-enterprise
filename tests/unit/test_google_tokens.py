"""Offline Google first-party credential verification with real RSA signatures."""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from finagent.core.config import AuthMode, Settings
from finagent.core.errors import AppError, ErrorCode
from finagent.security.authentication.tokens import (
    GoogleTokenValidator,
    build_validator,
)

CLIENT = "123456789-synthetic.apps.googleusercontent.com"
SUBJECT = "123456789012345678901"


@pytest.fixture(scope="module")
def signing_key() -> Any:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def claims(**changes: object) -> dict[str, Any]:
    now = int(time.time())
    return {
        "iss": "https://accounts.google.com",
        "aud": CLIENT,
        "sub": SUBJECT,
        "iat": now,
        "exp": now + 600,
        **changes,
    }


def validator(signing_key: Any, monkeypatch: pytest.MonkeyPatch) -> GoogleTokenValidator:
    result = GoogleTokenValidator(CLIENT)
    monkeypatch.setattr(
        result._jwks,
        "get_signing_key_from_jwt",
        lambda _: SimpleNamespace(key=signing_key.public_key()),
    )
    return result


@pytest.mark.req("FR-001", "SPR-004", "SPR-005")
@pytest.mark.parametrize("amr", [None, [], ["pwd"], ["mfa"], ["pwd", "otp"], "mfa"])
def test_google_identity_uses_sub_and_never_infers_mfa(
    signing_key: Any, monkeypatch: pytest.MonkeyPatch, amr: object
) -> None:
    token = jwt.encode(
        claims(amr=amr, email="admin@example.invalid", roles=["TENANT_ADMIN"], tid="other"),
        signing_key,
        algorithm="RS256",
    )
    identity = validator(signing_key, monkeypatch).validate(token)
    assert identity.subject == SUBJECT
    assert identity.issuer == "https://accounts.google.com"
    assert identity.mfa is False
    assert not identity.scopes


@pytest.mark.req("FR-001", "SPR-004")
@pytest.mark.parametrize(
    "changes",
    [
        {"aud": "other-client"},
        {"aud": [CLIENT, "other-client"]},
        {"azp": "other-client"},
        {"iss": "https://attacker.invalid"},
        {"iss": "accounts.google.com"},
        {"sub": ""},
        {"sub": None},
        {"sub": "x" * 256},
        {"exp": 1},
        {"exp": True},
        {"iat": True},
        {"iat": 9999999999},
        {"exp": int(time.time()) + 7200},
    ],
)
def test_google_rejects_invalid_signed_claims(
    signing_key: Any, monkeypatch: pytest.MonkeyPatch, changes: dict[str, object]
) -> None:
    token = jwt.encode(claims(**changes), signing_key, algorithm="RS256")
    with pytest.raises(AppError) as exc:
        validator(signing_key, monkeypatch).validate(token)
    assert exc.value.code is ErrorCode.UNAUTHENTICATED


@pytest.mark.req("FR-001", "SPR-004")
def test_google_rejects_forged_signature(signing_key: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    forged = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = jwt.encode(claims(), forged, algorithm="RS256")
    with pytest.raises(AppError):
        validator(signing_key, monkeypatch).validate(token)


@pytest.mark.req("FR-001", "SPR-004")
@pytest.mark.parametrize("token", ["", "opaque-google-api-access-token", "x" * 24577])
def test_google_rejects_non_credentials(
    signing_key: Any, monkeypatch: pytest.MonkeyPatch, token: str
) -> None:
    with pytest.raises(AppError):
        validator(signing_key, monkeypatch).validate(token)


@pytest.mark.req("FR-001", "SPR-004")
def test_google_requires_explicit_client_configuration() -> None:
    values = {
        "environment": "test",
        "database_url": "postgresql://unused",
        "auth_mode": "google",
    }
    with pytest.raises(ValueError):
        Settings(_env_file=None, google_web_client_id=None, **values)
    for client_id in ("invalid", "", "https://attacker.invalid"):
        with pytest.raises(ValueError):
            Settings(_env_file=None, google_web_client_id=client_id, **values)
    settings = Settings(_env_file=None, google_web_client_id=CLIENT, **values)
    assert settings.auth_mode is AuthMode.GOOGLE
    assert isinstance(build_validator(settings), GoogleTokenValidator)
