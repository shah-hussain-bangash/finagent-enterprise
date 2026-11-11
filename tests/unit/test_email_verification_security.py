"""Email code security: keyed binding, strict configuration and encrypted Gmail transport."""

import base64
import ssl
from uuid import uuid4

import pytest
from pydantic import ValidationError

from finagent.core.config import Settings
from finagent.core.errors import AppError
from finagent.database.models import EmailVerificationChallenge
from finagent.security.authentication import email_verification as email

pytestmark = [pytest.mark.release_blocker, pytest.mark.req("FR-001", "SPR-004")]


def settings(**changes):  # type: ignore[no-untyped-def]
    values = dict(
        environment="test",
        auth_mode="google",
        google_web_client_id="test.apps.googleusercontent.com",
        database_url="postgresql+psycopg://unused@localhost/unused",
        redis_url="redis://localhost/0",
        password_login_enabled=True,
        email_verification_enabled=True,
        gmail_sender="synthetic-sender@gmail.com",
        gmail_app_password="abcdefghijklmnop",
        email_verification_key=base64.b64encode(b"K" * 32).decode(),
    )
    values.update(changes)
    return Settings(_env_file=None, **values)


@pytest.mark.parametrize("field", ["tenant_id", "id", "password_session_id", "credential_id"])
def test_digest_binds_every_identity(field):  # type: ignore[no-untyped-def]
    row = EmailVerificationChallenge(
        tenant_id=uuid4(),
        id=uuid4(),
        credential_id=uuid4(),
        password_session_id=uuid4(),
        credential_version=1,
        email_digest="bound-email",
    )
    original = email.code_digest(settings(), row, "123456")
    setattr(row, field, uuid4())
    assert email.code_digest(settings(), row, "123456") != original


def test_digest_binds_code_email_version_and_key():
    row = EmailVerificationChallenge(
        tenant_id=uuid4(),
        id=uuid4(),
        credential_id=uuid4(),
        password_session_id=uuid4(),
        credential_version=1,
        email_digest="bound-email",
    )
    config = settings()
    original = email.code_digest(config, row, "123456")
    assert len(original) == 64 and "123456" not in original
    assert email.code_digest(config, row, "654321") != original
    row.credential_version = 2
    assert email.code_digest(config, row, "123456") != original
    row.credential_version = 1
    row.email_digest = "another-email"
    assert email.code_digest(config, row, "123456") != original
    row.email_digest = "bound-email"
    assert (
        email.code_digest(
            settings(email_verification_key=base64.b64encode(b"X" * 32).decode()), row, "123456"
        )
        != original
    )


@pytest.mark.parametrize("environment", ["dev", "staging", "production"])
def test_feature_rejected_outside_local_test(environment):  # type: ignore[no-untyped-def]
    with pytest.raises(ValidationError):
        settings(environment=environment)


@pytest.mark.parametrize("key", ["", "broken", base64.b64encode(b"k" * 16).decode()])
def test_invalid_key_rejected(key):  # type: ignore[no-untyped-def]
    with pytest.raises(ValidationError):
        settings(email_verification_key=key)


@pytest.mark.parametrize(
    "changes",
    [
        {"password_login_enabled": False},
        {"gmail_sender": None},
        {"gmail_app_password": None},
        {"gmail_app_password": "normal account password"},
        {"gmail_sender": "sender@example.com\r\nBcc: other@example.com"},
    ],
)
def test_invalid_delivery_configuration_rejected(changes):  # type: ignore[no-untyped-def]
    with pytest.raises(ValidationError):
        settings(**changes)


def test_disabled_feature_never_sends(monkeypatch):  # type: ignore[no-untyped-def]
    calls = []
    monkeypatch.setattr(email.smtplib, "SMTP_SSL", lambda *a, **k: calls.append(a))
    with pytest.raises(AppError):
        email.GmailMailer(settings(email_verification_enabled=False)).send_code(
            "recipient@example.com", "123456"
        )
    assert calls == []


def test_gmail_delivery_uses_verified_tls_fixed_endpoint(monkeypatch):  # type: ignore[no-untyped-def]
    recorded = {}

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def login(self, username, password):
            recorded["login"] = (username, password)

        def send_message(self, message):
            recorded["message"] = message
            return {}

    def connect(host, port, **kwargs):
        assert host == "smtp.gmail.com" and port == 465 and kwargs["timeout"] == 15
        context = kwargs["context"]
        assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
        return Client()

    monkeypatch.setattr(email.smtplib, "SMTP_SSL", connect)
    email.GmailMailer(settings()).send_code("recipient@example.com", "123456")
    message = recorded["message"]
    assert message["To"] == "recipient@example.com" and "123456" in message.get_content()
    assert "investigation" in message.get_content() and recorded["login"][0] == "synthetic-sender@gmail.com"
    assert "abcdefghijklmnop" not in message.as_string()


def test_refused_delivery_is_not_success(monkeypatch):  # type: ignore[no-untyped-def]
    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def login(self, *args):
            pass

        def send_message(self, message):
            return {"recipient@example.com": (550, b"refused")}

    monkeypatch.setattr(email.smtplib, "SMTP_SSL", lambda *args, **kwargs: Client())
    with pytest.raises(AppError):
        email.GmailMailer(settings()).send_code("recipient@example.com", "123456")
