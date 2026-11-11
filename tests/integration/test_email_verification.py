"""Actual PostgreSQL/Redis security checks; only SMTP delivery is replaced by a test sink."""

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
from sqlalchemy import select

from finagent.api.main import create_app
from finagent.core.config import LOCAL_ISSUER, Settings
from finagent.database.models import (
    EmailVerificationChallenge,
    PasswordCredential,
    Principal,
    Tenant,
    TotpGrant,
)
from finagent.database.session import session_factory, transaction
from finagent.security.authentication.passwords import hash_password
from tests.helpers import subject

pytestmark = [pytest.mark.integration, pytest.mark.release_blocker, pytest.mark.req("FR-001", "SPR-004")]
PASSWORD = "Synthetic email account passphrase 901!"


class MailSink:
    def __init__(self):
        self.messages = []
        self.fail = False

    def send_code(self, recipient, code):  # type: ignore[no-untyped-def]
        if self.fail:
            raise OSError("test SMTP failure")
        self.messages.append((recipient, code))


@pytest.fixture
def email_client(app_db_url: str, blob_root: str) -> Iterator[TestClient]:
    redis_url = os.environ.get("FINAGENT_TEST_REDIS_URL")
    if not redis_url:
        pytest.skip("Real Redis required for email verification checks")
    settings = Settings(
        _env_file=None,
        environment="test",
        auth_mode="google",
        google_web_client_id="test.apps.googleusercontent.com",
        database_url=app_db_url,
        redis_url=redis_url,
        blob_local_root=blob_root,
        password_login_enabled=True,
        email_verification_enabled=True,
        gmail_sender="synthetic-sender@gmail.com",
        gmail_app_password="abcdefghijklmnop",
        email_verification_key=base64.b64encode(b"K" * 32).decode(),
    )
    app = create_app(settings)
    app.state.verification_mailer = MailSink()
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


@pytest.fixture
def account(email_client, db_url):  # type: ignore[no-untyped-def]
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
        details = (credential.email, credential.id, principal.id, principal.tenant_id)
    client = redis.Redis.from_url(os.environ["FINAGENT_TEST_REDIS_URL"])
    keys = [
        "finagent:email-proof:v1:"
        + scope
        + ":"
        + hashlib.sha256(f"{details[3]}:{details[2]}".encode()).hexdigest()
        for scope in ("send", "verify")
    ]
    client.delete(*keys)
    try:
        yield details
    finally:
        client.delete(*keys)
        client.close()


def login(client, email):  # type: ignore[no-untyped-def]
    response = client.post("/api/v1/auth/password/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": "Bearer " + response.json()["access_token"]}


def send(client, headers):  # type: ignore[no-untyped-def]
    result = client.post("/api/v1/auth/email/send", headers=headers)
    assert result.status_code == 200, result.text
    return result.json()["challenge_id"], client.app.state.verification_mailer.messages[-1][1]


def confirm(client, headers, challenge, code):  # type: ignore[no-untyped-def]
    return client.post(
        "/api/v1/auth/email/verify", headers=headers, json={"challenge_id": challenge, "code": code}
    )


def test_email_proof_never_grants_mfa_or_changes_roles(email_client, account, db_url):  # type: ignore[no-untyped-def]
    headers = login(email_client, account[0])
    challenge, code = send(email_client, headers)
    assert email_client.app.state.verification_mailer.messages[-1][0] == account[0]
    accepted = confirm(email_client, headers, challenge, code)
    assert accepted.status_code == 200 and accepted.json() == {"email_verified": True, "grants_mfa": False}
    identity = email_client.get("/api/v1/auth/session", headers=headers).json()
    assert identity["roles"] == ["REVIEWER"] and identity["mfa_satisfied"] is False
    status = email_client.get("/api/v1/auth/email/status", headers=headers).json()
    assert status["email_verified"] is True and status["grants_mfa"] is False
    with transaction(session_factory(db_url)) as session:
        row = session.get(EmailVerificationChallenge, uuid.UUID(challenge))
        assert code not in row.code_hash and account[0] not in row.email_digest
        assert session.scalar(select(TotpGrant)) is None
    response = email_client.post(
        "/api/v1/approvals/" + str(uuid.uuid4()) + "/decision",
        headers={**headers, "Idempotency-Key": "email-mfa-" + uuid.uuid4().hex},
        json={
            "decision": "RETURN_FOR_REWORK",
            "rationale": "Synthetic email proof is not MFA",
            "report_version_id": str(uuid.uuid4()),
            "snapshot_id": str(uuid.uuid4()),
        },
    )
    assert response.status_code == 401 and response.json()["error"]["code"] == "MFA_REQUIRED"


def test_proof_bound_to_session_and_consumed_once(email_client, account):  # type: ignore[no-untyped-def]
    first, second = login(email_client, account[0]), login(email_client, account[0])
    challenge, code = send(email_client, first)
    assert confirm(email_client, second, challenge, code).status_code == 403
    assert confirm(email_client, first, challenge, code).status_code == 200
    assert confirm(email_client, first, challenge, code).status_code == 403
    assert email_client.get("/api/v1/auth/email/status", headers=second).json()["email_verified"] is False


@pytest.mark.parametrize(
    "change",
    [
        "expired",
        "credential_version",
        "email_changed",
        "delivery_failed",
        "tenant_suspended",
        "principal_suspended",
    ],
)
def test_revocation_and_expiry(email_client, account, db_url, change):  # type: ignore[no-untyped-def]
    headers = login(email_client, account[0])
    challenge, code = send(email_client, headers)
    with transaction(session_factory(db_url)) as session:
        row = session.get(EmailVerificationChallenge, uuid.UUID(challenge))
        credential = session.get(PasswordCredential, account[1])
        if change == "expired":
            row.created_at = datetime.now(UTC) - timedelta(minutes=10)
            row.expires_at = row.created_at + timedelta(minutes=5)
        elif change == "credential_version":
            credential.version += 1
        elif change == "email_changed":
            credential.email = "changed-" + uuid.uuid4().hex + "@example.com"
        elif change == "delivery_failed":
            row.delivery_status = "FAILED"
        elif change == "tenant_suspended":
            session.get(Tenant, account[3]).status = "SUSPENDED"
        elif change == "principal_suspended":
            session.get(Principal, account[2]).status = "SUSPENDED"
    assert confirm(email_client, headers, challenge, code).status_code in (401, 403)


def test_wrong_codes_lock_challenge_with_persisted_counters(email_client, account, db_url):  # type: ignore[no-untyped-def]
    headers = login(email_client, account[0])
    challenge, code = send(email_client, headers)
    for _ in range(5):
        assert confirm(email_client, headers, challenge, "abcdef").status_code == 403
    with transaction(session_factory(db_url)) as session:
        row = session.get(EmailVerificationChallenge, uuid.UUID(challenge))
        assert row.failed_attempts == 5 and row.cancelled_at is not None
    assert confirm(email_client, headers, challenge, code).status_code == 429


def test_delivery_failure_is_not_reported_as_sent(email_client, account, db_url):  # type: ignore[no-untyped-def]
    headers = login(email_client, account[0])
    email_client.app.state.verification_mailer.fail = True
    assert email_client.post("/api/v1/auth/email/send", headers=headers).status_code == 503
    with transaction(session_factory(db_url)) as session:
        row = session.scalar(select(EmailVerificationChallenge))
        assert row.delivery_status == "FAILED" and row.cancelled_at is not None
        assert row.consumed_at is None


def test_resend_cooldown_and_registered_recipient_only(email_client, account):  # type: ignore[no-untyped-def]
    headers = login(email_client, account[0])
    response = email_client.post(
        "/api/v1/auth/email/send", headers=headers, json={"recipient": "other@example.com"}
    )
    assert (
        response.status_code == 200
        and email_client.app.state.verification_mailer.messages[-1][0] == account[0]
    )
    assert email_client.post("/api/v1/auth/email/send", headers=headers).status_code == 429


def test_disabling_feature_and_redis_failure_fail_closed(email_client, account):  # type: ignore[no-untyped-def]
    headers = login(email_client, account[0])
    email_client.app.state.settings.redis_url = None
    assert email_client.post("/api/v1/auth/email/send", headers=headers).status_code == 503
    assert email_client.app.state.verification_mailer.messages == []
    email_client.app.state.settings.email_verification_enabled = False
    assert email_client.post("/api/v1/auth/email/send", headers=headers).status_code == 403


def test_extra_claims_rejected_and_logout_revokes_proof(email_client, account):  # type: ignore[no-untyped-def]
    headers = login(email_client, account[0])
    challenge, code = send(email_client, headers)
    response = email_client.post(
        "/api/v1/auth/email/verify",
        headers=headers,
        json={"challenge_id": challenge, "code": code, "mfa": True, "tenant_id": str(account[3])},
    )
    assert response.status_code == 400
    assert email_client.post("/api/v1/auth/password/logout", headers=headers).status_code == 204
    assert confirm(email_client, headers, challenge, code).status_code == 401


def test_concurrent_code_consumption_only_one_success(email_client, account):  # type: ignore[no-untyped-def]
    from concurrent.futures import ThreadPoolExecutor

    headers = login(email_client, account[0])
    challenge, code = send(email_client, headers)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(lambda _: confirm(email_client, headers, challenge, code).status_code, range(2))
        )
    assert sorted(results) == [200, 403]


def test_new_delivery_cancels_previous_unused_code(email_client, account, db_url):  # type: ignore[no-untyped-def]
    headers = login(email_client, account[0])
    old, old_code = send(email_client, headers)
    with transaction(session_factory(db_url)) as session:
        row = session.get(EmailVerificationChallenge, uuid.UUID(old))
        row.created_at -= timedelta(seconds=61)
        row.expires_at -= timedelta(seconds=61)
    current, current_code = send(email_client, headers)
    assert current != old
    assert confirm(email_client, headers, old, old_code).status_code == 403
    assert confirm(email_client, headers, current, current_code).status_code == 200


def test_cross_tenant_session_cannot_consume_challenge(email_client, account, db_url):  # type: ignore[no-untyped-def]
    first = login(email_client, account[0])
    challenge, code = send(email_client, first)
    with transaction(session_factory(db_url)) as session:
        other = session.scalar(select(Principal).where(Principal.subject == subject("b", "reviewer1")))
        credential = PasswordCredential(
            tenant_id=other.tenant_id,
            principal_id=other.id,
            email=uuid.uuid4().hex + "@example.com",
            password_hash=hash_password(PASSWORD),
        )
        session.add(credential)
        session.flush()
        address = credential.email
    second = login(email_client, address)
    assert confirm(email_client, second, challenge, code).status_code == 403
    assert confirm(email_client, first, challenge, code).status_code == 200
