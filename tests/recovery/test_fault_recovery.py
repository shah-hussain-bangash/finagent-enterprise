"""Recovery / fault-injection suite for the workflow foundation (prompt §30; slice 2).

FR-031, FR-033, FR-034, FR-035, DIR-020, DIR-029, DIR-031, NQ-019, NQ-020, NQ-021, AER-021,
MST:REC-001..003.
"""

from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select, update

from finagent.core.crypto import DecryptionError, Encryptor
from finagent.database.models import (
    Checkpoint,
    ExternalOperation,
    InboxEvent,
    Lease,
    OutboxEvent,
    RoleAssignment,
    WorkflowRun,
    WorkflowRunHistory,
)
from finagent.database.session import session_factory, transaction
from finagent.integrations.notifications.port import DeliveryStatus, LocalNotificationAdapter, Notification
from finagent.orchestration.checkpointer import CheckpointScopeError, TenantCheckpointer
from finagent.orchestration.faults import FaultInjector, SimulatedCrash
from finagent.orchestration.runtime import RunContext
from finagent.services.case_service.transitions import Actor
from finagent.services.workflow_service.runs import resume_run
from finagent.worker import leases
from finagent.worker.leases import RUN_RESOURCE, StaleLeaseError
from finagent.worker.runner import RunOutcome, execute_run
from tests.helpers import (
    assigned_analyst,
    audit_actions,
    ingest,
    principal,
    run_status,
    start_run,
    steps,
    subject,
)

pytestmark = [pytest.mark.integration]


@pytest.fixture
def run(client, token):  # type: ignore[no-untyped-def]
    case_id = ingest(client, token)["case_id"]
    analyst = assigned_analyst(client, token, case_id)
    r = start_run(client, token, case_id, analyst)
    assert r.status_code == 202
    return {"case_id": case_id, "analyst": analyst, "run_id": r.json()["run_id"]}


def expire_lease(db_url: str, run_id: str) -> None:
    with transaction(session_factory(db_url)) as s:
        s.execute(
            update(Lease)
            .where(Lease.resource_id == uuid.UUID(run_id))
            .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )


def tenant_of(db_url: str, run_id: str) -> uuid.UUID:
    with transaction(session_factory(db_url)) as s:
        r = s.get(WorkflowRun, uuid.UUID(run_id))
        assert r is not None
        return r.tenant_id


# ------------------------------------------------------------------ leases
@pytest.mark.release_blocker
@pytest.mark.req("FR-034", "DIR-031", "NQ-021")
def test_concurrent_claims_have_one_winner_and_tokens_are_monotonic(db_url, run) -> None:  # type: ignore[no-untyped-def]
    factory = session_factory(db_url)
    tenant, rid = tenant_of(db_url, run["run_id"]), uuid.UUID(run["run_id"])

    def claim(i: int) -> int | None:
        with transaction(factory) as s:
            h = leases.claim(s, tenant, RUN_RESOURCE, rid, f"worker-{i}", 30)
        return h.token if h else None

    with ThreadPoolExecutor(max_workers=8) as pool:
        tokens = [t for t in pool.map(claim, range(8)) if t is not None]
    assert tokens == [1]
    seen = [1]
    for i in range(3):  # each takeover after expiry yields a strictly larger token
        expire_lease(db_url, run["run_id"])
        t = claim(100 + i)
        assert t is not None and t > seen[-1]
        seen.append(t)


@pytest.mark.release_blocker
@pytest.mark.req("FR-034", "DIR-031", "NQ-021", "AER-021", "MST:REC-001")
def test_stale_token_cannot_commit_steps_checkpoints_or_effects(db_url, run, make_worker) -> None:  # type: ignore[no-untyped-def]
    factory = session_factory(db_url)
    tenant, rid = tenant_of(db_url, run["run_id"]), uuid.UUID(run["run_id"])
    with transaction(factory) as s:
        stale = leases.claim(s, tenant, RUN_RESOURCE, rid, "worker-old", 30)
    assert stale is not None
    expire_lease(db_url, run["run_id"])
    with transaction(factory) as s:
        fresh = leases.claim(s, tenant, RUN_RESOURCE, rid, "worker-new", 30)
    assert fresh is not None and fresh.token == stale.token + 1
    with pytest.raises(StaleLeaseError), transaction(factory) as s:
        leases.assert_current(s, stale)
    with transaction(factory) as s:
        assert leases.heartbeat(s, stale, 30) is None  # a stale owner cannot extend either

    worker = make_worker()
    ctx = RunContext(
        factory=factory,
        tenant_id=tenant,
        run_id=rid,
        case_id=uuid.UUID(run["case_id"]),
        requested_by=principal("a", run["analyst"].split(".")[1].split("@")[0]),
        lease=stale,
        worker_id="worker-old",
        notifier=LocalNotificationAdapter(),
        max_steps=12,
    )
    with pytest.raises(StaleLeaseError):
        ctx.run_step("authorize", lambda: {"ok": True})
    saver = TenantCheckpointer(
        factory,
        tenant_id=tenant,
        run_id=rid,
        graph_version="skeleton-1",
        encryptor=worker.deps.encryptor,
        lease=stale,
    )
    cfg = {"configurable": {"thread_id": str(rid), "checkpoint_ns": ""}}
    from langgraph.checkpoint.base import empty_checkpoint

    with pytest.raises(StaleLeaseError):
        saver.put(cfg, empty_checkpoint(), {}, {})  # type: ignore[arg-type]
    assert steps(db_url, run["run_id"]) == {}
    with transaction(factory) as s:
        assert s.scalars(select(Checkpoint)).first() is None


# ------------------------------------------------------------------ crash + takeover (slice 2)
@pytest.mark.release_blocker
@pytest.mark.req("FR-031", "FR-034", "DIR-020", "NQ-019", "NQ-020", "AER-021", "MST:REC-002")
@pytest.mark.parametrize(
    "crash_point",
    ["after_external_apply", "after_effect", "after_commit", "before_effect"],
    ids=["after-send-before-record", "after-record-before-step-commit", "after-step-commit", "before-effect"],
)
def test_worker_crash_then_takeover_resumes_without_duplicate_effect(
    make_worker, db_url, run, crash_point
) -> None:  # type: ignore[no-untyped-def]
    notifier = LocalNotificationAdapter()
    a = make_worker(
        notifier=notifier,
        worker_id="worker-A",
        faults=FaultInjector({(crash_point, "notify_assignee"): "crash"}),
    )
    with pytest.raises(SimulatedCrash):
        a.tick()
    assert run_status(db_url, run["run_id"])[0] == "RUNNING"
    b = make_worker(notifier=notifier, worker_id="worker-B")
    assert execute_run(b.deps, tenant_of(db_url, run["run_id"]), uuid.UUID(run["run_id"])) is RunOutcome.BUSY
    expire_lease(db_url, run["run_id"])
    b.tick()
    assert run_status(db_url, run["run_id"]) == ("WAITING_REVIEW", None)
    assert len(notifier.delivered) == 1  # exactly one externally visible effect
    got = steps(db_url, run["run_id"])
    assert got["authorize"] == ("SUCCEEDED", 1) and got["record_context"] == ("SUCCEEDED", 1)
    assert got["notify_assignee"][0] == "SUCCEEDED"
    if crash_point == "after_commit":  # committed step is replayed from its reference, not redone
        assert got["notify_assignee"] == ("SUCCEEDED", 1)
    with transaction(session_factory(db_url)) as s:
        ops = s.scalars(select(ExternalOperation)).all()
        assert [(o.status, o.attempts) for o in ops] == [("CONFIRMED", 1)]
        takeover = s.scalars(
            select(WorkflowRunHistory.reason).where(WorkflowRunHistory.from_status == "RUNNING")
        ).all()
    assert any("fencing token 2" in r for r in takeover)
    assert "WORKFLOW_RECOVERED" in audit_actions(db_url)


@pytest.mark.release_blocker
@pytest.mark.req("MST:ARCH-003", "MST:DB-003", "NQ-019", "NQ-020", "DIR-022")
@pytest.mark.parametrize("point", ["before_publish", "after_publish"])
def test_crash_between_commit_and_publish_loses_nothing_and_duplicates_nothing(
    make_worker, db_url, run, point
) -> None:  # type: ignore[no-untyped-def]
    notifier = LocalNotificationAdapter()
    crashing = make_worker(notifier=notifier, faults=FaultInjector({(point, "workflow.started.v1"): "crash"}))
    with pytest.raises(SimulatedCrash):
        crashing.tick()
    with transaction(session_factory(db_url)) as s:  # the business commit survived the crash
        row = s.scalars(select(OutboxEvent).where(OutboxEvent.event_type == "workflow.started.v1")).one()
        assert row.published_at is None
        event_id = row.id
    healthy = make_worker(notifier=notifier)
    for _ in range(3):
        healthy.tick()
    assert run_status(db_url, run["run_id"])[0] == "WAITING_REVIEW"
    assert len(notifier.delivered) == 1
    assert all(attempt == 1 for _, attempt in steps(db_url, run["run_id"]).values())
    with transaction(session_factory(db_url)) as s:
        assert s.scalar(select(OutboxEvent.published_at).where(OutboxEvent.id == event_id)) is not None
        inbox = s.scalars(select(InboxEvent).where(InboxEvent.event_id == event_id)).all()
    assert len(inbox) == 1


# ------------------------------------------------------------------ cancellation
class _CancelDuring(FaultInjector):
    def __init__(self, cancel: Any) -> None:
        super().__init__()
        self._cancel = cancel

    def hit(self, point: str, target: str) -> None:
        if (point, target) == ("after_effect", "record_context"):
            self._cancel()


@pytest.mark.release_blocker
@pytest.mark.req("FR-035", "NQ-021", "AER-021", "FR-005")
def test_cancel_mid_step_discards_output_preserves_committed_and_stops(
    make_worker, client, token, db_url, run
) -> None:  # type: ignore[no-untyped-def]
    notifier = LocalNotificationAdapter()

    def cancel() -> None:
        r = client.post(
            f"/api/v1/workflows/{run['run_id']}/cancel",
            json={"reason": "analyst stopped the run"},
            headers=token(run["analyst"]),
        )
        assert r.status_code == 202 and r.json()["cancel_requested"] is True

    make_worker(notifier=notifier, faults=_CancelDuring(cancel)).tick()
    assert run_status(db_url, run["run_id"])[0] == "CANCELLED"
    got = steps(db_url, run["run_id"])
    assert got["authorize"] == ("SUCCEEDED", 1)  # committed before cancellation: preserved
    assert got["record_context"][0] == "DISCARDED"  # in-flight output never accepted
    assert "notify_assignee" not in got and notifier.delivered == {}
    detail = client.get(f"/api/v1/cases/{run['case_id']}", headers=token(subject("a", "supervisor1"))).json()
    assert detail["status"] == "INVESTIGATING"  # run cancellation is not case cancellation


class _CancelBeforeInterrupt(FaultInjector):
    def __init__(self, cancel: Any) -> None:
        super().__init__()
        self._cancel = cancel

    def hit(self, point: str, target: str) -> None:
        if (point, target) == ("after_commit", "notify_assignee"):
            self._cancel()


@pytest.mark.release_blocker
@pytest.mark.req("FR-035", "NQ-021")
def test_cancel_after_last_step_boundary_is_not_lost_at_interrupt(
    make_worker, client, token, db_url, run
) -> None:  # type: ignore[no-untyped-def]
    def cancel() -> None:
        r = client.post(
            f"/api/v1/workflows/{run['run_id']}/cancel",
            json={"reason": "stop now"},
            headers=token(run["analyst"]),
        )
        assert r.status_code == 202

    make_worker(faults=_CancelBeforeInterrupt(cancel)).tick()
    assert run_status(db_url, run["run_id"])[0] == "CANCELLED"  # not parked in WAITING_REVIEW
    assert steps(db_url, run["run_id"])["notify_assignee"] == ("SUCCEEDED", 1)  # committed effect kept


@pytest.mark.release_blocker
@pytest.mark.req("FR-035", "FR-028", "NQ-021", "MST:REC-001")
def test_approval_after_cancel_is_rejected(make_worker, client, token, db_url, run) -> None:  # type: ignore[no-untyped-def]
    worker = make_worker()
    worker.tick()
    assert run_status(db_url, run["run_id"])[0] == "WAITING_REVIEW"
    r = client.post(
        f"/api/v1/workflows/{run['run_id']}/cancel",
        json={"reason": "duplicate work"},
        headers=token(run["analyst"]),
    )
    assert r.status_code == 202 and r.json()["status"] == "CANCELLED"
    from finagent.core.errors import AppError

    with pytest.raises(AppError) as err, transaction(session_factory(db_url)) as s:
        resume_run(
            s,
            tenant_of(db_url, run["run_id"]),
            uuid.UUID(run["run_id"]),
            {"decision": "CONTINUE"},
            Actor.system("approval-service"),
            "late approval",
        )
    assert err.value.code.value == "RUN_INVALID_STATE"
    worker.tick()
    assert run_status(db_url, run["run_id"])[0] == "CANCELLED"
    assert "review_received" not in steps(db_url, run["run_id"])
    again = client.post(
        f"/api/v1/workflows/{run['run_id']}/cancel", json={"reason": "again"}, headers=token(run["analyst"])
    )
    assert again.status_code == 409


@pytest.mark.release_blocker
@pytest.mark.req("FR-035", "FR-028", "NQ-021")
def test_resume_refused_while_cancellation_is_pending(make_worker, db_url, run) -> None:  # type: ignore[no-untyped-def]
    """Defence in depth for the resume guard itself: a waiting run with a pending cancellation (e.g.
    data written before the runner honoured late cancels) can never be resumed by an approval."""
    make_worker().tick()
    with transaction(session_factory(db_url)) as s:
        s.execute(update(WorkflowRun).values(cancel_requested_at=datetime.now(UTC)))
    from finagent.core.errors import AppError

    with pytest.raises(AppError), transaction(session_factory(db_url)) as s:
        resume_run(
            s,
            tenant_of(db_url, run["run_id"]),
            uuid.UUID(run["run_id"]),
            {"decision": "CONTINUE"},
            Actor.system("approval-service"),
            "approval racing a cancel",
        )
    assert run_status(db_url, run["run_id"])[0] == "WAITING_REVIEW"


# ------------------------------------------------------------------ resume validation
@pytest.mark.release_blocker
@pytest.mark.req("FR-031", "AER-019", "AER-021", "MST:REC-001")
def test_graph_version_mismatch_on_resume_goes_to_human_review(
    make_worker, client, token, db_url, run
) -> None:  # type: ignore[no-untyped-def]
    with transaction(session_factory(db_url)) as s:
        s.execute(update(WorkflowRun).values(graph_version="skeleton-0"))
    make_worker().tick()
    assert run_status(db_url, run["run_id"]) == ("HUMAN_REVIEW", "GRAPH_VERSION_MISMATCH")
    assert steps(db_url, run["run_id"]) == {}
    view = client.get(f"/api/v1/workflows/{run['run_id']}", headers=token(run["analyst"])).json()
    assert view["blockers"][0]["code"] == "GRAPH_VERSION_MISMATCH" and "review" in view["next_action"]


@pytest.mark.release_blocker
@pytest.mark.req("FR-034", "SPR-011", "DIR-033", "MST:ARCH-001")
def test_rescinded_requester_permission_stops_background_execution(make_worker, db_url, run) -> None:  # type: ignore[no-untyped-def]
    analyst_key = run["analyst"].split(".")[1].split("@")[0]
    with transaction(session_factory(db_url)) as s:
        s.execute(
            update(RoleAssignment)
            .where(RoleAssignment.principal_id == principal("a", analyst_key))
            .values(valid_to=RoleAssignment.valid_from)
        )
    notifier = LocalNotificationAdapter()
    make_worker(notifier=notifier).tick()
    assert run_status(db_url, run["run_id"]) == ("HUMAN_REVIEW", "REQUESTER_NOT_AUTHORIZED")
    assert steps(db_url, run["run_id"]) == {} and notifier.delivered == {}


@pytest.mark.req("MST:AG-002", "AER-020", "NQ-028")
def test_step_budget_stops_runaway_runs(make_worker, db_url, run) -> None:  # type: ignore[no-untyped-def]
    make_worker(max_steps_per_run=2).tick()
    assert run_status(db_url, run["run_id"]) == ("HUMAN_REVIEW", "STEP_BUDGET_EXCEEDED")
    assert set(steps(db_url, run["run_id"])) == {"authorize", "record_context"}


# ------------------------------------------------------------------ uncertain external effects
class _AmbiguousNotifier(LocalNotificationAdapter):
    """Times out AFTER the provider may have acted; cannot confirm either way."""

    def __init__(self, probe: DeliveryStatus) -> None:
        super().__init__()
        self.sends, self._probe = 0, probe

    def send(self, notification: Notification) -> None:
        self.sends += 1
        raise TimeoutError("provider timeout")

    def status(self, tenant_id: uuid.UUID, idempotency_key: str) -> DeliveryStatus:
        return self._probe


@pytest.mark.release_blocker
@pytest.mark.req("FR-033", "DIR-029", "NQ-020", "MST:REC-002", "MST:REC-003", "SPR-005")
@pytest.mark.parametrize("probe", [DeliveryStatus.NOT_FOUND, DeliveryStatus.UNKNOWN])
def test_ambiguous_external_outcome_becomes_uncertain_and_needs_human_reconcile(
    make_worker, client, token, db_url, run, probe
) -> None:  # type: ignore[no-untyped-def]
    notifier = _AmbiguousNotifier(probe)
    worker = make_worker(notifier=notifier)
    for _ in range(3):
        worker.tick()
    assert run_status(db_url, run["run_id"]) == ("OUTCOME_UNCERTAIN", "OUTCOME_UNCERTAIN")
    assert notifier.sends == 1  # never blindly resent
    with transaction(session_factory(db_url)) as s:
        op = s.scalars(select(ExternalOperation)).one()
        assert op.status == "OUTCOME_UNCERTAIN" and op.last_error_code == "AMBIGUOUS_TimeoutError"
    assert (
        client.post(
            f"/api/v1/workflows/{run['run_id']}/cancel",
            json={"reason": "try cancel"},
            headers=token(run["analyst"]),
        ).status_code
        == 409
    )
    body = {"resolution": "NOT_APPLIED", "reason": "provider console shows no message sent"}
    url = f"/api/v1/operations/{op.id}/reconcile"
    assert client.post(url, json=body, headers=token(run["analyst"])).status_code == 403
    assert client.post(url, json=body, headers=token("a.operator1@synthetic", mfa=False)).status_code == 401
    assert client.post(url, json=body, headers=token("b.operator1@synthetic")).status_code == 404
    r = client.post(url, json=body, headers=token("a.operator1@synthetic"))
    assert r.status_code == 200 and r.json()["status"] == "RECONCILED_NOT_APPLIED"
    assert client.post(url, json=body, headers=token("a.operator1@synthetic")).status_code == 409
    assert run_status(db_url, run["run_id"])[0] == "HUMAN_REVIEW"  # never auto-resumed
    assert {"EXTERNAL_OPERATION_UNCERTAIN", "EXTERNAL_OPERATION_RECONCILED"} <= set(audit_actions(db_url))
    with transaction(session_factory(db_url)) as s:
        assert (
            s.scalars(
                select(OutboxEvent).where(OutboxEvent.event_type == "external_operation.uncertain.v1")
            ).first()
            is not None
        )


# ------------------------------------------------------------------ checkpoint protection
@pytest.mark.release_blocker
@pytest.mark.req("DIR-032", "SPR-010", "AER-021", "NQ-022")
def test_checkpoints_are_encrypted_tenant_bound_and_scope_checked(make_worker, db_url, run) -> None:  # type: ignore[no-untyped-def]
    worker = make_worker()
    worker.tick()
    rid = uuid.UUID(run["run_id"])
    with transaction(session_factory(db_url)) as s:
        rows = s.scalars(select(Checkpoint).where(Checkpoint.run_id == rid)).all()
    assert rows and all(run["case_id"].encode() not in r.checkpoint_data for r in rows)
    enc = Encryptor(worker.settings.data_key())
    with pytest.raises(DecryptionError):  # the same ciphertext under another run's binding
        enc.decrypt(rows[0].checkpoint_data, f"{rows[0].tenant_id}:{uuid.uuid4()}::checkpoint".encode())
    with pytest.raises(DecryptionError):
        Encryptor(b"k" * 32).decrypt(
            rows[0].checkpoint_data, f"{rows[0].tenant_id}:{rid}::checkpoint".encode()
        )
    saver = TenantCheckpointer(
        session_factory(db_url),
        tenant_id=rows[0].tenant_id,
        run_id=rid,
        graph_version="skeleton-1",
        encryptor=enc,
        lease=None,
    )
    assert saver.get_tuple({"configurable": {"thread_id": str(rid), "checkpoint_ns": ""}}) is not None
    with pytest.raises(CheckpointScopeError):
        saver.get_tuple({"configurable": {"thread_id": str(uuid.uuid4()), "checkpoint_ns": ""}})
    other_tenant = TenantCheckpointer(
        session_factory(db_url),
        tenant_id=uuid.uuid4(),
        run_id=rid,
        graph_version="skeleton-1",
        encryptor=enc,
        lease=None,
    )
    assert other_tenant.get_tuple({"configurable": {"thread_id": str(rid), "checkpoint_ns": ""}}) is None
