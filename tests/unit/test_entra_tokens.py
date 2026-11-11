"""Offline RSA token checks; no Entra tenant, discovery request, or JWKS network call."""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from finagent.core.errors import AppError, ErrorCode
from finagent.security.authentication.tokens import EntraTokenValidator

ISSUER = "https://login.microsoftonline.com/11111111-1111-1111-1111-111111111111/v2.0"
AUDIENCE = "22222222-2222-2222-2222-222222222222"
SUBJECT = "33333333-3333-3333-3333-333333333333"


@pytest.fixture(scope="module")
def signing_key() -> Any:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def claims(**changes: object) -> dict[str, Any]:
    now = int(time.time())
    return {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "oid": SUBJECT,
        "sub": "different-pairwise-subject",
        "iat": now,
        "nbf": now - 1,
        "exp": now + 600,
        "scp": "access_as_user other_scope",
        **changes,
    }


def validator(signing_key: Any, monkeypatch: pytest.MonkeyPatch) -> EntraTokenValidator:
    result = EntraTokenValidator(ISSUER, AUDIENCE, "https://unused.invalid/keys")
    monkeypatch.setattr(
        result._jwks,
        "get_signing_key_from_jwt",
        lambda _: SimpleNamespace(key=signing_key.public_key()),
    )
    return result


@pytest.mark.req("FR-001", "SPR-004", "SPR-005")
@pytest.mark.parametrize(
    ("amr", "expected"),
    [
        (None, False),
        ([], False),
        (["pwd"], False),
        (["otp"], False),
        (["rsa"], False),
        (["fido"], False),
        ("mfa", False),
        (["pwd", "mfa"], True),
    ],
)
def test_entra_requires_explicit_mfa_evidence(
    signing_key: Any, monkeypatch: pytest.MonkeyPatch, amr: object, expected: bool
) -> None:
    token = jwt.encode(claims(amr=amr), signing_key, algorithm="RS256")
    identity = validator(signing_key, monkeypatch).validate(token)
    assert identity.subject == SUBJECT
    assert identity.issuer == ISSUER
    assert identity.mfa is expected
    assert identity.scopes == frozenset({"access_as_user", "other_scope"})


@pytest.mark.req("FR-001", "SPR-004")
@pytest.mark.parametrize(
    "changes",
    [
        {"aud": "wrong-api"},
        {"iss": "https://wrong.invalid"},
        {"exp": 1},
        {"nbf": 9999999999},
        {"oid": ""},
        {"oid": None},
    ],
)
def test_entra_rejects_invalid_signed_claims(
    signing_key: Any, monkeypatch: pytest.MonkeyPatch, changes: dict[str, object]
) -> None:
    token = jwt.encode(claims(**changes), signing_key, algorithm="RS256")
    with pytest.raises(AppError) as exc:
        validator(signing_key, monkeypatch).validate(token)
    assert exc.value.code is ErrorCode.UNAUTHENTICATED


@pytest.mark.req("FR-001", "SPR-004")
def test_entra_rejects_forged_signature(signing_key: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    forged = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = jwt.encode(claims(), forged, algorithm="RS256")
    with pytest.raises(AppError):
        validator(signing_key, monkeypatch).validate(token)


@pytest.mark.req("FR-001", "SPR-004")
def test_entra_does_not_take_identity_from_roles_or_email(
    signing_key: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = jwt.encode(
        claims(scp=None, roles=["TENANT_ADMIN"], email="administrator@example.invalid"),
        signing_key,
        algorithm="RS256",
    )
    identity = validator(signing_key, monkeypatch).validate(token)
    assert identity.subject == SUBJECT
    assert not identity.scopes
    assert not identity.mfa
