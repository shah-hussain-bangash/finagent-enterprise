"""Workflow API, worker lifecycle, outbox relay, inbox dedup and DLQ (P4; FR-005, FR-031, FR-032,
FR-035, MST:ARCH-003, MST:DB-003, NQ-020, NQ-023, DIR-022)."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import select, update

from finagent.database.models import (
    Checkpoint,
    DeadLetter,
    InboxEvent,
    OutboxEvent,
    QueueMessage,
    WorkflowRun,
)
from finagent.database.session import session_factory, transaction
from finagent.domain.states import RunStatus
from finagent.services.case_service.transitions import Actor
from finagent.services.workflow_service.runs import resume_run
from finagent.worker.events import EventEnvelope
from finagent.worker.queue import PostgresQueue
from tests.helpers import (
    assigned_analyst,
    audit_actions,
    ingest,
    run_status,
    scalar_count,
    start_run,
    steps,
    subject,
)

pytestmark = pytest.mark.integration


def drive(worker: Any, ticks: int = 3) -> list[dict[str, Any]]:
    return [worker.tick() for _ in range(ticks)]


@pytest.fixture
def started(client, token):  # type: ignore[no-untyped-def]
    case_id = ingest(client, token)["case_id"]
    analyst = assigned_analyst(client, token, case_id)
    r = start_run(client, token, case_id, analyst)
    assert r.status_code == 202, r.text
    return case_id, analyst, r.json()["run_id"]


def resume(db_url: str, run_id: str, decision: str = "CONTINUE") -> None:
    with transaction(session_factory(db_url)) as s:
        run = s.get(WorkflowRun, uuid.UUID(run_id))
        assert run is not None
        resume_run(s, run.tenant_id, run.id, {"decision": decision}, Actor.system("test"), "test resume")


# ------------------------------------------------------------------ API
@pytest.mark.req("FR-005", "FR-032", "FR-007", "MST:API-003", "NQ-006")
def test_start_returns_202_status_url_and_is_idempotent(client, token, db_url) -> None:  # type: ignore[no-untyped-def]
    case_id = ingest(client, token)["case_id"]
    analyst = assigned_analyst(client, token, case_id)
    first = start_run(client, token, case_id, analyst, key="wf-key-0001")
    assert first.status_code == 202 and first.headers["Location"] == first.json()["status_url"]
    again = start_run(client, token, case_id, analyst, key="wf-key-0001")
    assert again.status_code == 202 and again.json() == first.json()
    other_key = start_run(client, token, case_id, analyst)
    assert other_key.status_code == 409 and other_key.json()["error"]["code"] == "RUN_INVALID_STATE"
    assert scalar_count(db_url, WorkflowRun) == 1
    body = client.get(first.json()["status_url"], headers=token(analyst)).json()
    assert body["status"] == "QUEUED" and [h["to"] for h in body["history"]] == ["CREATED", "QUEUED"]
    detail = client.get(f"/api/v1/cases/{case_id}", headers=token(subject("a", "supervisor1"))).json()
    assert detail["status"] == "INVESTIGATING"  # ASSIGNED -> INVESTIGATING on start (SRS §3)
    assert scalar_count(db_url, OutboxEvent, event_type="workflow.started.v1") == 1


@pytest.mark.release_blocker
@pytest.mark.req("FR-002", "FR-005", "SPR-009", "NFR-002")
def test_run_visibility_is_tenant_and_case_scoped(client, token, started) -> None:  # type: ignore[no-untyped-def]
    case_id, analyst, run_id = started
    other = "a.analyst2@synthetic" if analyst == "a.analyst1@synthetic" else "a.analyst1@synthetic"
    for subj in (other, "b.supervisor1@synthetic", "b.operator1@synthetic"):
        assert client.get(f"/api/v1/workflows/{run_id}", headers=token(subj)).status_code == 404, subj
    assert client.get(f"/api/v1/workflows/{run_id}", headers=token("a.auditor1@synthetic")).status_code == 403
    assert (
        client.get(f"/api/v1/workflows/{run_id}", headers=token("a.operator1@synthetic")).status_code == 200
    )
    r = start_run(client, token, case_id, "b.analyst1@synthetic")
    assert r.status_code == 404  # another tenant cannot start work on this case
    assert (
        client.post(
            f"/api/v1/workflows/{run_id}/cancel",
            json={"reason": "attack"},
            headers=token("b.supervisor1@synthetic"),
        ).status_code
        == 404
    )


# ------------------------------------------------------------------ lifecycle
@pytest.mark.req("FR-005", "FR-031", "DIR-020", "MST:ARCH-003", "MST:DB-003", "AER-021", "FR-036")
def test_worker_runs_to_review_interrupt_then_completes_after_resume(
    make_worker, client, token, db_url, started
) -> None:  # type: ignore[no-untyped-def]
    _, analyst, run_id = started
    worker = make_worker()
    drive(worker)
    assert run_status(db_url, run_id) == ("WAITING_REVIEW", None)
    assert steps(db_url, run_id) == {
        "authorize": ("SUCCEEDED", 1),
        "record_context": ("SUCCEEDED", 1),
        "notify_assignee": ("SUCCEEDED", 1),
    }
    assert len(worker.notifier.delivered) == 1
    assert scalar_count(db_url, Checkpoint, run_id=uuid.UUID(run_id)) >= 3
    resume(db_url, run_id)
    drive(worker)
    view = client.get(f"/api/v1/workflows/{run_id}", headers=token(analyst)).json()
    assert view["status"] == "SUCCEEDED" and view["finished_at"] is not None
    assert [h["to"] for h in view["history"]] == [
        "CREATED",
        "QUEUED",
        "RUNNING",
        "WAITING_REVIEW",
        "QUEUED",
        "RUNNING",
        "SUCCEEDED",
    ]
    assert steps(db_url, run_id)["finalize"] == ("SUCCEEDED", 1)
    assert len(worker.notifier.delivered) == 1  # resume did not repeat the effect
    with transaction(session_factory(db_url)) as s:
        types = s.scalars(select(OutboxEvent.event_type).where(OutboxEvent.resource_id == run_id)).all()
    assert sorted(types) == sorted(
        [
            "workflow.started.v1",
            "workflow.awaiting_review.v1",
            "workflow.resume_requested.v1",
            "workflow.completed.v1",
        ]
    )
    assert "WORKFLOW_STATUS_CHANGED" in audit_actions(db_url)
    assert scalar_count(db_url, OutboxEvent, published_at=None) == 0  # relay drained the outbox


@pytest.mark.req("FR-035", "FR-005")
def test_stop_decision_ends_run_without_finalize_and_invalid_resume_fails(
    make_worker, db_url, client, token
) -> None:  # type: ignore[no-untyped-def]
    worker = make_worker()
    runs = []
    for ref in ("AML-STOP", "AML-BAD"):
        case_id = ingest(client, token, ref=ref)["case_id"]
        runs.append(
            start_run(client, token, case_id, assigned_analyst(client, token, case_id)).json()["run_id"]
        )
    drive(worker)
    resume(db_url, runs[0], "STOP")
    resume(db_url, runs[1], "APPROVE_EVERYTHING")  # not a permitted value (LLM-style free text)
    drive(worker)
    assert run_status(db_url, runs[0])[0] == "SUCCEEDED" and "finalize" not in steps(db_url, runs[0])
    assert run_status(db_url, runs[1]) == ("FAILED", "InvalidResumeError")


# ------------------------------------------------------------------ outbox / inbox / DLQ
@pytest.mark.release_blocker
@pytest.mark.req("NQ-020", "DIR-022", "MST:ARCH-003", "FR-032")
def test_duplicate_delivery_is_processed_once(make_worker, db_url, started) -> None:  # type: ignore[no-untyped-def]
    worker = make_worker()
    assert worker.tick()["consumed"] >= 1  # case.created, case.status_changed, workflow.started
    with transaction(session_factory(db_url)) as s:
        row = s.scalars(select(OutboxEvent).where(OutboxEvent.event_type == "workflow.started.v1")).one()
        from finagent.worker.events import from_outbox

        envelope = from_outbox(row).signed(worker.settings.signing_key())
    PostgresQueue(worker.factory).publish(envelope)  # broker redelivers the same event id
    stats = worker.consumer.poll()
    assert stats.processed == 0 and stats.duplicates == 1 and stats.dispatched == []
    assert scalar_count(db_url, InboxEvent, event_id=envelope.event_id) == 1


def _publish_raw(worker: Any, body: dict[str, Any]) -> None:
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    with transaction(worker.factory) as s:
        s.execute(
            pg_insert(QueueMessage).values(
                tenant_id=uuid.UUID(str(body.get("tenant_ref"))),
                topic="finagent-events",
                event_id=uuid.uuid4(),
                body=body,
            )
        )


@pytest.mark.release_blocker
@pytest.mark.req("NQ-023", "DIR-022", "SPR-007", "NQ-020")
def test_forged_unknown_and_cross_tenant_events_are_dead_lettered(make_worker, db_url, started) -> None:  # type: ignore[no-untyped-def]
    _, _, run_id = started
    worker = make_worker()
    worker.tick()
    from finagent.database.seeds import sid

    base = EventEnvelope(
        event_id=uuid.uuid4(),
        event_type="workflow.started.v1",
        schema_version="1",
        tenant_ref=sid("tenant:a"),
        resource_id=run_id,
        occurred_at="2026-10-15T00:00:00Z",  # type: ignore[arg-type]
        correlation_id="corr-test-0001",
        payload={"run_id": run_id},
    )
    key = worker.settings.signing_key()
    forged = base.signed(b"x" * 32)
    unknown = base.model_copy(update={"event_id": uuid.uuid4(), "event_type": "funds.transfer.v1"}).signed(
        key
    )
    cross = base.model_copy(update={"event_id": uuid.uuid4(), "tenant_ref": sid("tenant:b")}).signed(key)
    queue = PostgresQueue(worker.factory)
    for env in (forged, unknown, cross):
        queue.publish(env)
    _publish_raw(worker, {"tenant_ref": str(sid("tenant:a")), "garbage": True})
    stats = worker.tick()
    assert stats["dead_lettered"] == 4
    with transaction(session_factory(db_url)) as s:
        reasons = sorted(s.scalars(select(DeadLetter.reason_code)).all())
    assert reasons == [
        "MALFORMED_ENVELOPE",
        "RUN_NOT_FOUND_IN_TENANT",
        "SIGNATURE_INVALID",
        "UNKNOWN_EVENT_TYPE",
    ]
    assert scalar_count(db_url, QueueMessage) == 0  # poison never loops
    assert run_status(db_url, run_id)[0] == "WAITING_REVIEW"  # forged events changed nothing


@pytest.mark.req("NQ-023", "DIR-028")
def test_failing_handler_is_released_then_dead_lettered_after_max_deliveries(
    make_worker, db_url, started
) -> None:  # type: ignore[no-untyped-def]
    worker = make_worker(consumer_max_deliveries=3)

    def boom(s: Any, env: Any) -> None:
        raise RuntimeError("downstream unavailable")

    worker.consumer._handlers["workflow.started.v1"] = boom
    worker.consumer._delay = 0
    from finagent.worker.outbox_relay import relay_batch

    relay_batch(worker.factory, PostgresQueue(worker.factory), worker.settings.signing_key())
    for _ in range(3):
        worker.consumer.poll()
    with transaction(session_factory(db_url)) as s:
        dl = s.scalars(select(DeadLetter)).one()
        assert dl.reason_code == "MAX_DELIVERIES:RuntimeError" and dl.delivery_count == 3
        assert dl.owner_role == "PLATFORM_OPERATOR" and dl.status == "OPEN"
    assert "EVENT_DEAD_LETTERED" in audit_actions(db_url)
    assert scalar_count(db_url, QueueMessage) == 0


@pytest.mark.req("MST:ARCH-003", "NQ-019", "NFR-008")
def test_broker_outage_keeps_events_in_outbox_with_backoff(make_worker, db_url, started) -> None:  # type: ignore[no-untyped-def]
    class DownQueue(PostgresQueue):
        def publish(self, envelope: EventEnvelope) -> None:
            raise ConnectionError("broker down")

    worker = make_worker()
    from finagent.worker.outbox_relay import relay_batch

    result = relay_batch(worker.factory, DownQueue(worker.factory), worker.settings.signing_key())
    assert result.published == 0 and result.failed >= 1
    with transaction(session_factory(db_url)) as s:
        row = s.scalars(select(OutboxEvent).where(OutboxEvent.event_type == "workflow.started.v1")).one()
        assert row is not None and row.published_at is None and row.attempts == 1
        assert row.last_error_code == "ConnectionError" and row.next_attempt_at is not None
        s.execute(update(OutboxEvent).values(next_attempt_at=None))
    assert (
        relay_batch(worker.factory, PostgresQueue(worker.factory), worker.settings.signing_key()).published
        >= 1
    )


@pytest.mark.req("FR-005", "FR-033", "DIR-028", "NQ-023")
def test_transient_failure_retries_with_backoff_then_fails_when_exhausted(
    make_worker, db_url, started
) -> None:  # type: ignore[no-untyped-def]
    from finagent.orchestration.faults import FaultInjector

    _, _, run_id = started
    faults = FaultInjector({("before_invoke", "*"): "error"}, once=False)
    worker = make_worker(faults=faults, retry_backoff_seconds=1)
    with transaction(session_factory(db_url)) as s:  # the run's limit is fixed when it is created
        s.execute(update(WorkflowRun).values(max_attempts=2))
    worker.tick()
    assert run_status(db_url, run_id) == ("RETRYING", "RuntimeError")
    with transaction(session_factory(db_url)) as s:
        s.execute(update(WorkflowRun).values(next_attempt_at=WorkflowRun.created_at))
    worker.tick()
    assert run_status(db_url, run_id) == ("FAILED", "RuntimeError")
    view_history = [RunStatus(s) for s in ("QUEUED", "RUNNING", "RETRYING", "RUNNING", "FAILED")]
    with transaction(session_factory(db_url)) as s:
        from finagent.database.models import WorkflowRunHistory

        got = s.scalars(
            select(WorkflowRunHistory.to_status)
            .where(WorkflowRunHistory.run_id == uuid.UUID(run_id))
            .order_by(WorkflowRunHistory.seq)
        ).all()
    assert [RunStatus(g) for g in got[1:]] == view_history
