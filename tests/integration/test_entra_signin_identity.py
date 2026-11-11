"""Web sign-in identity endpoint uses the normal API token and database resolver."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select, update

from finagent.core.config import AuthMode
from finagent.database.models import Principal as PrincipalRow
from finagent.database.models import Tenant
from finagent.database.session import session_factory, transaction
from finagent.security.authentication.tokens import VerifiedIdentity


@pytest.mark.req("FR-001", "SPR-004")
def test_public_auth_config_exposes_only_mode(client) -> None:  # type: ignore[no-untyped-def]
    response = client.get("/api/v1/auth/config")
    assert response.status_code == 200
    assert response.json() == {"mode": "local"}
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.req("FR-001", "SPR-004")
def test_identity_endpoint_requires_valid_bearer(client, token) -> None:  # type: ignore[no-untyped-def]
    for headers in ({}, {"Authorization": "Bearer forged"}):
        assert client.get("/api/v1/auth/session", headers=headers).status_code == 401
    response = client.get("/api/v1/auth/session", headers=token("a.analyst1@synthetic"))
    assert response.status_code == 200
    assert response.json()["subject"] == "a.analyst1@synthetic"
    assert response.json()["mfa_satisfied"] is True
    assert response.json()["roles"] == ["ANALYST"]
    assert response.json()["permissions"] == sorted(
        {
            "CASE_READ",
            "CASE_UPDATE",
            "WORKFLOW_START",
            "WORKFLOW_CANCEL",
            "REPORT_EDIT",
            "REPORT_SUBMIT",
            "KNOWLEDGE_SEARCH",
            "DOCUMENT_UPLOAD",
            "DOCUMENT_DOWNLOAD",
        }
    )
    assert set(response.json()) == {
        "issuer",
        "subject",
        "display_name",
        "roles",
        "permissions",
        "mfa_satisfied",
    }
    assert (
        client.get("/api/v1/admin/config-versions", headers=token("a.analyst1@synthetic")).status_code == 403
    )


@pytest.mark.req("FR-001", "SPR-004", "SPR-005")
def test_identity_endpoint_does_not_invent_mfa(client, token) -> None:  # type: ignore[no-untyped-def]
    response = client.get("/api/v1/auth/session", headers=token("a.analyst1@synthetic", mfa=False))
    assert response.status_code == 200
    assert response.json()["mfa_satisfied"] is False


@pytest.mark.req("FR-001", "SPR-004")
def test_identity_endpoint_rejects_unknown_and_integration_subjects(client, token) -> None:  # type: ignore[no-untyped-def]
    for subject in ("a.unknown@synthetic", "a.tm-connector@synthetic"):
        response = client.get("/api/v1/auth/session", headers=token(subject))
        assert response.status_code == 401


@pytest.mark.req("FR-001", "SPR-004")
@pytest.mark.parametrize("status", ["SUSPENDED", "REMOVED"])
def test_identity_endpoint_rejects_inactive_principal(client, token, db_url, status) -> None:  # type: ignore[no-untyped-def]
    with transaction(session_factory(db_url)) as session:
        session.execute(
            update(PrincipalRow).where(PrincipalRow.subject == "a.analyst1@synthetic").values(status=status)
        )
    assert client.get("/api/v1/auth/session", headers=token("a.analyst1@synthetic")).status_code == 401


@pytest.mark.req("FR-001", "SPR-004")
def test_identity_endpoint_rejects_suspended_tenant(client, token, db_url) -> None:  # type: ignore[no-untyped-def]
    with transaction(session_factory(db_url)) as session:
        session.execute(
            update(Tenant)
            .where(
                Tenant.id.in_(
                    select(PrincipalRow.tenant_id).where(PrincipalRow.subject == "a.analyst1@synthetic")
                )
            )
            .values(status="SUSPENDED")
        )
    assert client.get("/api/v1/auth/session", headers=token("a.analyst1@synthetic")).status_code == 401


@pytest.mark.req("FR-001", "SPR-004")
@pytest.mark.parametrize("has_scope", [False, True])
def test_entra_callback_identity_requires_delegated_api_scope(client, db_url, has_scope) -> None:  # type: ignore[no-untyped-def]
    issuer = "https://login.microsoftonline.com/11111111-1111-1111-1111-111111111111/v2.0"
    oid = str(uuid.uuid4())
    with transaction(session_factory(db_url)) as session:
        session.execute(
            update(PrincipalRow)
            .where(PrincipalRow.subject == "a.analyst1@synthetic")
            .values(issuer=issuer, subject=oid)
        )

    class FakeValidator:
        def validate(self, _: str) -> VerifiedIdentity:
            return VerifiedIdentity(
                issuer,
                oid,
                False,
                frozenset({"access_as_user"}) if has_scope else frozenset(),
            )

    original_validator = client.app.state.token_validator
    client.app.state.settings.auth_mode = AuthMode.ENTRA
    client.app.state.token_validator = FakeValidator()
    try:
        response = client.get("/api/v1/auth/session", headers={"Authorization": "Bearer synthetic"})
        assert response.status_code == (200 if has_scope else 401)
        if has_scope:
            assert response.json()["subject"] == oid
            assert response.json()["issuer"] == issuer
            assert response.json()["mfa_satisfied"] is False
    finally:
        client.app.state.settings.auth_mode = AuthMode.LOCAL
        client.app.state.token_validator = original_validator
