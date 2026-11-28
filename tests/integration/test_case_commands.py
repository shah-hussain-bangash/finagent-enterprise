"""Case commands: guarded PATCH, reassignment and escalation (FR-004, FR-009, FR-011, UI-002)."""

from __future__ import annotations

import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from finagent.database.models import Case, CaseAssignment, Escalation, OutboxEvent, RoleAssignment
from finagent.database.seeds import sid
from finagent.database.session import session_factory, transaction
from finagent.integrations.notifications.port import LocalNotificationAdapter, Notification, NotificationError
from finagent.services.case_service import escalation
from tests.helpers import assigned_analyst, audit_actions, ingest, principal, scalar_count, subject

pytestmark = pytest.mark.integration
SUP = subject("a", "supervisor1")


def force_status(db_url: str, case_id: str, status: str) -> None:
    """Test arrangement only: put a case into a status reached by a later phase's service."""
    with transaction(session_factory(db_url)) as s:
        s.execute(update(Case).where(Case.id == uuid.UUID(case_id)).values(status=status))


def version(client, token, case_id: str) -> int:  # type: ignore[no-untyped-def]
    r = client.get(f"/api/v1/cases/{case_id}", headers=token(SUP))
    assert r.headers["ETag"] == f'"{r.json()["record_version"]}"'
    return int(r.json()["record_version"])


@pytest.fixture
def investigating(client, token, db_url):  # type: ignore[no-untyped-def]
    case_id = ingest(client, token)["case_id"]
    force_status(db_url, case_id, "INVESTIGATING")
    return case_id, assigned_analyst(client, token, case_id)


def test_assignee_search_is_case_scoped_and_requires_supervisor_mfa(client, token, db_url) -> None:  # type: ignore[no-untyped-def]
    case_id = ingest(client, token)["case_id"]
    assigned = assigned_analyst(client, token, case_id)
    url = f"/api/v1/cases/{case_id}/eligible-assignees"
    payload = {"role": "ANALYST", "q": "analyst"}
    response = client.post(url, json=payload, headers=token(SUP))
    assert response.status_code == 200, response.text
    results = response.json()["items"]
    assigned_id = str(principal("a", assigned.split(".")[1].split("@")[0]))
    assert results and all(person["id"] != assigned_id for person in results)
    assert all("analyst" in person["display_name"].lower() for person in results)
    assert client.post(url, json=payload, headers=token(SUP, mfa=False)).status_code == 401
    assert client.post(url, json=payload, headers=token(assigned)).status_code == 403
    assert client.post(url, json=payload, headers=token(subject("b", "supervisor1"))).status_code == 404
    assert "CASE_ASSIGNEE_SEARCHED" in audit_actions(db_url, case_id=case_id)


@pytest.mark.req("FR-004", "UI-002", "FR-011", "MST:API-003")
def test_block_and_resume_with_if_match_reason_and_owner(client, token, db_url, investigating) -> None:  # type: ignore[no-untyped-def]
    case_id, analyst = investigating
    v = version(client, token, case_id)
    r = client.patch(
        f"/api/v1/cases/{case_id}",
        json={"status": "BLOCKED", "reason": "KYC document missing", "blocked_owner_role": "ANALYST"},
        headers={**token(analyst), "If-Match": f'"{v}"'},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "BLOCKED" and r.json()["record_version"] == v + 1
    assert r.headers["ETag"] == f'"{v + 1}"'
    detail = client.get(f"/api/v1/cases/{case_id}", headers=token(SUP)).json()
    assert detail["blocked_reason"] == "KYC document missing" and detail["blocked_owner_role"] == "ANALYST"
    assert detail["due_at"] is not None  # FR-011: configured due time is shown
    r = client.patch(
        f"/api/v1/cases/{case_id}",
        json={"status": "INVESTIGATING", "reason": "document received", "record_version": v + 1},
        headers=token(analyst),
    )
    assert r.status_code == 200 and r.json()["blocked_reason"] is None
    assert scalar_count(db_url, OutboxEvent, event_type="case.blocked.v1") == 1
    assert audit_actions(db_url, case_id=case_id).count("CASE_STATUS_CHANGED") == 2


@pytest.mark.release_blocker
@pytest.mark.req("FR-004", "UI-002", "NQ-020")
def test_stale_version_is_rejected_and_nothing_is_overwritten(client, token, db_url, investigating) -> None:  # type: ignore[no-untyped-def]
    case_id, analyst = investigating
    v = version(client, token, case_id)
    ok = client.patch(
        f"/api/v1/cases/{case_id}",
        json={"status": "BLOCKED", "reason": "first writer", "blocked_owner_role": "ANALYST"},
        headers={**token(analyst), "If-Match": str(v)},
    )
    assert ok.status_code == 200
    stale = client.patch(
        f"/api/v1/cases/{case_id}",
        json={"status": "BLOCKED", "reason": "second writer", "blocked_owner_role": "AML_SUPERVISOR"},
        headers={**token(analyst), "If-Match": str(v)},
    )
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "VERSION_CONFLICT"
    assert stale.json()["error"]["details"]["current_version"] == v + 1
    detail = client.get(f"/api/v1/cases/{case_id}", headers=token(SUP)).json()
    assert detail["blocked_reason"] == "first writer"  # accepted work was not overwritten


@pytest.mark.req("FR-004", "NQ-020", "FR-034")
def test_concurrent_patches_on_same_version_exactly_one_wins(client, token, investigating) -> None:  # type: ignore[no-untyped-def]
    case_id, analyst = investigating
    v = version(client, token, case_id)

    def attempt(i: int) -> int:
        return client.patch(
            f"/api/v1/cases/{case_id}",
            json={"status": "BLOCKED", "reason": f"writer {i}", "blocked_owner_role": "ANALYST"},
            headers={**token(analyst), "If-Match": str(v)},
        ).status_code

    with ThreadPoolExecutor(max_workers=6) as pool:
        codes = sorted(pool.map(attempt, range(6)))
    assert codes.count(200) == 1 and codes.count(409) == 5


@pytest.mark.release_blocker
@pytest.mark.req("FR-004", "NQ-020", "FR-034")
def test_patch_waits_for_concurrent_writer_and_then_conflicts(client, token, db_url, investigating) -> None:  # type: ignore[no-untyped-def]
    """Deterministic race: another transaction holds the case row while a PATCH based on the same
    version arrives. The PATCH must wait for the lock and then see the newer version (409), never
    overwrite the other writer's committed change."""
    case_id, analyst = investigating
    v = version(client, token, case_id)
    other = session_factory(db_url)()  # autobegins; holds the row lock until commit
    try:
        row = other.scalars(select(Case).where(Case.id == uuid.UUID(case_id)).with_for_update()).one()
        with ThreadPoolExecutor(max_workers=1) as pool:
            fut = pool.submit(
                client.patch,
                f"/api/v1/cases/{case_id}",
                json={"status": "BLOCKED", "reason": "late writer", "blocked_owner_role": "ANALYST"},
                headers={**token(analyst), "If-Match": str(v)},
            )
            time.sleep(0.7)  # the PATCH is now waiting on the row
            row.record_version += 1
            row.blocked_reason = "concurrent writer"
            other.commit()
            r = fut.result(timeout=30)
    finally:
        other.close()
    assert r.status_code == 409 and r.json()["error"]["code"] == "VERSION_CONFLICT", r.text
    with transaction(session_factory(db_url)) as s:
        assert s.get(Case, uuid.UUID(case_id)).blocked_reason == "concurrent writer"  # type: ignore[union-attr]


@pytest.mark.req("FR-004", "DIR-020")
def test_missing_precondition_illegal_target_and_review_statuses(client, token, investigating) -> None:  # type: ignore[no-untyped-def]
    case_id, analyst = investigating
    h = token(analyst)
    body = {"status": "BLOCKED", "reason": "no version", "blocked_owner_role": "ANALYST"}
    r = client.patch(f"/api/v1/cases/{case_id}", json=body, headers=h)
    assert r.status_code == 400 and r.json()["error"]["code"] == "PRECONDITION_REQUIRED"
    v = version(client, token, case_id)
    # Review / approval / completion statuses are not reachable through PATCH (schema rejects).
    for target in ("AWAITING_REVIEW", "APPROVED", "COMPLETED", "FINALIZING"):
        r = client.patch(
            f"/api/v1/cases/{case_id}",
            json={"status": target, "reason": "try"},
            headers={**h, "If-Match": str(v)},
        )
        assert r.status_code == 400, target
    # Legal enum but illegal from INVESTIGATING -> 409 with the allowed targets.
    r = client.patch(
        f"/api/v1/cases/{case_id}",
        json={"status": "INVESTIGATING", "reason": "resume"},
        headers={**h, "If-Match": str(v)},
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "CASE_INVALID_STATE"
    r = client.patch(
        f"/api/v1/cases/{case_id}",
        json={"status": "BLOCKED", "reason": "x" * 5},
        headers={**h, "If-Match": str(v)},
    )
    assert r.status_code == 400  # BLOCKED needs an owner
    r = client.patch(
        f"/api/v1/cases/{case_id}", json={"status": "BLOCKED"}, headers={**h, "If-Match": "garbage"}
    )
    assert r.status_code == 400


@pytest.mark.req("FR-004", "FR-002", "FR-009")
def test_priority_and_cancellation_are_supervisor_only(client, token, db_url, investigating) -> None:  # type: ignore[no-untyped-def]
    case_id, analyst = investigating
    v = version(client, token, case_id)
    r = client.patch(
        f"/api/v1/cases/{case_id}",
        json={"priority": "URGENT", "reason": "self-escalate"},
        headers={**token(analyst), "If-Match": str(v)},
    )
    assert r.status_code == 403
    r = client.patch(
        f"/api/v1/cases/{case_id}",
        json={"priority": "URGENT", "reason": "regulator deadline"},
        headers={**token(SUP), "If-Match": str(v)},
    )
    assert r.status_code == 200 and r.json()["priority"] == "URGENT"
    r = client.patch(
        f"/api/v1/cases/{case_id}",
        json={"status": "CANCELLED", "reason": "duplicate of another case"},
        headers={**token(SUP), "If-Match": str(v + 1)},
    )
    assert r.status_code == 200 and r.json()["status"] == "CANCELLED"
    r = client.patch(
        f"/api/v1/cases/{case_id}",
        json={"priority": "LOW", "reason": "after close"},
        headers={**token(SUP), "If-Match": str(v + 2)},
    )
    assert r.status_code == 409  # terminal cases are immutable
    assert "CASE_PRIORITY_CHANGED" in audit_actions(db_url, case_id=case_id)


@pytest.mark.req("FR-004", "SPR-009", "NFR-002")
def test_patch_on_other_tenant_or_unassigned_case_is_concealed(client, token, investigating) -> None:  # type: ignore[no-untyped-def]
    case_id, analyst = investigating
    other = "a.analyst2@synthetic" if analyst == "a.analyst1@synthetic" else "a.analyst1@synthetic"
    body = {"status": "BLOCKED", "reason": "attack", "blocked_owner_role": "ANALYST"}
    for subj in (other, "b.analyst1@synthetic", "b.supervisor1@synthetic"):
        r = client.patch(f"/api/v1/cases/{case_id}", json=body, headers={**token(subj), "If-Match": "1"})
        absent = client.patch(
            f"/api/v1/cases/{uuid.uuid4()}", json=body, headers={**token(subj), "If-Match": "1"}
        )
        assert r.status_code == absent.status_code == 404, subj


# ------------------------------------------------------------------ reassignment (FR-009)
@pytest.mark.req("FR-009", "FR-036", "SPR-005")
def test_supervisor_reassigns_with_reason_and_history_is_kept(client, token, db_url) -> None:  # type: ignore[no-untyped-def]
    case_id = ingest(client, token)["case_id"]
    old = assigned_analyst(client, token, case_id)
    new_key = "analyst2" if old == "a.analyst1@synthetic" else "analyst1"
    r = client.post(
        f"/api/v1/cases/{case_id}/assignment",
        json={
            "principal_id": str(principal("a", new_key)),
            "role": "ANALYST",
            "reason": "workload balancing",
        },
        headers=token(SUP),
    )
    assert r.status_code == 200, r.text
    assert client.get(f"/api/v1/cases/{case_id}", headers=token(old)).status_code == 404
    assert client.get(f"/api/v1/cases/{case_id}", headers=token(subject("a", new_key))).status_code == 200
    with transaction(session_factory(db_url)) as s:
        rows = s.scalars(
            select(CaseAssignment)
            .where(CaseAssignment.case_id == uuid.UUID(case_id))
            .order_by(CaseAssignment.assigned_at)
        ).all()
        assert [(a.active, a.reason) for a in rows] == [
            (False, "auto: least-loaded analyst (DEC-13)"),
            (True, "workload balancing"),
        ]
        assert rows[0].ended_at is not None
    assert "CASE_REASSIGNED" in audit_actions(db_url, case_id=case_id)
    r = client.post(
        f"/api/v1/cases/{case_id}/assignment",
        json={"principal_id": str(principal("a", "reviewer1")), "role": "REVIEWER", "reason": "review rota"},
        headers=token(SUP),
    )
    assert r.status_code == 200
    assert client.get(f"/api/v1/cases/{case_id}", headers=token("a.reviewer1@synthetic")).status_code == 200


@pytest.mark.release_blocker
@pytest.mark.req("FR-009", "FR-003", "SPR-009", "SPR-010")
def test_reassignment_rules_deny_analysts_cross_tenant_and_sod_conflicts(client, token, db_url) -> None:  # type: ignore[no-untyped-def]
    case_id = ingest(client, token)["case_id"]
    analyst = assigned_analyst(client, token, case_id)
    analyst_id = principal("a", analyst.split(".")[1].split("@")[0])
    body = {
        "principal_id": str(principal("a", "reviewer1")),
        "role": "REVIEWER",
        "reason": "self-pick reviewer",
    }
    assert (
        client.post(f"/api/v1/cases/{case_id}/assignment", json=body, headers=token(analyst)).status_code
        == 403
    )
    for bad in (principal("b", "analyst1"), principal("a", "auditor1"), uuid.uuid4()):
        r = client.post(
            f"/api/v1/cases/{case_id}/assignment",
            json={"principal_id": str(bad), "role": "ANALYST", "reason": "invalid assignee"},
            headers=token(SUP),
        )
        assert r.status_code == 422 and str(bad) not in r.text
    # The case's analyst can never also be its reviewer (separation of duties) - even when that
    # person legitimately holds both roles.
    with transaction(session_factory(db_url)) as s:
        s.add(
            RoleAssignment(
                tenant_id=sid("tenant:a"), principal_id=analyst_id, role="REVIEWER", granted_by="t"
            )
        )
    r = client.post(
        f"/api/v1/cases/{case_id}/assignment",
        json={"principal_id": str(analyst_id), "role": "REVIEWER", "reason": "conflict of duties"},
        headers=token(SUP),
    )
    assert r.status_code == 403 and "Separation of duties" in r.json()["error"]["message"]
    r = client.post(
        f"/api/v1/cases/{case_id}/assignment",
        json={"principal_id": str(principal("a", "reviewer1")), "role": "ANALYST", "reason": "wrong role"},
        headers=token(SUP),
    )
    assert r.status_code == 422
    assert (
        client.post(
            f"/api/v1/cases/{case_id}/assignment", json=body, headers=token("b.supervisor1@synthetic")
        ).status_code
        == 404
    )


# ------------------------------------------------------------------ escalation (FR-011)
@pytest.mark.req("FR-011", "DIR-020")
def test_overdue_case_escalates_once_per_rule_and_episode(client, token, db_url) -> None:  # type: ignore[no-untyped-def]
    case_id = ingest(client, token)["case_id"]
    factory = session_factory(db_url)
    later = datetime.now(UTC) + timedelta(days=2)  # beyond "assigned-not-started" (24 h, PROPOSED)
    with transaction(factory) as s:
        assert escalation.evaluate_due(s, now=datetime.now(UTC)) == []  # not yet due
    with transaction(factory) as s:
        first = escalation.evaluate_due(s, now=later)
    with transaction(factory) as s:
        again = escalation.evaluate_due(s, now=later + timedelta(hours=1))
    assert len(first) == 1 and again == []
    assert scalar_count(db_url, Escalation, case_id=uuid.UUID(case_id)) == 1
    assert scalar_count(db_url, OutboxEvent, event_type="case.escalated.v1") == 1
    queue = client.get("/api/v1/escalations", headers=token(SUP)).json()["items"]
    assert [(e["case_id"], e["rule_key"], e["notification_status"]) for e in queue] == [
        (case_id, "assigned-not-started", "PENDING")
    ]
    assert client.get("/api/v1/escalations", headers=token("b.supervisor1@synthetic")).json()["items"] == []
    assert client.get("/api/v1/escalations", headers=token("a.analyst1@synthetic")).status_code == 403
    # Leaving the status ends the episode and resolves the escalation.
    force_status(db_url, case_id, "INVESTIGATING")
    with transaction(factory) as s:
        s.execute(
            update(Case).where(Case.id == uuid.UUID(case_id)).values(status_changed_at=datetime.now(UTC))
        )
        escalation.evaluate_due(s, now=later)
    assert client.get("/api/v1/escalations", headers=token(SUP)).json()["items"] == []


@pytest.mark.req("FR-011", "DIR-028")
def test_concurrent_evaluators_create_one_escalation(client, token, db_url) -> None:  # type: ignore[no-untyped-def]
    ingest(client, token)
    factory = session_factory(db_url)
    later = datetime.now(UTC) + timedelta(days=2)

    def run(_: int) -> int:
        with transaction(factory) as s:
            return len(escalation.evaluate_due(s, now=later))

    with ThreadPoolExecutor(max_workers=4) as pool:
        created = sum(pool.map(run, range(4)))
    assert created == 1 and scalar_count(db_url, Escalation) == 1


class FlakyNotifier:
    def __init__(self, failures: int) -> None:
        self.failures, self.sink = failures, LocalNotificationAdapter()
        self.calls = 0

    def send(self, notification: Notification) -> None:
        self.calls += 1
        if self.calls <= self.failures:
            raise NotificationError("PROVIDER_TIMEOUT")
        self.sink.send(notification)


@pytest.mark.req("FR-011", "NQ-020")
def test_notification_retries_never_duplicate_escalation_and_failures_stay_visible(
    client, token, db_url
) -> None:  # type: ignore[no-untyped-def]
    ingest(client, token)
    factory = session_factory(db_url)
    with transaction(factory) as s:
        escalation.evaluate_due(s, now=datetime.now(UTC) + timedelta(days=2))
    notifier = FlakyNotifier(failures=2)
    for _ in range(2):
        with transaction(factory) as s:
            escalation.deliver_notifications(s, notifier)
    item = client.get("/api/v1/escalations", headers=token(SUP)).json()["items"][0]
    assert item["notification_status"] == "FAILED" and item["notification_attempts"] == 2
    assert item["last_notification_error"] == "PROVIDER_TIMEOUT"  # FR-011: delivery failure visible
    with transaction(factory) as s:
        assert escalation.deliver_notifications(s, notifier) == {"sent": 1, "failed": 0, "gave_up": 0}
    with transaction(factory) as s:
        assert escalation.deliver_notifications(s, notifier) == {"sent": 0, "failed": 0, "gave_up": 0}
    assert scalar_count(db_url, Escalation) == 1 and len(notifier.sink.delivered) == 1
    item = client.get("/api/v1/escalations", headers=token(SUP)).json()["items"][0]
    assert item["notification_status"] == "SENT"
    sent = next(iter(notifier.sink.delivered.values()))
    assert sent.resource_ref == item["case_id"] and sent.template == "case.escalated"  # refs only


@pytest.mark.req("FR-011")
def test_permanent_notification_failure_gives_up_and_is_audited(client, token, db_url) -> None:  # type: ignore[no-untyped-def]
    ingest(client, token)
    factory = session_factory(db_url)
    with transaction(factory) as s:
        escalation.evaluate_due(s, now=datetime.now(UTC) + timedelta(days=2))
    notifier = FlakyNotifier(failures=100)
    for _ in range(escalation.MAX_NOTIFICATION_ATTEMPTS + 2):
        with transaction(factory) as s:
            escalation.deliver_notifications(s, notifier)
    assert notifier.calls == escalation.MAX_NOTIFICATION_ATTEMPTS  # bounded, then stops
    item = client.get("/api/v1/escalations", headers=token(SUP)).json()["items"][0]
    assert item["notification_status"] == "GAVE_UP"
    assert "NOTIFICATION_DELIVERY_FAILED" in audit_actions(db_url)
