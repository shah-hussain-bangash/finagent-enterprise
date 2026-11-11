"""Delegation, MFA step-up, break-glass, quotas and DB privilege separation
(FR-003, SPR-005, SPR-006, SPR-008, NQ-028, SPR-035; prompt §17, §24)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError

from finagent.database.models import BreakGlassGrant, Delegation, OutboxEvent, RoleAssignment
from finagent.database.seeds import sid
from finagent.database.session import session_factory, transaction
from tests.helpers import assigned_analyst, audit_actions, ingest, principal, scalar_count, subject

pytestmark = [pytest.mark.integration]
ADMIN = subject("a", "tenantadmin1")
SUP = subject("a", "supervisor1")
RESPONDER = subject("a", "responder1")


def delegate_body(**over: object) -> dict[str, object]:
    body: dict[str, object] = {
        "delegator_id": str(principal("a", "reviewer1")),
        "delegate_id": str(principal("a", "supervisor1")),
        "role": "REVIEWER",
        "reason": "annual leave cover for reviewer1",
        "valid_to": (datetime.now(UTC) + timedelta(days=5)).isoformat(),
    }
    body.update(over)
    return body


def review_assigned_case(client, token) -> tuple[str, str]:  # type: ignore[no-untyped-def]
    """A case whose reviewer is a.reviewer1; returns (case_id, analyst subject)."""
    case_id = ingest(client, token)["case_id"]
    r = client.post(
        f"/api/v1/cases/{case_id}/assignment",
        json={"principal_id": str(principal("a", "reviewer1")), "role": "REVIEWER", "reason": "review rota"},
        headers=token(SUP),
    )
    assert r.status_code == 200, r.text
    return case_id, assigned_analyst(client, token, case_id)


# ------------------------------------------------------------------ delegation (FR-003)
@pytest.mark.release_blocker
@pytest.mark.req("FR-003", "SPR-006", "SPR-005", "FR-036")
def test_delegation_is_explicit_bounded_audited_and_scoped(client, token, db_url) -> None:  # type: ignore[no-untyped-def]
    case_id, analyst = review_assigned_case(client, token)
    other_analyst = "a.analyst2@synthetic" if analyst == "a.analyst1@synthetic" else "a.analyst1@synthetic"
    other_id = principal("a", other_analyst.split(".")[1].split("@")[0])
    assert client.get(f"/api/v1/cases/{case_id}", headers=token(other_analyst)).status_code == 404

    body = delegate_body(delegate_id=str(other_id))
    assert client.post("/api/v1/admin/delegations", json=body, headers=token(analyst)).status_code == 403
    no_mfa = client.post("/api/v1/admin/delegations", json=body, headers=token(ADMIN, mfa=False))
    assert no_mfa.status_code == 401 and no_mfa.json()["error"]["code"] == "MFA_REQUIRED"
    created = client.post("/api/v1/admin/delegations", json=body, headers=token(ADMIN))
    assert created.status_code == 201, created.text
    did = created.json()["delegation_id"]

    # The delegate now sees the delegator's review queue - and the read records the delegation.
    assert client.get(f"/api/v1/cases/{case_id}", headers=token(other_analyst)).status_code == 200
    actions = audit_actions(db_url)
    assert "DELEGATION_CREATED" in actions
    with transaction(session_factory(db_url)) as s:
        from finagent.database.models import AuditEvent

        viewed = s.scalars(
            select(AuditEvent)
            .where(AuditEvent.action == "CASE_VIEWED", AuditEvent.actor_principal_id == other_id)
            .order_by(AuditEvent.seq.desc())
        ).first()
        assert viewed is not None and viewed.details["delegation_ids"] == [did]

    r = client.post(f"/api/v1/admin/delegations/{did}/revoke", headers=token(ADMIN))
    assert r.status_code == 200 and r.json()["revoked_at"] is not None
    assert client.get(f"/api/v1/cases/{case_id}", headers=token(other_analyst)).status_code == 404
    assert "DELEGATION_REVOKED" in audit_actions(db_url)


@pytest.mark.req("FR-003", "SPR-006")
def test_delegation_validation_rejects_unsafe_requests(client, token) -> None:  # type: ignore[no-untyped-def]
    h = token(ADMIN)
    far = (datetime.now(UTC) + timedelta(days=400)).isoformat()
    past = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    cases = [
        delegate_body(valid_to=far),  # longer than delegation_max_days
        delegate_body(valid_to=past),
        delegate_body(delegate_id=str(principal("a", "reviewer1"))),  # self
        delegate_body(role="TENANT_ADMIN", delegator_id=str(principal("a", "tenantadmin1"))),
        delegate_body(delegator_id=str(principal("a", "analyst1"))),  # does not hold REVIEWER
        delegate_body(delegate_id=str(principal("b", "supervisor1"))),  # other tenant
        delegate_body(delegate_id=str(principal("a", "tm-connector"))),  # integration identity
    ]
    for body in cases:
        r = client.post("/api/v1/admin/delegations", json=body, headers=h)
        assert r.status_code == 422, (body, r.text)
    r = client.post("/api/v1/admin/delegations", json=delegate_body(role="BREAK_GLASS"), headers=h)
    assert r.status_code == 422
    r = client.post("/api/v1/admin/delegations", json={**delegate_body(), "tenant_id": "x"}, headers=h)
    assert r.status_code == 400  # tenant never comes from the body


@pytest.mark.release_blocker
@pytest.mark.req("FR-003", "SPR-006", "DIR-033")
def test_delegation_stops_at_expiry_and_when_delegator_loses_role(client, token, db_url) -> None:  # type: ignore[no-untyped-def]
    case_id, _ = review_assigned_case(client, token)
    did = client.post("/api/v1/admin/delegations", json=delegate_body(), headers=token(ADMIN)).json()[
        "delegation_id"
    ]
    # supervisor1 already sees the tenant; use the delegated role directly instead:
    with transaction(session_factory(db_url)) as s:
        s.execute(
            update(RoleAssignment)
            .where(RoleAssignment.principal_id == principal("a", "supervisor1"))
            .values(valid_to=RoleAssignment.valid_from)
        )
    sup = token(SUP)
    assert client.get(f"/api/v1/cases/{case_id}", headers=sup).status_code == 200  # via delegation only
    with transaction(session_factory(db_url)) as s:  # delegator loses REVIEWER -> delegation void
        s.execute(
            update(RoleAssignment)
            .where(RoleAssignment.principal_id == principal("a", "reviewer1"))
            .values(valid_to=RoleAssignment.valid_from)
        )
    assert client.get(f"/api/v1/cases/{case_id}", headers=sup).status_code == 403
    with transaction(session_factory(db_url)) as s:  # restore reviewer, then expire the window
        s.execute(
            update(RoleAssignment)
            .where(RoleAssignment.principal_id == principal("a", "reviewer1"))
            .values(valid_to=None)
        )
    assert client.get(f"/api/v1/cases/{case_id}", headers=sup).status_code == 200
    with transaction(session_factory(db_url)) as s:
        s.execute(
            update(Delegation)
            .where(Delegation.id == uuid.UUID(did))
            .values(
                valid_from=datetime.now(UTC) - timedelta(days=2),
                valid_to=datetime.now(UTC) - timedelta(days=1),
            )
        )
    assert client.get(f"/api/v1/cases/{case_id}", headers=sup).status_code == 403


# ------------------------------------------------------------------ MFA step-up (SPR-005)
@pytest.mark.release_blocker
@pytest.mark.req("SPR-005", "FR-001")
def test_privileged_operations_require_mfa_step_up(client, token) -> None:  # type: ignore[no-untyped-def]
    case_id = ingest(client, token)["case_id"]
    body = {"principal_id": str(principal("a", "reviewer1")), "role": "REVIEWER", "reason": "review rota"}
    r = client.post(f"/api/v1/cases/{case_id}/assignment", json=body, headers=token(SUP, mfa=False))
    assert r.status_code == 401 and r.json()["error"]["code"] == "MFA_REQUIRED"
    assert "insufficient_user_authentication" in r.headers["WWW-Authenticate"]
    # Non-privileged reads stay available without step-up.
    assert client.get(f"/api/v1/cases/{case_id}", headers=token(SUP, mfa=False)).status_code == 200
    assert client.get("/api/v1/admin/delegations", headers=token(ADMIN, mfa=False)).status_code == 401
    r = client.post(f"/api/v1/cases/{case_id}/assignment", json=body, headers=token(SUP))
    assert r.status_code == 200


# ------------------------------------------------------------------ break-glass (SPR-008)
@pytest.mark.release_blocker
@pytest.mark.req("SPR-008", "SPR-006")
def test_break_glass_is_disabled_by_default_and_attempt_is_audited(client, token, db_url) -> None:  # type: ignore[no-untyped-def]
    case_id = ingest(client, token)["case_id"]
    r = client.post(
        "/api/v1/admin/break-glass",
        json={"justification": "production incident INC-0001 needs case triage"},
        headers=token(RESPONDER),
    )
    assert r.status_code == 403 and r.json()["error"]["code"] == "FEATURE_DISABLED"
    assert "BREAK_GLASS_DENIED" in audit_actions(db_url)
    assert client.get(f"/api/v1/cases/{case_id}", headers=token(RESPONDER)).status_code == 403


@pytest.mark.release_blocker
@pytest.mark.req("SPR-008", "SPR-005", "FR-036", "SPR-035")
def test_break_glass_grants_audited_read_only_access_that_expires(make_client, token, db_url) -> None:  # type: ignore[no-untyped-def]
    client = make_client(break_glass_enabled=True)
    case_id = ingest(client, token)["case_id"]
    case_b = ingest(client, token, tenant="b")["case_id"]
    ok = {"justification": "production incident INC-0001 needs case triage", "duration_minutes": 30}
    assert (
        client.post("/api/v1/admin/break-glass", json=ok, headers=token(RESPONDER, mfa=False)).status_code
        == 401
    )
    assert (
        client.post("/api/v1/admin/break-glass", json=ok, headers=token("a.analyst1@synthetic")).status_code
        == 403
    )
    short = {"justification": "because"}
    assert client.post("/api/v1/admin/break-glass", json=short, headers=token(RESPONDER)).status_code == 400
    long = {**ok, "duration_minutes": 600}
    assert client.post("/api/v1/admin/break-glass", json=long, headers=token(RESPONDER)).status_code == 422
    r = client.post("/api/v1/admin/break-glass", json=ok, headers=token(RESPONDER))
    assert r.status_code == 201, r.text
    grant_id = r.json()["grant_id"]
    assert client.post("/api/v1/admin/break-glass", json=ok, headers=token(RESPONDER)).status_code == 409

    h = token(RESPONDER)
    assert client.get(f"/api/v1/cases/{case_id}", headers=h).status_code == 200
    assert [c["case_id"] for c in client.get("/api/v1/cases", headers=h).json()["items"]] == [case_id]
    assert client.get(f"/api/v1/cases/{case_b}", headers=h).status_code == 404  # still tenant-bound
    assert client.get(f"/api/v1/cases/{case_id}", headers=token(RESPONDER, mfa=False)).status_code == 403
    # Read-only: no update, assignment, decision or audit-erasure path.
    patch = {"status": "CANCELLED", "reason": "emergency close"}
    assert (
        client.patch(f"/api/v1/cases/{case_id}", json=patch, headers={**h, "If-Match": "2"}).status_code
        == 403
    )
    assign = {"principal_id": str(principal("a", "analyst1")), "role": "ANALYST", "reason": "emergency"}
    assert client.post(f"/api/v1/cases/{case_id}/assignment", json=assign, headers=h).status_code == 403
    actions = audit_actions(db_url)
    assert {"BREAK_GLASS_ACTIVATED", "BREAK_GLASS_CASE_VIEWED", "BREAK_GLASS_CASES_LISTED"} <= set(actions)
    assert scalar_count(db_url, OutboxEvent, event_type="security.break_glass_activated.v1") == 1

    with transaction(session_factory(db_url)) as s:  # auto-expiry
        s.execute(
            update(BreakGlassGrant)
            .where(BreakGlassGrant.id == uuid.UUID(grant_id))
            .values(
                activated_at=datetime.now(UTC) - timedelta(hours=2),
                expires_at=datetime.now(UTC) - timedelta(hours=1),
            )
        )
    assert client.get(f"/api/v1/cases/{case_id}", headers=h).status_code == 403

    review = {"outcome": "JUSTIFIED", "note": "incident INC-0001 confirmed by on-call lead"}
    assert (
        client.post(f"/api/v1/admin/break-glass/{grant_id}/review", json=review, headers=h).status_code == 403
    )
    r = client.post(f"/api/v1/admin/break-glass/{grant_id}/review", json=review, headers=token(ADMIN))
    assert r.status_code == 200 and r.json()["review_outcome"] == "JUSTIFIED"
    again = client.post(f"/api/v1/admin/break-glass/{grant_id}/review", json=review, headers=token(ADMIN))
    assert again.status_code == 409


@pytest.mark.release_blocker
@pytest.mark.req("SPR-008", "SPR-006", "FR-002")
def test_emergency_role_cannot_come_from_database_and_disabling_voids_grants(
    make_client, token, db_url
) -> None:  # type: ignore[no-untyped-def]
    enabled = make_client(break_glass_enabled=True)
    case_id = ingest(enabled, token)["case_id"]
    with transaction(session_factory(db_url)) as s:  # an attacker-inserted row grants nothing
        s.add(
            RoleAssignment(
                tenant_id=sid("tenant:a"),
                principal_id=principal("a", "auditor1"),
                role="EMERGENCY_READ",
                granted_by="x",
            )
        )
    assert enabled.get(f"/api/v1/cases/{case_id}", headers=token("a.auditor1@synthetic")).status_code == 403
    body = {"justification": "production incident INC-0002 needs case triage"}
    assert enabled.post("/api/v1/admin/break-glass", json=body, headers=token(RESPONDER)).status_code == 201
    assert enabled.get(f"/api/v1/cases/{case_id}", headers=token(RESPONDER)).status_code == 200
    disabled = make_client()  # feature switched off -> active grants stop working immediately
    assert disabled.get(f"/api/v1/cases/{case_id}", headers=token(RESPONDER)).status_code == 403


# ------------------------------------------------------------------ quotas (NQ-028)
@pytest.mark.release_blocker
@pytest.mark.req("NQ-028", "SPR-017", "NQ-027")
def test_principal_and_tenant_quotas_return_429_without_cross_tenant_starvation(make_client, token) -> None:  # type: ignore[no-untyped-def]
    client = make_client(
        rate_limit_principal_per_minute=1,
        rate_limit_principal_burst=3,
        rate_limit_tenant_per_minute=1,
        rate_limit_tenant_burst=5,
    )
    sup_a = token(SUP)
    codes = [client.get("/api/v1/cases", headers=sup_a).status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]
    r = client.get("/api/v1/cases", headers=sup_a)
    assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1
    assert r.json()["error"]["code"] == "RATE_LIMITED"
    # Another principal in tenant A still has its own bucket until the TENANT bucket is spent...
    other = token("a.analyst1@synthetic")
    assert [client.get("/api/v1/cases", headers=other).status_code for _ in range(3)] == [200, 200, 429]
    # ...while tenant B is unaffected by tenant A's exhaustion.
    assert client.get("/api/v1/cases", headers=token("b.supervisor1@synthetic")).status_code == 200


# ------------------------------------------------------------------ DB privilege separation
@pytest.mark.release_blocker
@pytest.mark.req("SPR-035", "SPR-036", "FR-036", "DIR-019", "SPR-016")
def test_application_db_role_cannot_alter_audit_or_schema(client, token, app_db_url) -> None:  # type: ignore[no-untyped-def]
    ingest(client, token)  # the app role CAN insert audit rows through normal flows
    attacks = (
        "UPDATE audit_events SET outcome = 'TAMPERED'",
        "DELETE FROM audit_events",
        "TRUNCATE audit_events",
        "DELETE FROM cases",
        "CREATE TABLE evil (id int)",
        "DROP TABLE audit_events",
        "ALTER TABLE cases DISABLE TRIGGER ALL",
        "UPDATE alembic_version SET version_num = '0000'",
    )
    for stmt in attacks:
        with pytest.raises(DBAPIError) as err, transaction(session_factory(app_db_url)) as s:
            s.execute(text(stmt))
        assert "permission denied" in str(err.value) or "must be owner" in str(err.value), stmt
