"""Real PostgreSQL app-role and Redis password sign-in admission tests."""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from finagent.api.main import create_app
from finagent.core.config import LOCAL_ISSUER, Settings
from finagent.database.models import PasswordCredential, PasswordSession, Principal, RoleAssignment, Tenant
from finagent.database.session import session_factory, transaction
from finagent.security.authentication.passwords import hash_password, token_digest
from tests.helpers import subject

pytestmark = [pytest.mark.integration, pytest.mark.release_blocker, pytest.mark.req("FR-001", "SPR-004")]
PASSWORD = "Synthetic integration passphrase 901!"


@pytest.fixture
def password_client(app_db_url: str, blob_root: str) -> Iterator[TestClient]:
    redis_url = os.environ.get("FINAGENT_TEST_REDIS_URL")
    if not redis_url:
        pytest.skip("FINAGENT_TEST_REDIS_URL is required; password integration NOT RUN")
    config = Settings(
        _env_file=None,
        environment="test",
        auth_mode="google",
        database_url=app_db_url,
        google_web_client_id="synthetic.apps.googleusercontent.com",
        password_login_enabled=True,
        redis_url=redis_url,
        blob_local_root=blob_root,
    )
    with TestClient(create_app(config), raise_server_exceptions=False) as client:
        yield client


@pytest.fixture
def account(db_url: str) -> tuple[str, uuid.UUID, uuid.UUID]:
    email = uuid.uuid4().hex + "@example.com"
    with transaction(session_factory(db_url)) as session:
        user = session.scalar(
            select(Principal).where(
                Principal.issuer == LOCAL_ISSUER, Principal.subject == subject("a", "analyst1")
            )
        )
        assert user is not None
        credential = PasswordCredential(
            tenant_id=user.tenant_id, principal_id=user.id, email=email, password_hash=hash_password(PASSWORD)
        )
        session.add(credential)
        session.flush()
        return email, credential.id, user.id


def signin(client: TestClient, email: str, password: str = PASSWORD):  # type: ignore[no-untyped-def]
    return client.post("/api/v1/auth/password/login", json={"email": email, "password": password})


def test_password_login_uses_database_roles_and_never_mfa(password_client, account, db_url):  # type: ignore[no-untyped-def]
    response = signin(password_client, " " + account[0].upper() + " ")
    assert response.status_code == 200, response.text
    data = response.json()
    headers = {"Authorization": "Bearer " + data["access_token"]}
    identity = password_client.get("/api/v1/auth/session", headers=headers)
    assert identity.status_code == 200, identity.text
    assert identity.json()["roles"] == ["ANALYST"]
    assert identity.json()["mfa_satisfied"] is False
    assert data["mfa_satisfied"] is False
    assert 0 < data["expires_at"] - int(datetime.now(UTC).timestamp()) <= 1800
    assert password_client.get("/api/v1/cases", headers=headers).status_code == 200
    assert password_client.get("/api/v1/admin/delegations", headers=headers).status_code == 403
    with transaction(session_factory(db_url)) as session:
        saved = session.scalar(
            select(PasswordSession).where(PasswordSession.token_hash == token_digest(data["access_token"]))
        )
        assert saved is not None and data["access_token"] not in saved.token_hash


def test_unknown_and_wrong_password_have_same_public_error(password_client, account, db_url):  # type: ignore[no-untyped-def]
    wrong = signin(password_client, account[0], "Wrong synthetic passphrase 902!")
    missing = signin(password_client, uuid.uuid4().hex + "@example.com", "Wrong synthetic passphrase 902!")
    assert wrong.status_code == missing.status_code == 401
    for result in (wrong, missing):
        assert result.json()["error"]["code"] == "UNAUTHENTICATED"
        assert PASSWORD not in result.text and account[0] not in result.text
    assert wrong.json()["error"]["message"] == missing.json()["error"]["message"]
    with transaction(session_factory(db_url)) as session:
        assert session.get(PasswordCredential, account[1]).failed_attempts == 1


@pytest.mark.parametrize("change", ["disabled", "version", "expired", "principal", "tenant", "role_removed"])
def test_sessions_observe_revocation_and_current_access(password_client, account, db_url, change):  # type: ignore[no-untyped-def]
    issued = signin(password_client, account[0])
    assert issued.status_code == 200
    token = issued.json()["access_token"]
    with transaction(session_factory(db_url)) as session:
        credential = session.get(PasswordCredential, account[1])
        if change == "disabled":
            credential.enabled = False
        elif change == "version":
            credential.version += 1
        elif change == "expired":
            session.scalar(
                select(PasswordSession).where(PasswordSession.token_hash == token_digest(token))
            ).expires_at = datetime.now(UTC) - timedelta(seconds=1)
        elif change == "principal":
            session.get(Principal, account[2]).status = "SUSPENDED"
        elif change == "tenant":
            session.get(Tenant, credential.tenant_id).status = "SUSPENDED"
        else:
            for role in session.scalars(
                select(RoleAssignment).where(RoleAssignment.principal_id == account[2])
            ):
                role.valid_to = datetime.now(UTC) - timedelta(seconds=1)
    response = password_client.get("/api/v1/cases", headers={"Authorization": "Bearer " + token})
    assert response.status_code == (403 if change == "role_removed" else 401), response.text


def test_logout_revokes_token_after_feature_disabled(password_client, account):  # type: ignore[no-untyped-def]
    issued = signin(password_client, account[0])
    assert issued.status_code == 200
    headers = {"Authorization": "Bearer " + issued.json()["access_token"]}
    password_client.app.state.settings.password_login_enabled = False
    assert password_client.post("/api/v1/auth/password/logout", headers=headers).status_code == 204
    password_client.app.state.settings.password_login_enabled = True
    assert password_client.get("/api/v1/auth/session", headers=headers).status_code == 401
    assert password_client.post("/api/v1/auth/password/logout", headers=headers).status_code == 204


def test_redis_throttling_and_persistent_lockout(password_client, account, db_url):  # type: ignore[no-untyped-def]
    for _ in range(5):
        assert signin(password_client, account[0], "Wrong synthetic passphrase 902!").status_code == 401
    assert signin(password_client, account[0]).status_code == 429
    with transaction(session_factory(db_url)) as session:
        row = session.get(PasswordCredential, account[1])
        assert row.locked_until > datetime.now(UTC)
        assert row.failed_attempts == 0


def test_app_role_cannot_reset_passwords(app_db_url, account):  # type: ignore[no-untyped-def]
    with pytest.raises(DBAPIError), transaction(session_factory(app_db_url)) as session:
        session.execute(
            text("UPDATE password_credentials SET password_hash = 'forbidden' WHERE id = :id"),
            {"id": account[1]},
        )
    with pytest.raises(DBAPIError), transaction(session_factory(app_db_url)) as session:
        session.execute(text("DELETE FROM password_credentials WHERE id = :id"), {"id": account[1]})


def test_database_rejects_cross_tenant_credential_link(db_url, account):  # type: ignore[no-untyped-def]
    with pytest.raises(IntegrityError), transaction(session_factory(db_url)) as session:
        other = Tenant(slug="password-other-tenant", display_name="Other synthetic tenant")
        session.add(other)
        session.flush()
        session.add(
            PasswordCredential(
                tenant_id=other.id,
                principal_id=account[2],
                email=uuid.uuid4().hex + "@example.com",
                password_hash=hash_password(PASSWORD),
            )
        )
        session.flush()
