"""The operator harness submits through the API, preserves assignment and revokes secrets."""

from __future__ import annotations

import uuid

import httpx
import pytest
from sqlalchemy import func, select

from finagent.database.models import Case, ConnectorCredential, RoleAssignment
from finagent.database.session import session_factory, transaction
from finagent.integrations.local_connector_demo import run_demo
from tests.integration import test_connector_intake_auth

connector_client = test_connector_intake_auth.connector_client
connector_settings = test_connector_intake_auth.connector_settings

pytestmark = [
    pytest.mark.integration,
    pytest.mark.release_blocker,
    pytest.mark.req("FR-007", "FR-008", "SPR-004"),
]


def test_demo_uses_real_intake_replay_and_revocation_without_changing_roles(
    connector_client, connector_settings, db_url
):  # type: ignore[no-untyped-def]
    with transaction(session_factory(db_url)) as session:
        roles_before = session.scalar(select(func.count()).select_from(RoleAssignment))
    run_id = uuid.uuid4()
    first = run_demo(connector_settings, db_url, run_id, client=connector_client)
    replay = run_demo(connector_settings, db_url, run_id, client=connector_client)
    assert first["case_id"] == replay["case_id"]
    assert first["new_case_created"] is True and replay["new_case_created"] is False
    assert first["credential_revoked"] is True and first["revoked_credential_rejected"] is True
    assert first["connector_case_read_denied"] is True and first["assigned_analyst_id"]
    assert first["human_roles_changed"] is False and first["workflow_started"] is False
    assert "fa_int_" not in str(first)
    with transaction(session_factory(db_url)) as session:
        assert session.scalar(select(func.count()).select_from(Case)) == 1
        assert session.scalar(select(func.count()).select_from(RoleAssignment)) == roles_before
        rows = list(session.scalars(select(ConnectorCredential)))
        assert len(rows) == 2 and all(row.revoked_at is not None for row in rows)


def test_failed_http_intake_still_revokes_credential(
    connector_client, connector_settings, db_url, monkeypatch
):  # type: ignore[no-untyped-def]
    monkeypatch.setattr(connector_client, "post", lambda *args, **kwargs: httpx.Response(503))
    with pytest.raises(RuntimeError, match="ALERT_INTAKE_HTTP_503"):
        run_demo(connector_settings, db_url, uuid.uuid4(), client=connector_client)
    with transaction(session_factory(db_url)) as session:
        assert session.scalar(select(func.count()).select_from(Case)) == 0
        rows = list(session.scalars(select(ConnectorCredential)))
        assert len(rows) == 1 and rows[0].revoked_at is not None


def test_http_client_construction_failure_still_revokes_credential(connector_settings, db_url, monkeypatch):  # type: ignore[no-untyped-def]
    def fail(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("SIMULATED_HTTP_CLIENT_CONSTRUCTION_FAILURE")

    monkeypatch.setattr("finagent.integrations.local_connector_demo.httpx.Client", fail)
    with pytest.raises(RuntimeError, match="SIMULATED_HTTP_CLIENT_CONSTRUCTION_FAILURE"):
        run_demo(connector_settings, db_url, uuid.uuid4())
    with transaction(session_factory(db_url)) as session:
        rows = list(session.scalars(select(ConnectorCredential)))
        assert len(rows) == 1 and rows[0].revoked_at is not None
