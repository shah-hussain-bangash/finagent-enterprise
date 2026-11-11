"""Signed Google credentials use existing database scope and revocation rules."""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import select, update

from finagent.core.config import AuthMode
from finagent.database.models import Principal as PrincipalRow
from finagent.database.models import Tenant
from finagent.database.session import session_factory, transaction
from finagent.security.authentication.tokens import GoogleTokenValidator

ISSUER = "https://accounts.google.com"
CLIENT = "123456789-synthetic.apps.googleusercontent.com"
SUBJECT = "123456789012345678901"


@pytest.mark.req("FR-001", "SPR-004", "SPR-005")
@pytest.mark.parametrize(
    "scenario",
    [
        "active",
        "unknown",
        "wrong-audience",
        "suspended",
        "removed",
        "tenant-suspended",
        "integration",
    ],
)
def test_google_signed_credential_requires_active_trusted_human(
    client: Any, db_url: str, monkeypatch: pytest.MonkeyPatch, scenario: str
) -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    validator = GoogleTokenValidator(CLIENT)
    monkeypatch.setattr(
        validator._jwks,
        "get_signing_key_from_jwt",
        lambda _: SimpleNamespace(key=private_key.public_key()),
    )
    source = "a.tm-connector@synthetic" if scenario == "integration" else "a.analyst1@synthetic"
    with transaction(session_factory(db_url)) as session:
        if scenario != "unknown":
            session.execute(
                update(PrincipalRow)
                .where(PrincipalRow.subject == source)
                .values(issuer=ISSUER, subject=SUBJECT)
            )
        if scenario in {"suspended", "removed"}:
            session.execute(
                update(PrincipalRow)
                .where(PrincipalRow.issuer == ISSUER, PrincipalRow.subject == SUBJECT)
                .values(status=scenario.upper())
            )
        if scenario == "tenant-suspended":
            session.execute(
                update(Tenant)
                .where(
                    Tenant.id.in_(
                        select(PrincipalRow.tenant_id).where(
                            PrincipalRow.issuer == ISSUER,
                            PrincipalRow.subject == SUBJECT,
                        )
                    )
                )
                .values(status="SUSPENDED")
            )
    now = int(time.time())
    credential = jwt.encode(
        {
            "iss": ISSUER,
            "aud": "other-client" if scenario == "wrong-audience" else CLIENT,
            "sub": SUBJECT,
            "iat": now,
            "exp": now + 600,
            "amr": ["mfa"],
            "roles": ["TENANT_ADMIN"],
            "permissions": ["tenant:admin"],
            "email": "a.analyst1@synthetic",
        },
        private_key,
        algorithm="RS256",
    )
    before = client.app.state.token_validator
    before_mode = client.app.state.settings.auth_mode
    client.app.state.settings.auth_mode = AuthMode.GOOGLE
    client.app.state.token_validator = validator
    try:
        assert client.get("/api/v1/auth/config").json() == {"mode": "google"}
        response = client.get("/api/v1/auth/session", headers={"Authorization": f"Bearer {credential}"})
        assert response.status_code == (200 if scenario == "active" else 401), response.text
        if scenario == "active":
            assert response.json()["subject"] == SUBJECT
            assert response.json()["issuer"] == ISSUER
            assert response.json()["mfa_satisfied"] is False
            # Roles and permissions come from database membership, not these signed token claims.
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
                client.get(
                    "/api/v1/admin/config-versions", headers={"Authorization": f"Bearer {credential}"}
                ).status_code
                == 403
            )
            # Database membership is re-read on every request; the same token cannot bypass revocation.
            with transaction(session_factory(db_url)) as session:
                session.execute(
                    update(PrincipalRow)
                    .where(PrincipalRow.issuer == ISSUER, PrincipalRow.subject == SUBJECT)
                    .values(status="SUSPENDED")
                )
            assert (
                client.get(
                    "/api/v1/auth/session",
                    headers={"Authorization": f"Bearer {credential}"},
                ).status_code
                == 401
            )
    finally:
        client.app.state.token_validator = before
        client.app.state.settings.auth_mode = before_mode
