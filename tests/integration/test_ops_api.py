"""Development sign-in (local/test only), case activity and operations views (P11 backend support)."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from tests.helpers import assigned_analyst, ingest, subject

pytestmark = pytest.mark.integration


@pytest.mark.req("SPR-004", "FR-001", "MST:SEC-003")
@pytest.mark.release_blocker
def test_dev_sign_in_exists_only_for_local_issuer(client, make_client) -> None:  # type: ignore[no-untyped-def]
    ids = client.get("/api/v1/dev/identities").json()["items"]
    assert any(i["subject"] == "a.reviewer1@synthetic" and i["roles"] == ["REVIEWER"] for i in ids)
    assert all(i["subject"] != "a.tm-connector@synthetic" for i in ids)  # integration identities excluded
    tok = client.post("/api/v1/dev/session", json={"subject": "a.analyst1@synthetic"}).json()["access_token"]
    assert client.get("/api/v1/cases", headers={"Authorization": f"Bearer {tok}"}).status_code == 200
    assert client.post("/api/v1/dev/session", json={"subject": "z.nobody@synthetic"}).status_code == 401
    assert client.post("/api/v1/dev/session", json={"subject": "a.tm-connector@synthetic"}).status_code == 401
    no_mfa = client.post(
        "/api/v1/dev/session", json={"subject": "a.supervisor1@synthetic", "mfa": False}
    ).json()
    r = client.post(
        f"/api/v1/cases/{uuid.uuid4()}/assignment",
        headers={"Authorization": f"Bearer {no_mfa['access_token']}"},
        json={"principal_id": str(uuid.uuid4()), "role": "ANALYST", "reason": "step-up check"},
    )
    assert r.status_code == 401 and r.json()["error"]["code"] == "MFA_REQUIRED"


@pytest.mark.req("FR-036", "FR-010", "FR-002")
def test_case_activity_is_scoped_to_case_readers(client, token) -> None:  # type: ignore[no-untyped-def]
    case_id = ingest(client, token)["case_id"]
    analyst = assigned_analyst(client, token, case_id)
    items: list[dict[str, Any]] = client.get(
        f"/api/v1/cases/{case_id}/activity", headers=token(analyst)
    ).json()["items"]
    assert "CASE_CREATED" in {i["action"] for i in items} and all("details" not in i for i in items)
    other = "a.analyst2@synthetic" if analyst == "a.analyst1@synthetic" else "a.analyst1@synthetic"
    for who in (other, subject("b", "supervisor1"), subject("a", "tenantadmin1")):
        assert client.get(f"/api/v1/cases/{case_id}/activity", headers=token(who)).status_code in (403, 404)
    runs = client.get(f"/api/v1/cases/{case_id}/workflows", headers=token(analyst)).json()
    assert runs == {"items": []}


@pytest.mark.req("MST:REC-003", "FR-033")
def test_operations_queue_is_operator_only(client, token) -> None:  # type: ignore[no-untyped-def]
    assert client.get("/api/v1/operations", headers=token(subject("a", "operator1"))).json() == {"items": []}
    assert client.get("/api/v1/operations", headers=token(subject("a", "analyst1"))).status_code == 403
    agents = client.get("/api/v1/agents", headers=token(subject("a", "analyst1"))).json()["items"]
    assert {a["capability_id"] for a in agents} >= {"AG-KYC", "AG-RISK"}
    assert all("prompt_text" not in a for a in agents)
