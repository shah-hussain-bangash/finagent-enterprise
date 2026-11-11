"""Real PostgreSQL connector auth alongside Google and password sign-in."""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from finagent.api.main import create_app
from finagent.core.config import Environment, Settings
from finagent.core.errors import AppError
from finagent.database.models import (
    Case,
    CaseAssignment,
    ConnectorCredential,
    IntegrationConnection,
    PasswordCredential,
    Principal,
    RoleAssignment,
    Tenant,
)
from finagent.database.session import session_factory, transaction
from finagent.security.authentication import connector_credentials as credentials
from finagent.security.authentication.passwords import hash_password
from finagent.services.audit_service.writer import verify_chain
from finagent.services.case_service.intake import _least_loaded_analyst
from tests.helpers import CONNECTOR, alert_envelope, subject

pytestmark = [pytest.mark.integration, pytest.mark.release_blocker, pytest.mark.req("FR-001", "SPR-004")]


@pytest.fixture
def connector_settings(app_db_url: str, blob_root: str) -> Settings:
    redis_url = os.environ.get("FINAGENT_TEST_REDIS_URL")
    if not redis_url:
        pytest.skip("FINAGENT_TEST_REDIS_URL required; connector coexistence tests NOT RUN")
    return Settings(
        _env_file=None,
        environment="test",
        auth_mode="google",
        database_url=app_db_url,
        google_web_client_id="synthetic.apps.googleusercontent.com",
        blob_local_root=blob_root,
        local_connector_auth_enabled=True,
        password_login_enabled=True,
        redis_url=redis_url,
    )


@pytest.fixture
def connector_client(connector_settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(connector_settings), raise_server_exceptions=False) as client:
        yield client


@pytest.fixture
def credential(db_url: str, connector_settings: Settings):  # type: ignore[no-untyped-def]
    with transaction(session_factory(db_url)) as session:
        principal = session.scalar(select(Principal).where(Principal.subject == CONNECTOR["a"]))
        assert principal is not None
        raw, row = credentials.issue_local_credential(
            session, connector_settings, principal.id, reason="Synthetic connector integration verification"
        )
        return raw, row.id, principal.id, principal.tenant_id


def post(client, credential, envelope=None, key=None):  # type: ignore[no-untyped-def]
    return client.post(
        "/api/v1/alerts",
        json=envelope or alert_envelope("CONNECTOR-" + uuid.uuid4().hex),
        headers={
            "Authorization": "Bearer " + credential[0],
            "Idempotency-Key": key or "connector-" + uuid.uuid4().hex,
        },
    )


@pytest.mark.req("FR-007", "FR-008")
def test_google_mode_connector_intake_and_replay_use_normal_assignment(
    connector_client, credential, db_url, monkeypatch
):  # type: ignore[no-untyped-def]
    def reject_provider(*args):  # type: ignore[no-untyped-def]
        raise AssertionError("Opaque connector tokens must not use the human provider validator")

    monkeypatch.setattr(connector_client.app.state.token_validator, "validate", reject_provider)
    with transaction(session_factory(db_url)) as session:
        predicted = _least_loaded_analyst(session, credential[3])
    envelope, key = alert_envelope("CONNECTOR-" + uuid.uuid4().hex), "connector-" + uuid.uuid4().hex
    first = post(connector_client, credential, envelope, key)
    second = post(connector_client, credential, envelope, key)
    assert first.status_code == second.status_code == 201, first.text
    assert first.json()["case_id"] == second.json()["case_id"]
    with transaction(session_factory(db_url)) as session:
        count = session.scalar(select(func.count()).select_from(Case))
        assert count == 1
        assignment = session.scalar(
            select(CaseAssignment).where(
                CaseAssignment.case_id == uuid.UUID(first.json()["case_id"]),
                CaseAssignment.assignment_role == "ANALYST",
            )
        )
        assert assignment.principal_id == predicted
        stored = session.get(ConnectorCredential, credential[1])
        assert stored.token_hash == credentials.digest(credential[0]) and stored.scope == "alert:ingest"
        assert credential[0] not in stored.token_hash
        assert verify_chain(session, credential[3])


@pytest.mark.parametrize("path", ["/api/v1/cases", "/api/v1/auth/session", "/api/v1/admin/delegations"])
def test_connector_cannot_enter_human_or_case_routes(connector_client, credential, path):  # type: ignore[no-untyped-def]
    result = connector_client.get(path, headers={"Authorization": "Bearer " + credential[0]})
    assert result.status_code == 401, result.text


def test_connector_cannot_start_a_workflow(connector_client, credential):  # type: ignore[no-untyped-def]
    result = connector_client.post(
        "/api/v1/workflows",
        json={"case_id": str(uuid.uuid4()), "workflow_type": "durability_probe"},
        headers={"Authorization": "Bearer " + credential[0], "Idempotency-Key": "connector-workflow-denial"},
    )
    assert result.status_code == 401, result.text


@pytest.mark.parametrize("change,expected", [("source", 403), ("alert_type", 422), ("tenant_claim", 400)])
def test_source_family_and_tenant_body_checks_are_preserved(connector_client, credential, change, expected):  # type: ignore[no-untyped-def]
    envelope = alert_envelope("CONNECTOR-" + uuid.uuid4().hex)
    if change == "source":
        envelope["source_system"] = "tm-synthetic-b"
    elif change == "alert_type":
        envelope["payload"]["alert_type"] = "UNREGISTERED_FAMILY"
    else:
        envelope["tenant_id"] = str(uuid.uuid4())
    result = post(connector_client, credential, envelope)
    assert result.status_code == expected, result.text


@pytest.mark.parametrize(
    "change,expected",
    [
        ("expired", 401),
        ("revoked", 401),
        ("principal", 401),
        ("tenant", 401),
        ("connection", 401),
        ("role", 403),
        ("real_tenant", 401),
        ("disabled", 401),
        ("nonlocal", 401),
    ],
)
def test_current_revocation_membership_and_environment_apply_on_next_request(
    connector_client, credential, connector_settings, db_url, change, expected
):  # type: ignore[no-untyped-def]
    with transaction(session_factory(db_url)) as session:
        row = session.get(ConnectorCredential, credential[1])
        if change == "expired":
            row.created_at = datetime.now(UTC) - timedelta(minutes=5)
            row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        elif change == "revoked":
            row.revoked_at = datetime.now(UTC)
        elif change == "principal":
            session.get(Principal, credential[2]).status = "SUSPENDED"
        elif change == "tenant":
            session.get(Tenant, credential[3]).status = "SUSPENDED"
        elif change == "real_tenant":
            session.get(Tenant, credential[3]).is_synthetic = False
        elif change == "connection":
            session.scalar(
                select(IntegrationConnection).where(IntegrationConnection.principal_id == credential[2])
            ).status = "SUSPENDED"
        elif change == "role":
            for role in session.scalars(
                select(RoleAssignment).where(RoleAssignment.principal_id == credential[2])
            ):
                role.valid_to = datetime.now(UTC) - timedelta(seconds=1)
    if change == "disabled":
        connector_settings.local_connector_auth_enabled = False
    if change == "nonlocal":
        connector_settings.environment = Environment.PRODUCTION
    result = post(connector_client, credential)
    assert result.status_code == expected, result.text


def test_revocation_cannot_be_bypassed_with_a_previously_cached_intake_response(
    connector_client, credential, connector_settings, db_url
):  # type: ignore[no-untyped-def]
    envelope, key = alert_envelope("CONNECTOR-" + uuid.uuid4().hex), "connector-" + uuid.uuid4().hex
    assert post(connector_client, credential, envelope, key).status_code == 201
    with transaction(session_factory(db_url)) as session:
        credentials.revoke_local_credential(
            session, connector_settings, credential[1], reason="Revoke after synthetic intake"
        )
    assert post(connector_client, credential, envelope, key).status_code == 401


def test_human_principal_cannot_receive_connector_credential(connector_settings, db_url):  # type: ignore[no-untyped-def]
    with pytest.raises(AppError), transaction(session_factory(db_url)) as session:
        principal = session.scalar(select(Principal).where(Principal.subject == subject("a", "analyst1")))
        credentials.issue_local_credential(
            session, connector_settings, principal.id, reason="Reject credential for a human principal"
        )


def test_forged_database_link_to_human_is_rejected(connector_client, credential, db_url):  # type: ignore[no-untyped-def]
    with transaction(session_factory(db_url)) as session:
        human = session.scalar(select(Principal).where(Principal.subject == subject("a", "analyst1")))
        session.get(ConnectorCredential, credential[1]).principal_id = human.id
    assert post(connector_client, credential).status_code == 401


def test_database_rejects_cross_tenant_credential_link(credential, db_url):  # type: ignore[no-untyped-def]
    with pytest.raises(IntegrityError), transaction(session_factory(db_url)) as session:
        other = session.scalar(select(Tenant).where(Tenant.slug == "synthetic-bank-b"))
        session.get(ConnectorCredential, credential[1]).tenant_id = other.id
        session.flush()


def test_application_role_has_read_only_credential_privileges(app_db_url, credential):  # type: ignore[no-untyped-def]
    with transaction(session_factory(app_db_url)) as session:
        for privilege, expected in (
            ("SELECT", True),
            ("INSERT", False),
            ("UPDATE", False),
            ("DELETE", False),
        ):
            allowed = session.execute(
                text("SELECT has_table_privilege(current_user, 'connector_credentials', :p)"),
                {"p": privilege},
            ).scalar_one()
            assert allowed is expected


def test_maximum_allowed_ttl_is_usable_after_database_roundtrip(db_url, connector_settings):  # type: ignore[no-untyped-def]
    with transaction(session_factory(db_url)) as session:
        principal = session.scalar(select(Principal).where(Principal.subject == CONNECTOR["a"]))
        token, row = credentials.issue_local_credential(
            session,
            connector_settings,
            principal.id,
            ttl_seconds=3600,
            reason="Verify maximum bounded connector TTL",
        )
        credential_id = row.id
    with transaction(session_factory(db_url)) as session:
        row = session.get(ConnectorCredential, credential_id)
        assert row.expires_at - row.created_at == timedelta(seconds=3600)
        assert credentials.validate_credential(session, connector_settings, token).mfa is False


def test_google_config_and_password_analyst_access_remain_unchanged(connector_client, db_url):  # type: ignore[no-untyped-def]
    email = uuid.uuid4().hex + "@example.com"
    phrase = "Synthetic coexistence passphrase 901!"
    with transaction(session_factory(db_url)) as session:
        analyst = session.scalar(select(Principal).where(Principal.subject == subject("a", "analyst1")))
        session.add(
            PasswordCredential(
                tenant_id=analyst.tenant_id,
                principal_id=analyst.id,
                email=email,
                password_hash=hash_password(phrase),
            )
        )
    assert connector_client.get("/api/v1/auth/config").json() == {"mode": "google"}
    login = connector_client.post("/api/v1/auth/password/login", json={"email": email, "password": phrase})
    assert login.status_code == 200, login.text
    headers = {"Authorization": "Bearer " + login.json()["access_token"]}
    identity = connector_client.get("/api/v1/auth/session", headers=headers)
    assert identity.status_code == 200 and identity.json()["roles"] == ["ANALYST"]
    assert identity.json()["mfa_satisfied"] is False
    assert connector_client.get("/api/v1/cases", headers=headers).status_code == 200
    denied = connector_client.post(
        "/api/v1/alerts",
        headers={**headers, "Idempotency-Key": "human-intake-denial-" + uuid.uuid4().hex},
        json=alert_envelope(),
    )
    assert denied.status_code == 403, denied.text


def test_owner_can_revoke_after_adapter_is_disabled(connector_client, credential, connector_settings, db_url):  # type: ignore[no-untyped-def]
    connector_settings.local_connector_auth_enabled = False
    with transaction(session_factory(db_url)) as session:
        credentials.revoke_local_credential(
            session,
            connector_settings,
            credential[1],
            reason="Disable local adapter and revoke its credential",
        )
    connector_settings.local_connector_auth_enabled = True
    assert post(connector_client, credential).status_code == 401
