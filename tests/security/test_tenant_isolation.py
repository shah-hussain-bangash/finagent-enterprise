"""Designed cross-tenant and access-control attacks (prompt §18, §31; SRS T-04; SPR-009..013)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import update

from finagent.database.models import Principal, RoleAssignment
from finagent.database.seeds import sid
from finagent.database.session import session_factory, transaction

pytestmark = [pytest.mark.integration, pytest.mark.release_blocker]


def _alert(source: str, ref: str) -> dict:  # type: ignore[type-arg]
    return {
        "schema_version": "1.0",
        "event_id": f"evt-{uuid.uuid4()}",
        "source_system": source,
        "source_event_time": "2026-10-15T07:00:00Z",
        "payload": {
            "source_alert_id": ref,
            "alert_type": "TM_STRUCTURING",
            "occurred_at": "2026-10-15T06:59:00Z",
            "subject_refs": ["customer-test-001"],
            "window_start": "2026-09-15T00:00:00Z",
            "window_end": "2026-10-15T00:00:00Z",
        },
    }


@pytest.fixture
def case_a(client, token) -> str:  # type: ignore[no-untyped-def]
    r = client.post(
        "/api/v1/alerts",
        json=_alert("tm-synthetic-a", "AML-A-1"),
        headers={**token("a.tm-connector@synthetic"), "Idempotency-Key": "seed-key-a-0001"},
    )
    assert r.status_code == 201
    return str(r.json()["case_id"])


def _body_minus_corr(resp) -> dict:  # type: ignore[no-untyped-def,type-arg]
    body = resp.json()
    body["error"].pop("correlation_id")
    return body


@pytest.mark.req("BR-002", "FR-002", "SPR-009", "SPR-010", "NFR-002", "MST:API-002", "MST:SEC-001", "DIR-002")
def test_tenant_b_get_tenant_a_case_is_indistinguishable_from_absent(client, token, case_a) -> None:  # type: ignore[no-untyped-def]
    for subject in ("b.supervisor1@synthetic", "b.analyst1@synthetic", "b.reviewer1@synthetic"):
        attack = client.get(f"/api/v1/cases/{case_a}", headers=token(subject))
        absent = client.get(f"/api/v1/cases/{uuid.uuid4()}", headers=token(subject))
        assert attack.status_code == absent.status_code == 404
        assert _body_minus_corr(attack) == _body_minus_corr(absent)
        assert case_a not in attack.text and "AML-A-1" not in attack.text


@pytest.mark.req("FR-010", "SPR-009", "NFR-002")
def test_tenant_b_lists_and_filters_never_include_tenant_a(client, token, case_a) -> None:  # type: ignore[no-untyped-def]
    for status in (None, "ASSIGNED"):
        params = {"status": status} if status else {}
        r = client.get("/api/v1/cases", params=params, headers=token("b.supervisor1@synthetic"))
        assert r.status_code == 200 and r.json() == {"items": [], "next_cursor": None}
    own = client.get("/api/v1/cases", headers=token("a.supervisor1@synthetic")).json()["items"]
    assert [c["case_id"] for c in own] == [case_a]


@pytest.mark.req("FR-001", "SPR-010", "DIR-002")
def test_forged_tenant_headers_and_query_params_are_ignored(client, token, case_a) -> None:  # type: ignore[no-untyped-def]
    headers = {
        **token("b.supervisor1@synthetic"),
        "X-Tenant-Id": str(sid("tenant:a")),
        "X-Role": "AML_SUPERVISOR",
    }
    r = client.get(f"/api/v1/cases/{case_a}", params={"tenant_id": str(sid("tenant:a"))}, headers=headers)
    assert r.status_code == 404


@pytest.mark.req("FR-002", "DIR-003", "SPR-009")
def test_admin_operator_auditor_have_no_case_content(client, token, case_a) -> None:  # type: ignore[no-untyped-def]
    for subject in (
        "a.tenantadmin1@synthetic",
        "a.operator1@synthetic",
        "a.auditor1@synthetic",
        "a.policyadmin1@synthetic",
        "a.tm-connector@synthetic",
    ):
        assert client.get(f"/api/v1/cases/{case_a}", headers=token(subject)).status_code == 403
        assert client.get("/api/v1/cases", headers=token(subject)).status_code == 403


@pytest.mark.req("FR-002", "FR-009")
def test_analyst_sees_only_assigned_case_supervisor_sees_tenant(client, token, case_a) -> None:  # type: ignore[no-untyped-def]
    detail = client.get(f"/api/v1/cases/{case_a}", headers=token("a.supervisor1@synthetic")).json()
    assigned = detail["assigned_analyst_id"]
    owner, other = (
        ("a.analyst1@synthetic", "a.analyst2@synthetic")
        if assigned == str(sid("principal:a:analyst1"))
        else ("a.analyst2@synthetic", "a.analyst1@synthetic")
    )
    assert client.get(f"/api/v1/cases/{case_a}", headers=token(owner)).status_code == 200
    assert client.get(f"/api/v1/cases/{case_a}", headers=token(other)).status_code == 404
    assert [t["to"] for t in detail["timeline"]] == ["RECEIVED", "ASSIGNED"]


@pytest.mark.req("FR-001", "SPR-004", "SPR-006", "DIR-033")
def test_unknown_suspended_and_revoked_principals_lose_access_immediately(
    client, token, db_url, case_a
) -> None:  # type: ignore[no-untyped-def]
    assert client.get("/api/v1/cases", headers=token("nobody@synthetic")).status_code == 401
    assert client.get("/api/v1/cases").status_code == 401
    sup = token("a.supervisor1@synthetic")
    assert client.get(f"/api/v1/cases/{case_a}", headers=sup).status_code == 200
    with transaction(session_factory(db_url)) as s:  # revoke role -> next request denied
        s.execute(
            update(RoleAssignment)
            .where(RoleAssignment.principal_id == sid("principal:a:supervisor1"))
            .values(valid_to=RoleAssignment.valid_from)
        )
    assert client.get(f"/api/v1/cases/{case_a}", headers=sup).status_code == 403
    with transaction(session_factory(db_url)) as s:  # suspend principal -> unauthenticated
        s.execute(
            update(Principal).where(Principal.id == sid("principal:a:supervisor1")).values(status="SUSPENDED")
        )
    assert client.get(f"/api/v1/cases/{case_a}", headers=sup).status_code == 401


@pytest.mark.req("FR-036", "SPR-035", "MST:OPS-001")
def test_denied_reads_are_audited_in_requesters_tenant_only(client, token, case_a) -> None:  # type: ignore[no-untyped-def]
    client.get(f"/api/v1/cases/{case_a}", headers=token("b.supervisor1@synthetic"))
    b_events = client.get("/api/v1/audit-events", headers=token("b.auditor1@synthetic")).json()["items"]
    assert [e["action"] for e in b_events] == ["CASE_READ_DENIED_OR_ABSENT"]
    assert all(e["object_id"] is None and e["case_id"] is None for e in b_events)
    a_actions = [
        e["action"]
        for e in client.get("/api/v1/audit-events", headers=token("a.auditor1@synthetic")).json()["items"]
    ]
    assert "CASE_READ_DENIED_OR_ABSENT" not in a_actions
    assert client.get("/api/v1/audit-events", headers=token("a.analyst1@synthetic")).status_code == 403


@pytest.mark.req("SPR-013", "FR-002")
def test_malformed_ids_and_cursors_fail_safely(client, token) -> None:  # type: ignore[no-untyped-def]
    sup = token("a.supervisor1@synthetic")
    for bad in ("1' OR '1'='1", "..%2F..%2Fetc%2Fpasswd", "00000000"):
        r = client.get(f"/api/v1/cases/{bad}", headers=sup)
        assert r.status_code == 404 and "Traceback" not in r.text
    r = client.get("/api/v1/cases", params={"cursor": "!!notbase64"}, headers=sup)
    assert r.status_code == 400
    r = client.get("/api/v1/cases", params={"limit": 100000}, headers=sup)
    assert r.status_code == 200  # clamped to page_size_max, not an unbounded scan
