"""Real PostgreSQL/Redis and real signature verification; SMTP is a synthetic sink."""

import hashlib
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
import redis
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from finagent.api.main import create_app
from finagent.core.config import LOCAL_ISSUER
from finagent.database.models import (
    PasskeyChallenge,
    PasskeyFactor,
    PasskeyGrant,
    PasswordCredential,
    Principal,
)
from finagent.database.session import session_factory, transaction
from finagent.security.authentication.passwords import hash_password
from tests.helpers import subject
from tests.passkey_helpers import VirtualAuthenticator
from tests.unit.test_passkey_security import config

pytestmark = [pytest.mark.integration, pytest.mark.release_blocker, pytest.mark.req("FR-001", "SPR-005")]
PASSWORD = "Synthetic passkey account passphrase 901!"
BASE = "/api/v1/auth/passkeys"


class MailSink:
    def __init__(self):
        self.messages = []

    def send_code(self, recipient, code):  # type: ignore[no-untyped-def]
        self.messages.append((recipient, code))


@pytest.fixture
def account(db_url, app_db_url, blob_root):  # type: ignore[no-untyped-def]
    redis_url = os.environ.get("FINAGENT_TEST_REDIS_URL")
    if not redis_url:
        pytest.skip("Real Redis required for passkey integration")
    with transaction(session_factory(db_url)) as session:
        principal = session.scalar(
            select(Principal).where(
                Principal.issuer == LOCAL_ISSUER, Principal.subject == subject("a", "reviewer1")
            )
        )
        credential = PasswordCredential(
            tenant_id=principal.tenant_id,
            principal_id=principal.id,
            email=uuid.uuid4().hex + "@example.com",
            password_hash=hash_password(PASSWORD),
        )
        session.add(credential)
        session.flush()
        email, principal_id, tenant_id = credential.email, principal.id, principal.tenant_id
    settings = config(
        database_url=app_db_url,
        redis_url=redis_url,
        blob_local_root=blob_root,
        passkey_enrollment_principals=[str(principal_id)],
    )
    app = create_app(settings)
    app.state.verification_mailer = MailSink()
    limiter = redis.Redis.from_url(redis_url)

    def reset_limits():
        digest = hashlib.sha256(f"{tenant_id}:{principal_id}".encode()).hexdigest()
        for operation in (
            "passkey-register",
            "passkey-options",
            "passkey-finish",
            "passkey-revoke",
            "send",
            "verify",
        ):
            limiter.delete("finagent:email-proof:v1:" + operation + ":" + digest)

    reset_limits()
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            yield client, email, principal_id, tenant_id, reset_limits
    finally:
        reset_limits()
        limiter.close()


def login(client, email):  # type: ignore[no-untyped-def]
    response = client.post("/api/v1/auth/password/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": "Bearer " + response.json()["access_token"]}


def verify_email(client, headers):  # type: ignore[no-untyped-def]
    response = client.post("/api/v1/auth/email/send", headers=headers)
    assert response.status_code == 200, response.text
    code = client.app.state.verification_mailer.messages[-1][1]
    response = client.post(
        "/api/v1/auth/email/verify",
        headers=headers,
        json={"challenge_id": response.json()["challenge_id"], "code": code},
    )
    assert response.status_code == 200, response.text


def enroll(account):  # type: ignore[no-untyped-def]
    client, email, *_ = account
    headers = login(client, email)
    verify_email(client, headers)
    response = client.post(BASE + "/register/options", headers=headers, json={"password": PASSWORD})
    assert response.status_code == 200, response.text
    options = response.json()
    assert options["publicKey"]["authenticatorSelection"]["userVerification"] == "required"
    auth = VirtualAuthenticator()
    body = {
        "challenge_id": options["challenge_id"],
        "credential": auth.registration(options["publicKey"]["challenge"]),
    }
    response = client.post(BASE + "/register/finish", headers=headers, json=body)
    assert response.status_code == 200, response.text
    assert response.json() == {"registered": True, "grants_mfa": False}
    return client, headers, auth, body


def assertion(client, headers, auth, **changes):  # type: ignore[no-untyped-def]
    response = client.post(BASE + "/verify/options", headers=headers)
    assert response.status_code == 200, response.text
    options = response.json()
    assert options["publicKey"]["userVerification"] == "required"
    return {
        "challenge_id": options["challenge_id"],
        "credential": auth.assertion(options["publicKey"]["challenge"], **changes),
    }


def mfa(client, headers):  # type: ignore[no-untyped-def]
    response = client.get("/api/v1/auth/session", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["mfa_satisfied"]


def test_registration_does_not_grant_mfa_and_second_session_needs_own_assertion(account):
    client, first, auth, _ = enroll(account)
    second = login(client, account[1])
    assert not mfa(client, first) and not mfa(client, second)
    response = client.post(BASE + "/verify/finish", headers=first, json=assertion(client, first, auth))
    assert response.status_code == 200, response.text
    assert 0 < response.json()["expires_at"] - int(datetime.now(UTC).timestamp()) <= 300
    assert mfa(client, first) and not mfa(client, second)
    assert client.get("/api/v1/auth/session", headers=first).json()["roles"] == ["REVIEWER"]


def test_same_assertion_is_one_use_even_with_zero_counter(account):
    client, headers, auth, _ = enroll(account)
    body = assertion(client, headers, auth, count=0)
    assert client.post(BASE + "/verify/finish", headers=headers, json=body).status_code == 200
    assert client.post(BASE + "/verify/finish", headers=headers, json=body).status_code == 403


def test_cross_session_attempt_cannot_consume_original_challenge(account):
    client, headers, auth, _ = enroll(account)
    other = login(client, account[1])
    body = assertion(client, headers, auth)
    assert client.post(BASE + "/verify/finish", headers=other, json=body).status_code == 403
    assert not mfa(client, other)
    assert client.post(BASE + "/verify/finish", headers=headers, json=body).status_code == 200


@pytest.mark.parametrize(
    "changes", [{"flags": 1}, {"origin": "https://untrusted.invalid"}, {"rp": "untrusted.invalid"}]
)
def test_invalid_response_consumes_challenge_without_mfa(account, changes):  # type: ignore[no-untyped-def]
    client, headers, auth, _ = enroll(account)
    body = assertion(client, headers, auth, **changes)
    assert client.post(BASE + "/verify/finish", headers=headers, json=body).status_code == 403
    assert not mfa(client, headers)
    assert client.post(BASE + "/verify/finish", headers=headers, json=body).status_code == 403


def test_new_challenge_invalidates_old_challenge(account):
    client, headers, auth, _ = enroll(account)
    old = assertion(client, headers, auth)
    fresh = assertion(client, headers, auth)
    assert client.post(BASE + "/verify/finish", headers=headers, json=old).status_code == 403
    assert client.post(BASE + "/verify/finish", headers=headers, json=fresh).status_code == 200


def test_expired_challenge_denied(account, db_url):
    client, headers, auth, _ = enroll(account)
    body = assertion(client, headers, auth)
    with transaction(session_factory(db_url)) as session:
        row = session.get(PasskeyChallenge, uuid.UUID(body["challenge_id"]))
        row.created_at = datetime.now(UTC) - timedelta(minutes=4)
        row.expires_at = row.created_at + timedelta(minutes=2)
    assert client.post(BASE + "/verify/finish", headers=headers, json=body).status_code == 403
    assert not mfa(client, headers)


def test_revocation_invalidates_grants_and_prevents_replacement(account):
    client, headers, auth, _ = enroll(account)
    assert client.post(BASE + "/revoke", headers=headers).status_code == 403
    assert (
        client.post(
            BASE + "/verify/finish", headers=headers, json=assertion(client, headers, auth)
        ).status_code
        == 200
    )
    assert client.post(BASE + "/revoke", headers=headers).status_code == 200
    assert not mfa(client, headers)
    assert client.post(BASE + "/verify/options", headers=headers).status_code == 403
    assert (
        client.post(BASE + "/register/options", headers=headers, json={"password": PASSWORD}).status_code
        == 403
    )


def test_enrollment_requires_recent_email_and_current_password(account):
    client, email, *_ = account
    headers = login(client, email)
    assert (
        client.post(BASE + "/register/options", headers=headers, json={"password": PASSWORD}).status_code
        == 403
    )
    verify_email(client, headers)
    assert (
        client.post(BASE + "/register/options", headers=headers, json={"password": "incorrect"}).status_code
        == 403
    )
    assert (
        client.post(BASE + "/register/options", headers=headers, json={"password": PASSWORD}).status_code
        == 200
    )


def test_registration_response_cannot_be_replayed(account):
    client, headers, _, body = enroll(account)
    assert client.post(BASE + "/register/finish", headers=headers, json=body).status_code == 403
    assert not mfa(client, headers)


def test_concurrent_finishes_only_one_succeeds(account):
    client, headers, auth, _ = enroll(account)
    body = assertion(client, headers, auth)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda _: client.post(BASE + "/verify/finish", headers=headers, json=body).status_code,
                range(2),
            )
        )
    assert sorted(results) == [200, 403]


def test_grant_expiry_and_factor_version_checked(account, db_url):
    client, headers, auth, _ = enroll(account)
    assert (
        client.post(
            BASE + "/verify/finish", headers=headers, json=assertion(client, headers, auth)
        ).status_code
        == 200
    )
    with transaction(session_factory(db_url)) as session:
        grant = session.scalar(select(PasskeyGrant))
        grant.verified_at = datetime.now(UTC) - timedelta(minutes=6)
        grant.expires_at = grant.verified_at + timedelta(minutes=5)
    assert not mfa(client, headers)
    assert (
        client.post(
            BASE + "/verify/finish", headers=headers, json=assertion(client, headers, auth, count=2)
        ).status_code
        == 200
    )
    with transaction(session_factory(db_url)) as session:
        session.scalar(select(PasskeyFactor)).version += 1
    assert not mfa(client, headers)


def test_api_cannot_replace_stored_public_key(account, app_db_url):
    enroll(account)
    with pytest.raises(DBAPIError), transaction(session_factory(app_db_url)) as session:
        session.execute(text("UPDATE passkey_factors SET public_key = 'forged'"))


def test_disabled_feature_anonymous_access_and_body_limit(account):
    client = account[0]
    assert client.get(BASE + "/status").status_code == 401
    headers = login(client, account[1])
    assert client.post(BASE + "/register/finish", headers=headers, content=b"x" * 32769).status_code == 413
    client.app.state.settings.local_passkeys_enabled = False
    assert client.get(BASE + "/status", headers=headers).status_code == 403


def test_non_allowlisted_principal_cannot_enroll(account, db_url):
    client = account[0]
    with transaction(session_factory(db_url)) as session:
        principal = session.scalar(
            select(Principal).where(
                Principal.issuer == LOCAL_ISSUER, Principal.subject == subject("b", "reviewer1")
            )
        )
        email = uuid.uuid4().hex + "@example.com"
        session.add(
            PasswordCredential(
                tenant_id=principal.tenant_id,
                principal_id=principal.id,
                email=email,
                password_hash=hash_password(PASSWORD),
            )
        )
    headers = login(client, email)
    verify_email(client, headers)
    assert (
        client.post(BASE + "/register/options", headers=headers, json={"password": PASSWORD}).status_code
        == 403
    )


def test_cross_tenant_finish_does_not_grant_access(account, db_url):
    client, first, auth, _ = enroll(account)
    body = assertion(client, first, auth)
    with transaction(session_factory(db_url)) as session:
        principal = session.scalar(
            select(Principal).where(
                Principal.issuer == LOCAL_ISSUER, Principal.subject == subject("b", "reviewer1")
            )
        )
        email = uuid.uuid4().hex + "@example.com"
        session.add(
            PasswordCredential(
                tenant_id=principal.tenant_id,
                principal_id=principal.id,
                email=email,
                password_hash=hash_password(PASSWORD),
            )
        )
    other = login(client, email)
    assert client.post(BASE + "/verify/finish", headers=other, json=body).status_code == 403
    assert not mfa(client, other)
    assert client.post(BASE + "/verify/finish", headers=first, json=body).status_code == 200


def test_verification_rate_limit_fails_closed(account):
    client, headers, _, _ = enroll(account)
    for _ in range(5):
        assert client.post(BASE + "/verify/options", headers=headers).status_code == 200
    assert client.post(BASE + "/verify/options", headers=headers).status_code == 429


def test_password_reset_invalidates_existing_passkey_grant(account, db_url):
    client, headers, auth, _ = enroll(account)
    assert (
        client.post(
            BASE + "/verify/finish", headers=headers, json=assertion(client, headers, auth)
        ).status_code
        == 200
    )
    with transaction(session_factory(db_url)) as session:
        credential = session.scalar(
            select(PasswordCredential).where(PasswordCredential.principal_id == account[2])
        )
        credential.version += 1
    assert client.get("/api/v1/auth/session", headers=headers).status_code == 401


def test_logged_out_password_session_cannot_finish_passkey_challenge(account):
    client, headers, auth, _ = enroll(account)
    body = assertion(client, headers, auth)
    assert client.post("/api/v1/auth/password/logout", headers=headers).status_code == 204
    assert client.post(BASE + "/verify/finish", headers=headers, json=body).status_code == 401


def test_passkey_grant_cannot_outlive_password_session(account, db_url):
    from finagent.database.models import PasswordSession

    client, headers, auth, _ = enroll(account)
    with transaction(session_factory(db_url)) as session:
        session.scalar(select(PasswordSession)).expires_at = datetime.now(UTC) + timedelta(seconds=60)
    response = client.post(BASE + "/verify/finish", headers=headers, json=assertion(client, headers, auth))
    assert response.status_code == 200, response.text
    assert 0 < response.json()["expires_at"] - int(datetime.now(UTC).timestamp()) <= 60
