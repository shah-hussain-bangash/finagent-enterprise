"""Security regressions for independent password login; no database impersonation."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import redis
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from finagent.api.main import create_app
from finagent.api.routers.password_auth import PasswordLogin, throttle
from finagent.core.config import Settings
from finagent.core.errors import AppError, ErrorCode
from finagent.security.authentication import passwords

pytestmark = [pytest.mark.release_blocker, pytest.mark.req("FR-001", "SPR-004")]
PASSWORD = "Synthetic testing passphrase only 901!"


@pytest.fixture
def config() -> Settings:
    return Settings(
        _env_file=None,
        environment="test",
        auth_mode="google",
        google_web_client_id="synthetic.apps.googleusercontent.com",
        database_url="postgresql+psycopg://unused:unused@127.0.0.1:1/unused",
        password_login_enabled=True,
        redis_url="redis://127.0.0.1:1/0",
    )


def test_salted_verifiers_do_not_store_passwords() -> None:
    first, second = passwords.hash_password(PASSWORD), passwords.hash_password(PASSWORD)
    assert PASSWORD not in first and first != second
    assert passwords.verify_password(PASSWORD, first)
    assert not passwords.verify_password(PASSWORD + "x", first)
    assert not passwords.verify_password(PASSWORD.lower(), first)


@pytest.mark.parametrize("password", ["", "short", "x" * 14, "x" * 129])
def test_password_provisioning_rejects_invalid_lengths(password: str) -> None:
    with pytest.raises(ValueError):
        passwords.hash_password(password)


@pytest.mark.parametrize(
    "encoded", ["", "plaintext", "scrypt-v1$999999999$8$3$00$00", "scrypt-v1$32768$8$3$zz$zz"]
)
def test_untrusted_hash_parameters_never_execute(encoded: str) -> None:
    assert not passwords.verify_password(PASSWORD, encoded)


@pytest.mark.parametrize("value", ["not-an-email", "x@y", "a..b@example.com", "a@éxample.com", "x" * 255])
def test_invalid_email(value: str) -> None:
    assert passwords.normalize_email(value) is None


def test_email_normalization_preserves_provider_dots_and_plus_aliases() -> None:
    assert passwords.normalize_email(" Analyst+Demo@Example.COM ") == "analyst+demo@example.com"
    assert passwords.normalize_email("a.b@example.com") != passwords.normalize_email("ab@example.com")


def test_credentials_contract_rejects_role_and_tenant_claims() -> None:
    with pytest.raises(ValidationError):
        PasswordLogin(email="demo@example.com", password=PASSWORD, role="ANALYST", tenant_id="a")
    assert PASSWORD not in repr(PasswordLogin(email="demo@example.com", password=PASSWORD))


def test_strict_throttle_fails_closed_without_redis(
    config: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    request = MagicMock()
    request.client.host = "127.0.0.1"
    # Connection construction is lazy in the real Redis library; mimic an eval outage.
    client = MagicMock()
    client.eval.side_effect = redis.ConnectionError()
    monkeypatch.setattr(redis.Redis, "from_url", lambda *a, **kw: client)
    with pytest.raises(AppError) as failure:
        throttle(request, config, "private@example.com")
    assert failure.value.code == ErrorCode.DEPENDENCY_UNAVAILABLE
    assert client.close.called


def test_throttle_keys_hide_email_and_ignore_forwarded_headers(
    config: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, request = MagicMock(), MagicMock()
    client.eval.return_value = 1
    request.client.host = "127.0.0.1"
    monkeypatch.setattr(redis.Redis, "from_url", lambda *a, **kw: client)
    throttle(request, config, "Private@Example.com")
    assert "private@example.com" not in repr(client.eval.call_args_list)
    assert client.eval.call_count == 2
    client.eval.return_value = 6
    with pytest.raises(AppError) as failure:
        throttle(request, config, "private@example.com")
    assert failure.value.code == ErrorCode.RATE_LIMITED
    assert failure.value.headers["Retry-After"] == "60"


def test_feature_defaults_off_and_requires_external_auth_and_redis(config: Settings) -> None:
    data = config.model_dump()
    for overrides in ({"redis_url": None}, {"auth_mode": "local", "dev_jwt_secret": SecretStr("x" * 40)}):
        with pytest.raises(ValidationError):
            Settings(_env_file=None, **(data | overrides))
    disabled = Settings(_env_file=None, **(data | {"password_login_enabled": False}))
    with TestClient(create_app(disabled)) as client:
        response = client.post(
            "/api/v1/auth/password/login", json={"email": "a@example.com", "password": PASSWORD}
        )
        assert response.status_code == 403
        assert client.get("/api/v1/auth/password/config").json() == {"enabled": False}


@pytest.mark.parametrize("enabled", [False, True])
def test_provider_config_contract_is_unchanged(config: Settings, enabled: bool) -> None:
    config.password_login_enabled = enabled
    with TestClient(create_app(config)) as client:
        assert client.get("/api/v1/auth/config").json() == {"mode": "google"}
        response = client.get("/api/v1/auth/password/config")
        assert response.status_code == 200
        assert response.json() == {"enabled": enabled}
        assert response.headers["Cache-Control"] == "no-store"


def test_http_validation_never_echoes_password(config: Settings) -> None:
    with TestClient(create_app(config)) as client:
        response = client.post(
            "/api/v1/auth/password/login",
            json={"email": "a@example.com", "password": PASSWORD, "roles": ["TENANT_ADMIN"]},
        )
        assert response.status_code == 400 and PASSWORD not in response.text
        assert response.headers["Cache-Control"] == "no-store"


def test_invalid_session_never_reaches_database() -> None:
    session = MagicMock()
    with pytest.raises(AppError):
        passwords.validate_session(session, "fa_pwd_tampered")
    assert not session.execute.called


def test_streamed_login_request_limit_does_not_trust_content_length(config: Settings) -> None:
    with TestClient(create_app(config)) as client:
        response = client.post(
            "/api/v1/auth/password/login",
            content=iter([b"x" * 3000, b"y" * 3000]),
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 413
        assert response.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"
