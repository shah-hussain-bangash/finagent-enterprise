"""RFC vectors and fail-closed cryptographic/configuration boundaries."""

import base64
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from finagent.core.config import Settings
from finagent.core.errors import AppError
from finagent.database.models import TotpFactor
from finagent.security.authentication import totp

pytestmark = [pytest.mark.release_blocker, pytest.mark.req("FR-001", "SPR-005")]
SECRET = b"12345678901234567890"


def config(**kwargs):  # type: ignore[no-untyped-def]
    values = dict(
        environment="test",
        auth_mode="google",
        google_web_client_id="test.apps.googleusercontent.com",
        database_url="postgresql+psycopg://unused@localhost/unused",
        redis_url="redis://localhost/0",
        password_login_enabled=True,
        local_totp_enabled=True,
        local_totp_key=base64.b64encode(b"K" * 32).decode(),
    )
    values.update(kwargs)
    return Settings(_env_file=None, **values)


@pytest.mark.parametrize(
    "timestamp,expected",
    [
        (59, "94287082"),
        (1111111109, "07081804"),
        (1111111111, "14050471"),
        (1234567890, "89005924"),
        (2000000000, "69279037"),
        (20000000000, "65353130"),
    ],
)
def test_rfc6238_sha1_vectors(timestamp, expected):  # type: ignore[no-untyped-def]
    assert totp.otp(SECRET, timestamp // 30, digits=8) == expected


@pytest.mark.parametrize("offset", [-1, 0, 1])
def test_bounded_clock_skew_and_replay(offset):  # type: ignore[no-untyped-def]
    now = datetime.fromtimestamp(1800000000, UTC)
    counter = int(now.timestamp()) // 30 + offset
    code = totp.otp(SECRET, counter)
    assert totp.matching_counter(SECRET, code, now) == counter
    assert totp.matching_counter(SECRET, code, now, last=counter) is None


@pytest.mark.parametrize(
    "code", ["", "12345", "1234567", "\uff11\uff12\uff13\uff14\uff15\uff16", "abcdef", " 12345"]
)
def test_code_format_is_ascii_six_digits(code):  # type: ignore[no-untyped-def]
    assert totp.matching_counter(SECRET, code, datetime.now(UTC)) is None


def test_old_and_future_steps_denied():
    now = datetime.fromtimestamp(1800000000, UTC)
    for delta in (-2, 2):
        assert totp.matching_counter(SECRET, totp.otp(SECRET, 60000000 + delta), now) is None


def test_encryption_is_random_authenticated_and_tenant_principal_bound():
    settings = config()
    tenant, principal = uuid4(), uuid4()
    encrypted = totp.seal(settings, tenant, principal, SECRET)
    assert encrypted != totp.seal(settings, tenant, principal, SECRET)
    assert SECRET.decode() not in encrypted
    factor = TotpFactor(tenant_id=tenant, principal_id=principal, encrypted_secret=encrypted)
    assert totp.unseal(settings, factor) == SECRET
    factor.principal_id = uuid4()
    with pytest.raises(AppError):
        totp.unseal(settings, factor)
    factor.principal_id = principal
    factor.tenant_id = uuid4()
    with pytest.raises(AppError):
        totp.unseal(settings, factor)


@pytest.mark.parametrize("key", ["", "invalid-base64", base64.b64encode(b"a" * 16).decode()])
def test_invalid_key_prevents_startup(key):  # type: ignore[no-untyped-def]
    with pytest.raises(ValidationError):
        config(local_totp_key=key)


@pytest.mark.parametrize("environment", ["dev", "staging", "production"])
def test_local_totp_cannot_start_outside_local_test(environment):  # type: ignore[no-untyped-def]
    with pytest.raises(ValidationError):
        config(environment=environment)


def test_disabled_feature_cannot_verify_or_encrypt():
    settings = config(local_totp_enabled=False)
    with pytest.raises(AppError):
        totp.require_enabled(settings)
    with pytest.raises(AppError):
        totp.seal(settings, uuid4(), uuid4(), SECRET)


def test_password_required_for_totp():
    with pytest.raises(ValidationError):
        config(password_login_enabled=False)
