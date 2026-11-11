"""Real PostgreSQL app-role and Redis: possession, binding, replay, expiry and revocation."""

from __future__ import annotations

import base64
import hashlib
import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
import redis
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from finagent.api.main import create_app
from finagent.core.config import LOCAL_ISSUER, Settings
from finagent.database.models import (
    PasswordCredential,
    PasswordSession,
    Principal,
    Tenant,
    TotpFactor,
    TotpGrant,
)
from finagent.database.session import session_factory, transaction
from finagent.security.authentication import totp
from finagent.security.authentication.passwords import hash_password
from finagent.services.audit_service.writer import verify_chain
from tests.helpers import subject

pytestmark = [pytest.mark.integration, pytest.mark.release_blocker, pytest.mark.req("FR-001", "SPR-005")]
SECRET = b"12345678901234567890"
PASSWORD = "Synthetic MFA integration passphrase 901!"


@pytest.fixture
def mfa_client(app_db_url: str, blob_root: str) -> Iterator[TestClient]:
    redis_url = os.environ.get("FINAGENT_TEST_REDIS_URL")
    if not redis_url:
        pytest.skip("Real Redis required for TOTP integration")
    settings = Settings(
        _env_file=None,
        environment="test",
        auth_mode="google",
        google_web_client_id="test.apps.googleusercontent.com",
        database_url=app_db_url,
        redis_url=redis_url,
        blob_local_root=blob_root,
        password_login_enabled=True,
        local_totp_enabled=True,
        local_totp_key=base64.b64encode(b"K" * 32).decode(),
    )
    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        yield client


@pytest.fixture
def enrolled(mfa_client, db_url):  # type: ignore[no-untyped-def]
    with transaction(session_factory(db_url)) as session:
        principal = session.scalar(
            select(Principal).where(
                Principal.issuer == LOCAL_ISSUER, Principal.subject == subject("a", "reviewer1")
            )
        )
        email = uuid.uuid4().hex + "@example.com"
        credential = PasswordCredential(
            tenant_id=principal.tenant_id,
            principal_id=principal.id,
            email=email,
            password_hash=hash_password(PASSWORD),
        )
        session.add(credential)
        factor = TotpFactor(
            tenant_id=principal.tenant_id,
            principal_id=principal.id,
            encrypted_secret=totp.seal(
                mfa_client.app.state.settings, principal.tenant_id, principal.id, SECRET
            ),
        )
        session.add(factor)
        session.flush()
        account = email, credential.id, factor.id, principal.id, principal.tenant_id
    # Seeded principal IDs persist across schema resets; isolate only this test's
    # dedicated throttle key, never flush a Redis database.
    client = redis.Redis.from_url(os.environ["FINAGENT_TEST_REDIS_URL"])
    key = "finagent:totp-step-up:v1:" + hashlib.sha256(f"{account[4]}:{account[3]}".encode()).hexdigest()
    client.delete(key)
    try:
        yield account
    finally:
        client.delete(key)
        client.close()


def login(client, email):  # type: ignore[no-untyped-def]
    response = client.post("/api/v1/auth/password/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    assert response.json()["mfa_satisfied"] is False
    return {"Authorization": "Bearer " + response.json()["access_token"]}


def code() -> str:
    return totp.otp(SECRET, int(datetime.now(UTC).timestamp()) // 30)


def step(client, headers, value=None):  # type: ignore[no-untyped-def]
    return client.post("/api/v1/auth/totp/verify", headers=headers, json={"code": value or code()})


def test_real_code_elevates_only_same_session_without_changing_roles(mfa_client, enrolled, db_url):  # type: ignore[no-untyped-def]
    first, second = login(mfa_client, enrolled[0]), login(mfa_client, enrolled[0])
    path = "/api/v1/auth/session"
    before = mfa_client.get(path, headers=first).json()
    assert before["mfa_satisfied"] is False
    current = code()
    result = step(mfa_client, first, current)
    assert result.status_code == 200, result.text
    assert 0 < result.json()["expires_at"] - int(datetime.now(UTC).timestamp()) <= 300
    after = mfa_client.get(path, headers=first).json()
    assert after["mfa_satisfied"] is True and after["roles"] == before["roles"] == ["REVIEWER"]
    assert mfa_client.get(path, headers=second).json()["mfa_satisfied"] is False
    assert step(mfa_client, second, current).status_code == 403
    with transaction(session_factory(db_url)) as session:
        grant = session.scalar(select(TotpGrant))
        assert grant and grant.factor_id == enrolled[2]


@pytest.mark.parametrize(
    "change",
    [
        "expiry",
        "factor_disabled",
        "factor_version",
        "password_reset",
        "logout",
        "tenant_suspended",
        "principal_suspended",
    ],
)
def test_grant_revocation_and_current_access(mfa_client, enrolled, db_url, change):  # type: ignore[no-untyped-def]
    headers = login(mfa_client, enrolled[0])
    assert step(mfa_client, headers).status_code == 200
    with transaction(session_factory(db_url)) as session:
        grant = session.scalar(select(TotpGrant))
        factor = session.get(TotpFactor, enrolled[2])
        if change == "expiry":
            grant.verified_at = datetime.now(UTC) - timedelta(minutes=10)
            grant.expires_at = grant.verified_at + timedelta(minutes=5)
        elif change == "factor_disabled":
            factor.enabled = False
        elif change == "factor_version":
            factor.version += 1
        elif change == "password_reset":
            session.get(PasswordCredential, enrolled[1]).version += 1
        elif change == "logout":
            session.scalar(select(PasswordSession)).revoked_at = datetime.now(UTC)
        elif change == "tenant_suspended":
            session.get(Tenant, enrolled[4]).status = "SUSPENDED"
        elif change == "principal_suspended":
            session.get(Principal, enrolled[3]).status = "SUSPENDED"
    response = mfa_client.get("/api/v1/auth/session", headers=headers)
    if change in ("expiry", "factor_disabled", "factor_version"):
        assert response.status_code == 200 and response.json()["mfa_satisfied"] is False
    else:
        assert response.status_code in (401, 403)


def test_failed_counter_commits_and_lockout_blocks_valid_code(mfa_client, enrolled, db_url):  # type: ignore[no-untyped-def]
    headers = login(mfa_client, enrolled[0])
    for _ in range(5):
        assert step(mfa_client, headers, "abcdef").status_code == 403
    with transaction(session_factory(db_url)) as session:
        factor = session.get(TotpFactor, enrolled[2])
        assert factor.locked_until > datetime.now(UTC) and factor.failed_attempts == 0
        assert session.scalar(select(TotpGrant)) is None
    assert step(mfa_client, headers).status_code == 429


def test_fake_mfa_claim_and_extra_fields_rejected(mfa_client, enrolled):  # type: ignore[no-untyped-def]
    headers = login(mfa_client, enrolled[0])
    response = mfa_client.post(
        "/api/v1/auth/totp/verify", headers=headers, json={"code": code(), "mfa": True}
    )
    assert response.status_code == 400
    assert mfa_client.get("/api/v1/auth/session", headers=headers).json()["mfa_satisfied"] is False


def test_no_enrollment_remains_first_factor_only(mfa_client, enrolled, db_url):  # type: ignore[no-untyped-def]
    with transaction(session_factory(db_url)) as session:
        session.get(TotpFactor, enrolled[2]).enabled = False
    headers = login(mfa_client, enrolled[0])
    assert mfa_client.get("/api/v1/auth/totp/status", headers=headers).json() == {
        "enrolled": False,
        "mfa_satisfied": False,
    }
    assert step(mfa_client, headers).status_code == 403


def test_app_role_cannot_change_or_insert_factor(app_db_url, enrolled):  # type: ignore[no-untyped-def]
    for sql in (
        "UPDATE totp_factors SET enabled=false",
        "UPDATE totp_factors SET encrypted_secret='forged'",
        "UPDATE totp_factors SET version=version+1",
        "DELETE FROM totp_factors",
    ):
        with pytest.raises(DBAPIError), transaction(session_factory(app_db_url)) as session:
            session.execute(text(sql))


def test_disabling_feature_removes_step_up(mfa_client, enrolled):  # type: ignore[no-untyped-def]
    headers = login(mfa_client, enrolled[0])
    assert step(mfa_client, headers).status_code == 200
    mfa_client.app.state.settings.local_totp_enabled = False
    response = mfa_client.get("/api/v1/auth/session", headers=headers)
    assert response.status_code == 200 and response.json()["mfa_satisfied"] is False


def test_actual_decision_route_requires_verified_second_factor(mfa_client, enrolled):  # type: ignore[no-untyped-def]
    headers = login(mfa_client, enrolled[0])
    headers["Idempotency-Key"] = "totp-approval-" + uuid.uuid4().hex
    path = "/api/v1/approvals/" + str(uuid.uuid4()) + "/decision"
    body = {
        "decision": "RETURN_FOR_REWORK",
        "rationale": "Synthetic authentication boundary test",
        "report_version_id": str(uuid.uuid4()),
        "snapshot_id": str(uuid.uuid4()),
    }
    denied = mfa_client.post(path, headers=headers, json=body)
    assert denied.status_code == 401 and denied.json()["error"]["code"] == "MFA_REQUIRED"
    assert step(mfa_client, headers).status_code == 200
    # With possession verified, the unchanged approval service checks request scope.
    # A nonexistent request stays unavailable; MFA grants no assignment or case access.
    absent = mfa_client.post(path, headers=headers, json=body)
    assert absent.status_code == 404, absent.text


def test_concurrent_replay_only_one_session_elevated(mfa_client, enrolled, db_url):  # type: ignore[no-untyped-def]
    from concurrent.futures import ThreadPoolExecutor

    headers = [login(mfa_client, enrolled[0]), login(mfa_client, enrolled[0])]
    current = code()
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda h: step(mfa_client, h, current).status_code, headers))
    assert sorted(results) == [200, 403]
    with transaction(session_factory(db_url)) as session:
        assert len(session.scalars(select(TotpGrant)).all()) == 1
        assert verify_chain(session, enrolled[4])


def test_wrong_encryption_key_fails_closed(mfa_client, enrolled):  # type: ignore[no-untyped-def]
    from pydantic import SecretStr

    headers = login(mfa_client, enrolled[0])
    mfa_client.app.state.settings.local_totp_key = SecretStr(base64.b64encode(b"X" * 32).decode())
    result = step(mfa_client, headers)
    assert result.status_code == 503, result.text
    assert mfa_client.get("/api/v1/auth/session", headers=headers).json()["mfa_satisfied"] is False


def test_redis_unavailable_cannot_elevate(mfa_client, enrolled, db_url):  # type: ignore[no-untyped-def]
    headers = login(mfa_client, enrolled[0])
    mfa_client.app.state.settings.redis_url = None
    assert step(mfa_client, headers).status_code == 503
    with transaction(session_factory(db_url)) as session:
        assert session.scalar(select(TotpGrant)) is None


def test_grant_clamped_to_remaining_password_session(mfa_client, enrolled, db_url):  # type: ignore[no-untyped-def]
    headers = login(mfa_client, enrolled[0])
    expiry = datetime.now(UTC) + timedelta(seconds=90)
    with transaction(session_factory(db_url)) as session:
        session.scalar(select(PasswordSession)).expires_at = expiry
    result = step(mfa_client, headers)
    assert result.status_code == 200 and result.json()["expires_at"] == int(expiry.timestamp())


def test_another_principals_factor_cannot_elevate_session(mfa_client, enrolled, db_url):  # type: ignore[no-untyped-def]
    headers = login(mfa_client, enrolled[0])
    assert step(mfa_client, headers).status_code == 200
    with transaction(session_factory(db_url)) as session:
        other = session.scalar(select(Principal).where(Principal.subject == subject("a", "analyst1")))
        session.get(TotpFactor, enrolled[2]).principal_id = other.id
    assert mfa_client.get("/api/v1/auth/session", headers=headers).json()["mfa_satisfied"] is False


def test_tenant_foreign_key_prevents_factor_rebinding(mfa_client, enrolled, db_url):  # type: ignore[no-untyped-def]
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError), transaction(session_factory(db_url)) as session:
        other = session.scalar(select(Principal).where(Principal.subject == subject("b", "reviewer1")))
        session.get(TotpFactor, enrolled[2]).principal_id = other.id


def test_oversized_chunked_code_body_rejected(mfa_client, enrolled):  # type: ignore[no-untyped-def]
    headers = {**login(mfa_client, enrolled[0]), "Content-Type": "application/json"}
    result = mfa_client.post(
        "/api/v1/auth/totp/verify", headers=headers, content=iter([b'{"code":"', b"A" * 4096, b'"}'])
    )
    assert result.status_code == 413


def test_owner_enrollment_checks_possession_but_does_not_elevate(
    mfa_client, enrolled, db_url, monkeypatch, capsys
):  # type: ignore[no-untyped-def]
    from finagent.security.authentication import totp_console

    with transaction(session_factory(db_url)) as session:
        session.delete(session.get(TotpFactor, enrolled[2]))
    monkeypatch.setenv("FINAGENT_MIGRATION_DATABASE_URL", db_url)
    monkeypatch.setattr(totp_console, "get_settings", lambda: mfa_client.app.state.settings)
    monkeypatch.setattr("builtins.input", lambda prompt: str(enrolled[3]))
    monkeypatch.setattr(
        totp_console.secrets, "token_bytes", lambda size: SECRET if size == 20 else b"N" * size
    )
    monkeypatch.setattr(totp_console.getpass, "getpass", lambda prompt: code())
    totp_console.main()
    output = capsys.readouterr().out
    assert "AUTHENTICATOR_ENROLLED; NO_SESSION_ELEVATED" in output
    with transaction(session_factory(db_url)) as session:
        credential = session.get(PasswordCredential, enrolled[1])
        factor = totp.factor_for(session, credential)
        assert factor and totp.unseal(mfa_client.app.state.settings, factor) == SECRET
        assert session.scalar(select(TotpGrant)) is None
    with pytest.raises(SystemExit, match="already enrolled"):
        totp_console.main()


def test_invalid_enrollment_code_saves_no_factor(mfa_client, enrolled, db_url, monkeypatch, capsys):  # type: ignore[no-untyped-def]
    from finagent.security.authentication import totp_console

    with transaction(session_factory(db_url)) as session:
        session.delete(session.get(TotpFactor, enrolled[2]))
    monkeypatch.setenv("FINAGENT_MIGRATION_DATABASE_URL", db_url)
    monkeypatch.setattr(totp_console, "get_settings", lambda: mfa_client.app.state.settings)
    monkeypatch.setattr("builtins.input", lambda prompt: str(enrolled[3]))
    monkeypatch.setattr(totp_console.getpass, "getpass", lambda prompt: "abcdef")
    with pytest.raises(SystemExit, match="no factor saved"):
        totp_console.main()
    capsys.readouterr()
    with transaction(session_factory(db_url)) as session:
        assert session.scalar(select(TotpFactor)) is None


def test_real_tenant_enrollment_refused(mfa_client, enrolled, db_url, monkeypatch):  # type: ignore[no-untyped-def]
    from finagent.security.authentication import totp_console

    with transaction(session_factory(db_url)) as session:
        session.get(Tenant, enrolled[4]).is_synthetic = False
    monkeypatch.setenv("FINAGENT_MIGRATION_DATABASE_URL", db_url)
    monkeypatch.setattr(totp_console, "get_settings", lambda: mfa_client.app.state.settings)
    monkeypatch.setattr("builtins.input", lambda prompt: str(enrolled[3]))
    with pytest.raises(SystemExit, match="synthetic tenant"):
        totp_console.main()


def test_app_role_cannot_insert_factors(app_db_url, enrolled):  # type: ignore[no-untyped-def]
    with pytest.raises(DBAPIError), transaction(session_factory(app_db_url)) as session:
        session.add(
            TotpFactor(tenant_id=enrolled[4], principal_id=enrolled[3], encrypted_secret="not-a-secret")
        )
        session.flush()
