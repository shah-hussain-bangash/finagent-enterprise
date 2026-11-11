"""AML graph branches: blocked evidence, escalation, uncertain write + reconciliation, reviewer
REQUEST_INFORMATION / REJECT effects, crash-replay of the draft notification (P9/P10).

FR-005, FR-015, FR-028, FR-029, FR-031..035, AER-021, AER-023, AER-037, NQ-019/020, MST:REC-001..003.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select, update

from finagent.agents.providers import MockModelProvider
from finagent.database.models import ExternalOperation, Lease, Report, WorkflowRun
from finagent.database.session import session_factory, transaction
from finagent.integrations.notifications.port import LocalNotificationAdapter, Notification
from finagent.orchestration.faults import FaultInjector, SimulatedCrash
from tests.agent_helpers import approve_policies
from tests.helpers import CONNECTOR, alert_envelope, assigned_analyst, subject

pytestmark = pytest.mark.integration
REVIEWER, OPERATOR, TADMIN = (
    subject("a", "reviewer1"),
    subject("a", "operator1"),
    subject("a", "tenantadmin1"),
)


def ok(r: Any, code: int = 200) -> dict[str, Any]:
    assert r.status_code == code, r.text
    out: dict[str, Any] = r.json()
    return out


def status_of(db_url: str, run_id: str) -> tuple[str, str]:
    with transaction(session_factory(db_url)) as s:
        run = s.get(WorkflowRun, uuid.UUID(run_id))
        assert run is not None
        return run.status, run.status_reason


def drain(worker: Any, db_url: str, run_id: str, until: set[str], ticks: int = 15) -> str:
    for _ in range(ticks):
        worker.tick()
        st, _ = status_of(db_url, run_id)
        if st in until:
            return st
    raise AssertionError(f"run stuck in {status_of(db_url, run_id)}")


class Env:
    def __init__(
        self, make_client: Any, make_worker: Any, token: Any, bank: Any, db_url: str, **over: Any
    ) -> None:
        self.over = {"bank_base_url": bank.url, "lease_ttl_seconds": 5, **over}
        self.client, self.token, self.bank, self.db = make_client(**self.over), token, bank, db_url
        self.make_worker = make_worker
        approve_policies(self.client, token)

    def case(self, subject_ref: str = "customer-demo-001") -> tuple[str, str]:
        env = alert_envelope(f"AML-WF-{uuid.uuid4().hex[:8]}", subject_refs=[subject_ref])
        r = self.client.post(
            "/api/v1/alerts",
            json=env,
            headers={**self.token(CONNECTOR["a"]), "Idempotency-Key": f"k-{uuid.uuid4()}"},
        )
        case_id = ok(r, 201)["case_id"]
        return case_id, assigned_analyst(self.client, self.token, case_id)

    def start(self, case_id: str, analyst: str) -> str:
        r = self.client.post(
            "/api/v1/workflows",
            json={"case_id": case_id},
            headers={**self.token(analyst), "Idempotency-Key": f"wf-{uuid.uuid4()}"},
        )
        return str(ok(r, 202)["run_id"])

    def submit(self, case_id: str, analyst: str) -> dict[str, Any]:
        rep = ok(self.client.get(f"/api/v1/cases/{case_id}/reports", headers=self.token(analyst)))
        return ok(
            self.client.post(
                f"/api/v1/cases/{case_id}/reports/submit",
                json={"version_no": rep["report"]["current_version_no"]},
                headers={**self.token(analyst), "Idempotency-Key": f"s-{uuid.uuid4()}"},
            ),
            201,
        )

    def decide(self, sub: dict[str, Any], decision: str) -> Any:
        return self.client.post(
            f"/api/v1/approvals/{sub['approval_request_id']}/decision",
            json={
                "decision": decision,
                "rationale": "synthetic reviewer rationale for the test",
                "report_version_id": sub["report_version_id"],
                "snapshot_id": sub["snapshot_id"],
            },
            headers={**self.token(REVIEWER), "Idempotency-Key": f"d-{uuid.uuid4()}"},
        )


@pytest.fixture
def env(make_client, make_worker, token, bank, db_url):  # type: ignore[no-untyped-def]
    return Env(make_client, make_worker, token, bank, db_url)


@pytest.mark.req("FR-015", "FR-005", "AER-010", "FR-031", "DIR-009")
@pytest.mark.release_blocker
def test_blocking_gap_waits_for_evidence_then_continues(env: Env) -> None:
    case_id, analyst = env.case()
    env.bank.fault("/v1/customers/customer-demo-001", "status", "403", times=1)  # profile denied once
    run_id = env.start(case_id, analyst)
    worker = env.make_worker(**env.over)
    assert drain(worker, env.db, run_id, {"WAITING_EVIDENCE"}) == "WAITING_EVIDENCE"
    case = ok(env.client.get(f"/api/v1/cases/{case_id}", headers=env.token(analyst)))
    assert case["status"] == "BLOCKED" and "SOURCE_MISSING:CUSTOMER_PROFILE" in case["blocked_reason"]
    assert case["blocked_owner_role"] == "ANALYST"
    with transaction(session_factory(env.db)) as s:
        assert s.scalar(select(func.count()).select_from(Report)) == 0  # nothing drafted on missing evidence
    wrong = env.client.post(
        f"/api/v1/workflows/{run_id}/resume",
        json={"action": "REANALYZE", "reason": "try again"},
        headers=env.token(analyst),
    )
    assert wrong.status_code == 409
    other = "a.analyst2@synthetic" if analyst == "a.analyst1@synthetic" else "a.analyst1@synthetic"
    assert (
        env.client.post(
            f"/api/v1/workflows/{run_id}/resume",
            json={"action": "EVIDENCE_SUPPLIED", "reason": "x" * 6},
            headers=env.token(other),
        ).status_code
        == 404
    )
    ok(
        env.client.post(
            f"/api/v1/workflows/{run_id}/resume",
            json={"action": "EVIDENCE_SUPPLIED", "reason": "source access restored"},
            headers=env.token(analyst),
        ),
        202,
    )
    assert (
        ok(env.client.get(f"/api/v1/cases/{case_id}", headers=env.token(analyst)))["status"]
        == "INVESTIGATING"
    )
    assert drain(worker, env.db, run_id, {"WAITING_REVIEW"}) == "WAITING_REVIEW"


@pytest.mark.req("AER-037", "SPR-031", "BR-007", "AER-018")
@pytest.mark.release_blocker
def test_authority_boundary_in_agent_output_escalates_the_run(env: Env) -> None:
    case_id, analyst = env.case()
    run_id = env.start(case_id, analyst)
    worker = env.make_worker(model_provider=MockModelProvider(["ok", "guilt_claim"]), **env.over)
    assert drain(worker, env.db, run_id, {"HUMAN_REVIEW"}) == "HUMAN_REVIEW"
    _, reason = status_of(env.db, run_id)
    assert "authority boundary" in reason and "AG-AML" in reason
    with transaction(session_factory(env.db)) as s:
        assert s.scalar(select(func.count()).select_from(Report)) == 0  # no draft from rejected output


@pytest.mark.req("FR-029", "FR-033", "MST:REC-002", "MST:REC-003", "NQ-019", "AER-023")
@pytest.mark.release_blocker
def test_uncertain_status_write_is_reconciled_never_blindly_retried(
    make_client,
    make_worker,
    token,
    bank,
    db_url,  # type: ignore[no-untyped-def]
) -> None:
    env = Env(
        make_client,
        make_worker,
        token,
        bank,
        db_url,
        case_status_write_enabled=True,
        connector_timeout_seconds=1,
    )
    ok(
        env.client.put(
            "/api/v1/admin/feature-flags/case_status_write",
            headers=token(TADMIN),
            json={"enabled": True, "reason": "synthetic test enables the single write"},
        )
    )
    case_id, analyst = env.case()
    run_id = env.start(case_id, analyst)
    worker = env.make_worker(**env.over)
    assert drain(worker, db_url, run_id, {"WAITING_REVIEW"}) == "WAITING_REVIEW"
    sub = env.submit(case_id, analyst)
    ok(env.decide(sub, "APPROVE"))
    bank.fault("/v1/cases/", "timeout", "2", times=1)  # the write lands late: client sees a timeout
    assert drain(worker, db_url, run_id, {"OUTCOME_UNCERTAIN"}) == "OUTCOME_UNCERTAIN"
    assert ok(env.client.get(f"/api/v1/cases/{case_id}", headers=token(analyst)))["status"] == "FINALIZING"
    with transaction(session_factory(db_url)) as s:
        op = s.scalar(
            select(ExternalOperation).where(ExternalOperation.operation_type == "CASE_STATUS_UPDATE")
        )
        assert op is not None and op.status == "OUTCOME_UNCERTAIN"
        op_id = str(op.id)
    for _ in range(3):
        worker.tick()  # never auto-retried while uncertain
    assert status_of(db_url, run_id)[0] == "OUTCOME_UNCERTAIN" and bank.calls("/status") == 1
    assert (
        env.client.post(
            f"/api/v1/workflows/{run_id}/cancel", json={"reason": "give up"}, headers=token(analyst)
        ).status_code
        == 409
    )
    ok(
        env.client.post(
            f"/api/v1/operations/{op_id}/reconcile",
            headers=token(OPERATOR),
            json={"resolution": "APPLIED", "reason": "confirmed in the case system by phone"},
        )
    )
    assert status_of(db_url, run_id)[0] == "HUMAN_REVIEW"
    ok(
        env.client.post(
            f"/api/v1/workflows/{run_id}/resume",
            json={"action": "RETRY", "reason": "reconciled as applied"},
            headers=token(OPERATOR),
        ),
        202,
    )
    assert drain(worker, db_url, run_id, {"SUCCEEDED"}) == "SUCCEEDED"
    assert ok(env.client.get(f"/api/v1/cases/{case_id}", headers=token(analyst)))["status"] == "COMPLETED"
    assert bank.calls("/status") == 1  # reconciliation, not a second write


@pytest.mark.req("FR-028", "FR-015", "FR-031")
def test_request_information_blocks_case_and_reopens_evidence(env: Env) -> None:
    case_id, analyst = env.case()
    run_id = env.start(case_id, analyst)
    worker = env.make_worker(**env.over)
    drain(worker, env.db, run_id, {"WAITING_REVIEW"})
    sub = env.submit(case_id, analyst)
    out = ok(env.decide(sub, "REQUEST_INFORMATION"))
    assert out["status"] == "INFO_REQUESTED" and out["decision"]["effect"] == "BLOCKED"
    case = ok(env.client.get(f"/api/v1/cases/{case_id}", headers=env.token(analyst)))
    assert case["status"] == "BLOCKED" and case["blocked_owner_role"] == "ANALYST"
    assert drain(worker, env.db, run_id, {"WAITING_EVIDENCE"}) == "WAITING_EVIDENCE"
    ok(
        env.client.post(
            f"/api/v1/workflows/{run_id}/resume",
            json={"action": "EVIDENCE_SUPPLIED", "reason": "added"},
            headers=env.token(analyst),
        ),
        202,
    )
    assert drain(worker, env.db, run_id, {"WAITING_REVIEW"}) == "WAITING_REVIEW"
    reps = ok(env.client.get(f"/api/v1/cases/{case_id}/reports", headers=env.token(analyst)))
    assert reps["report"]["current_version_no"] == 2 and reps["report"]["status"] == "DRAFT"


@pytest.mark.req("FR-028", "MST:ARCH-003")
def test_reject_effect_follows_configuration(make_client, make_worker, token, bank, db_url) -> None:  # type: ignore[no-untyped-def]
    default = Env(make_client, make_worker, token, bank, db_url)
    case_id, analyst = default.case()
    run_id = default.start(case_id, analyst)
    drain(default.make_worker(**default.over), db_url, run_id, {"WAITING_REVIEW"})
    out = ok(default.decide(default.submit(case_id, analyst), "REJECT"))
    assert out["decision"]["effect"] == "REWORK"  # DEC-11 conservative default
    assert ok(default.client.get(f"/api/v1/cases/{case_id}", headers=token(analyst)))["status"] == "REWORK"
    close = Env(make_client, make_worker, token, bank, db_url, approval_reject_effect="CLOSE")
    case2, analyst2 = close.case()
    run2 = close.start(case2, analyst2)
    worker = close.make_worker(**close.over)
    drain(worker, db_url, run2, {"WAITING_REVIEW"})
    out = ok(close.decide(close.submit(case2, analyst2), "REJECT"))
    assert out["decision"]["effect"] == "CANCELLED"
    assert drain(worker, db_url, run2, {"SUCCEEDED"}) == "SUCCEEDED"
    assert ok(close.client.get(f"/api/v1/cases/{case2}", headers=token(analyst2)))["status"] == "CANCELLED"


class CountingNotifier(LocalNotificationAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.sends = 0

    def send(self, notification: Notification) -> None:
        self.sends += 1
        super().send(notification)


@pytest.mark.req("FR-034", "AER-021", "NQ-019", "NQ-020", "MST:REC-001")
@pytest.mark.release_blocker
def test_killed_worker_mid_graph_resumes_without_repeating_effects(env: Env) -> None:
    case_id, analyst = env.case()
    run_id = env.start(case_id, analyst)
    notifier = CountingNotifier()
    crashing = env.make_worker(
        notifier=notifier,
        worker_id="A",
        faults=FaultInjector.parse("after_effect:notify_draft_ready:crash"),
        **env.over,
    )
    with pytest.raises(SimulatedCrash):
        for _ in range(5):
            crashing.tick()
    with transaction(session_factory(env.db)) as s:
        s.execute(
            update(Lease)
            .where(Lease.resource_id == uuid.UUID(run_id))
            .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
    survivor = env.make_worker(notifier=notifier, worker_id="B", **env.over)
    assert drain(survivor, env.db, run_id, {"WAITING_REVIEW"}) == "WAITING_REVIEW"
    assert notifier.sends == 1 and len(notifier.delivered) == 1  # probe found it delivered: no resend
    outputs = ok(env.client.get(f"/api/v1/cases/{case_id}/agent-outputs", headers=env.token(analyst)))[
        "items"
    ]
    assert len(outputs) == 5  # committed capability steps were not re-run after the takeover
    with transaction(session_factory(env.db)) as s:
        assert s.scalar(select(func.count()).select_from(Report)) == 1
