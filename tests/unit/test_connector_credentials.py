"""Fail-closed connector token parsing, scope and configuration regressions."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError
from starlette.requests import Request

from finagent.api.deps import get_principal
from finagent.core.config import Environment, Settings
from finagent.core.errors import AppError, ErrorCode
from finagent.integrations.local_connector_demo import validate_demo_target
from finagent.security.authentication import connector_credentials as credentials

pytestmark = [pytest.mark.release_blocker, pytest.mark.req("FR-001", "SPR-004")]
SYNTHETIC_CREDENTIAL = "fa_int_" + "A" * 43


@pytest.fixture
def config() -> Settings:
    return Settings(
        _env_file=None,
        environment="test",
        auth_mode="google",
        google_web_client_id="synthetic.apps.googleusercontent.com",
        database_url="postgresql+psycopg://unused:unused@127.0.0.1:1/unused",
        local_connector_auth_enabled=True,
    )


@pytest.mark.parametrize(
    "value",
    ["", "fa_int_", "fa_int_" + "A" * 42, "fa_int_" + "A" * 44, "fa_int_" + "." * 43, "fa_pwd_" + "A" * 43],
)
def test_malformed_credentials_never_query_database(config: Settings, value: str) -> None:
    session = MagicMock()
    with pytest.raises(AppError) as failure:
        credentials.validate_credential(session, config, value)
    assert failure.value.code is ErrorCode.UNAUTHENTICATED
    assert not session.execute.called


def test_adapter_defaults_off_and_cannot_be_enabled_outside_local_test(config: Settings) -> None:
    values = config.model_dump()
    disabled = Settings(_env_file=None, **(values | {"local_connector_auth_enabled": False}))
    session = MagicMock()
    with pytest.raises(AppError):
        credentials.validate_credential(session, disabled, SYNTHETIC_CREDENTIAL)
    assert not session.execute.called
    for environment in ("dev", "staging", "production"):
        with pytest.raises(ValidationError, match="local connector credentials are forbidden"):
            Settings(_env_file=None, **(values | {"environment": environment}))


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/api/v1/cases"),
        ("GET", "/api/v1/auth/session"),
        ("POST", "/api/v1/workflows"),
        ("GET", "/api/v1/alerts"),
        ("POST", "/api/v1/alerts/unexpected"),
    ],
)
def test_credentials_are_route_and_method_bound_before_database(method: str, path: str) -> None:
    app = SimpleNamespace(state=SimpleNamespace(token_validator=MagicMock(), session_factory=MagicMock()))
    request = Request({"type": "http", "method": method, "path": path, "headers": [], "app": app})
    with pytest.raises(AppError) as failure:
        get_principal(request, "Bearer " + SYNTHETIC_CREDENTIAL)
    assert failure.value.code is ErrorCode.UNAUTHENTICATED
    assert not app.state.session_factory.called and not app.state.token_validator.validate.called


@pytest.mark.parametrize(
    "change",
    [
        "revoked",
        "expired",
        "future",
        "overlong",
        "scope",
        "human",
        "principal",
        "tenant",
        "real_tenant",
        "disabled",
        "nonlocal",
    ],
)
def test_record_and_runtime_constraints_fail_closed(config: Settings, change: str) -> None:
    now = datetime.now(UTC)
    row = SimpleNamespace(
        created_at=now - timedelta(seconds=1),
        expires_at=now + timedelta(seconds=600),
        revoked_at=None,
        scope="alert:ingest",
    )
    principal = SimpleNamespace(
        issuer="trusted-db-issuer", subject="registered-source", kind="INTEGRATION", status="ACTIVE"
    )
    tenant = SimpleNamespace(status="ACTIVE", is_synthetic=True)
    if change == "revoked":
        row.revoked_at = now
    elif change == "expired":
        row.expires_at = now - timedelta(seconds=1)
    elif change == "future":
        row.created_at = now + timedelta(minutes=1)
    elif change == "overlong":
        row.expires_at = now + timedelta(hours=2)
    elif change == "scope":
        row.scope = "case:read"
    elif change == "human":
        principal.kind = "USER"
    elif change == "principal":
        principal.status = "SUSPENDED"
    elif change == "tenant":
        tenant.status = "SUSPENDED"
    elif change == "real_tenant":
        tenant.is_synthetic = False
    elif change == "disabled":
        config.local_connector_auth_enabled = False
    else:
        config.environment = Environment.PRODUCTION
    session = MagicMock()
    session.execute.return_value.one_or_none.return_value = (row, principal, tenant)
    with pytest.raises(AppError) as failure:
        credentials.validate_credential(session, config, SYNTHETIC_CREDENTIAL)
    assert failure.value.code is ErrorCode.UNAUTHENTICATED


def test_identity_is_database_owned_and_never_mfa(config: Settings) -> None:
    now = datetime.now(UTC)
    row = SimpleNamespace(
        created_at=now - timedelta(seconds=1),
        expires_at=now + timedelta(seconds=600),
        revoked_at=None,
        scope="alert:ingest",
    )
    principal = SimpleNamespace(
        issuer="trusted-db-issuer", subject="registered-source", kind="INTEGRATION", status="ACTIVE"
    )
    session = MagicMock()
    session.execute.return_value.one_or_none.return_value = (
        row,
        principal,
        SimpleNamespace(status="ACTIVE", is_synthetic=True),
    )
    identity = credentials.validate_credential(session, config, SYNTHETIC_CREDENTIAL)
    assert identity.issuer == "trusted-db-issuer" and identity.subject == "registered-source"
    assert identity.mfa is False and not identity.scopes
    assert SYNTHETIC_CREDENTIAL not in credentials.digest(SYNTHETIC_CREDENTIAL)


@pytest.mark.parametrize("ttl", [0, 59, 3601, True])
def test_issuance_lifetime_is_bounded_before_any_database_access(config: Settings, ttl: int) -> None:
    session = MagicMock()
    with pytest.raises(ValueError):
        credentials.issue_local_credential(
            session, config, MagicMock(), ttl_seconds=ttl, reason="Synthetic local intake verification"
        )
    assert not session.get.called and not session.add.called


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com",
        "http://example.com:8001",
        "http://127.0.0.1:8001/private",
        "http://127.0.0.1:8001/?token=example",
        "http://127.0.0.1:8001/#fragment",
        "http://user:password@127.0.0.1:8001",
        "http://127.0.0.1:9000",
        "http://finagent-api-1:8001",
    ],
)
def test_operator_demo_cannot_send_credentials_to_arbitrary_targets(config: Settings, url: str) -> None:
    with pytest.raises(RuntimeError, match="LOCAL_SYNTHETIC_DEMO_TARGET_REQUIRED"):
        validate_demo_target(config, url)


def test_operator_demo_requires_mock_model_and_disabled_downstream_writes(config: Settings) -> None:
    validate_demo_target(config, "http://127.0.0.1:8001")
    config.case_status_write_enabled = True
    with pytest.raises(RuntimeError):
        validate_demo_target(config, "http://127.0.0.1:8001")
    config.case_status_write_enabled = False
    config.model_provider = "azure"
    with pytest.raises(RuntimeError):
        validate_demo_target(config, "http://127.0.0.1:8001")
