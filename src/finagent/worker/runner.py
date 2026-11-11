"""Execute one workflow run under a fenced lease (FR-031/033/034/035, AER-019/021, NQ-019/021/023).

Order of checks before any node runs (all inside a fenced transaction):
  terminal/paused? -> cancellation requested? -> graph version compatible? -> requester still
  authorized for this tenant and case? -> QUEUED/RETRYING -> RUNNING, or RUNNING takeover recorded.
Then the graph resumes from its last checkpoint (or from the deterministic resume payload).
Outcomes map to run statuses deterministically; nothing the graph returns is a state command.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.types import Command
from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.orm import Session, sessionmaker

from finagent.agents.gateway import BudgetExceededError, ModelUnavailableError
from finagent.agents.registry import CapabilityUnavailableError
from finagent.core.config import Settings
from finagent.core.crypto import Encryptor
from finagent.core.errors import AppError
from finagent.database.models import Lease, WorkflowRun, WorkflowRunHistory
from finagent.database.session import transaction
from finagent.domain.states import RUN_TERMINAL, RunStatus
from finagent.integrations.notifications.port import NotificationPort
from finagent.orchestration.checkpointer import TenantCheckpointer
from finagent.orchestration.faults import FaultInjector, SimulatedCrash
from finagent.orchestration.graphs.aml import AgentEscalationError
from finagent.orchestration.registry import spec_for
from finagent.orchestration.runtime import Heartbeater, RunCancelledError, RunContext, StepBudgetExceededError
from finagent.security.authentication.resolver import resolve_principal_by_id
from finagent.security.authorization.policy import Permission
from finagent.services.audit_service import writer as audit
from finagent.services.case_service.queries import load_scoped_case
from finagent.services.case_service.transitions import Actor
from finagent.services.review_service.finalization import FinalizationFailedError
from finagent.services.workflow_service.external_ops import EffectRejectedError, OutcomeUncertainError
from finagent.services.workflow_service.runs import change_run_status
from finagent.worker import leases
from finagent.worker.leases import RUN_RESOURCE, LeaseHandle, StaleLeaseError

log = logging.getLogger("finagent.worker")
PERMANENT_MODEL_ERRORS = frozenset({"CONTENT_FILTERED", "AUTH", "BAD_REQUEST", "BAD_RESPONSE", "CANCELLED"})
_PAUSED = frozenset({RunStatus.WAITING_EVIDENCE, RunStatus.WAITING_REVIEW, RunStatus.HUMAN_REVIEW})


class RunOutcome(StrEnum):
    BUSY = "BUSY"  # another worker holds the lease
    SKIPPED = "SKIPPED"  # nothing to do (terminal, paused, not yet due)
    STALE = "STALE"  # we lost the lease mid-run; the new owner continues
    WAITING = "WAITING"
    SUCCEEDED = "SUCCEEDED"
    CANCELLED = "CANCELLED"
    RETRY_SCHEDULED = "RETRY_SCHEDULED"
    FAILED = "FAILED"
    HUMAN_REVIEW = "HUMAN_REVIEW"
    UNCERTAIN = "UNCERTAIN"


@dataclass
class WorkerDeps:
    factory: sessionmaker[Session]
    settings: Settings
    notifier: NotificationPort
    worker_id: str
    faults: FaultInjector = field(default_factory=FaultInjector)
    model_provider: Any = None  # tests inject a scripted MockModelProvider
    _services: Any = None

    @property
    def encryptor(self) -> Encryptor:
        return Encryptor(self.settings.data_key())

    @property
    def services(self) -> Any:
        if self._services is None:
            from finagent.orchestration.services import build_services

            self._services = build_services(self.factory, self.settings, self.model_provider)
        return self._services


def _lock_run(s: Session, tenant_id: uuid.UUID, run_id: uuid.UUID) -> WorkflowRun:
    return s.scalars(
        select(WorkflowRun)
        .where(WorkflowRun.tenant_id == tenant_id, WorkflowRun.id == run_id)
        .with_for_update()
    ).one()


def _requester_authorized(s: Session, run: WorkflowRun) -> bool:
    try:
        ctx = resolve_principal_by_id(s, run.tenant_id, run.requested_by)
    except AppError:
        return False
    if Permission.WORKFLOW_START not in ctx.permissions:
        return False
    return load_scoped_case(s, ctx, str(run.case_id)) is not None


def find_runnable(factory: sessionmaker[Session], limit: int = 20) -> list[tuple[uuid.UUID, uuid.UUID]]:
    """Runs a worker should try: queued, retry due, or RUNNING with no live lease (crashed owner)."""
    live_lease = exists().where(
        Lease.tenant_id == WorkflowRun.tenant_id,
        Lease.resource_type == RUN_RESOURCE,
        Lease.resource_id == WorkflowRun.id,
        Lease.owner.is_not(None),
        Lease.expires_at > func.clock_timestamp(),
    )
    with transaction(factory) as s:
        rows = s.execute(
            select(WorkflowRun.tenant_id, WorkflowRun.id)
            .where(
                or_(
                    WorkflowRun.status == RunStatus.QUEUED.value,
                    and_(
                        WorkflowRun.status == RunStatus.RETRYING.value,
                        WorkflowRun.next_attempt_at <= func.now(),
                    ),
                    and_(WorkflowRun.status == RunStatus.RUNNING.value, ~live_lease),
                )
            )
            .order_by(WorkflowRun.updated_at)
            .limit(limit)
        ).all()
    return [(r[0], r[1]) for r in rows]


def execute_run(deps: WorkerDeps, tenant_id: uuid.UUID, run_id: uuid.UUID) -> RunOutcome:
    ttl = deps.settings.lease_ttl_seconds
    with transaction(deps.factory) as s:
        lease = leases.claim(s, tenant_id, RUN_RESOURCE, run_id, deps.worker_id, ttl)
    if lease is None:
        return RunOutcome.BUSY
    try:
        with Heartbeater(deps.factory, lease, ttl) as hb:
            outcome = _execute(deps, lease, hb)
    except SimulatedCrash:
        raise  # a killed worker releases nothing: its lease must expire before takeover
    except StaleLeaseError:
        log.warning("lease_lost", extra={"run_id": str(run_id), "token": lease.token})
        return RunOutcome.STALE
    with transaction(deps.factory) as s:
        leases.release(s, lease)
    return outcome


def _finish(
    deps: WorkerDeps,
    lease: LeaseHandle,
    target: RunStatus,
    reason: str,
    code: str | None = None,
    **extra: Any,
) -> None:
    actor = Actor.system(f"worker:{deps.worker_id}")
    with transaction(deps.factory) as s:
        leases.assert_current(s, lease)
        run = _lock_run(s, lease.tenant_id, lease.resource_id)
        if RunStatus(run.status) is target:
            return
        run.last_error_code = code
        for k, v in extra.items():
            setattr(run, k, v)
        change_run_status(s, run, target, reason, actor)


def _execute(deps: WorkerDeps, lease: LeaseHandle, hb: Heartbeater) -> RunOutcome:
    actor = Actor.system(f"worker:{deps.worker_id}")
    now = datetime.now(UTC)
    with transaction(deps.factory) as s:
        leases.assert_current(s, lease)
        run = _lock_run(s, lease.tenant_id, lease.resource_id)
        status = RunStatus(run.status)
        if status in RUN_TERMINAL or status in _PAUSED or status is RunStatus.OUTCOME_UNCERTAIN:
            return RunOutcome.SKIPPED
        if status is RunStatus.RETRYING and run.next_attempt_at and run.next_attempt_at > now:
            return RunOutcome.SKIPPED
        if run.cancel_requested_at is not None:
            change_run_status(s, run, RunStatus.CANCELLED, "cancelled before execution", actor)
            return RunOutcome.CANCELLED
        spec = spec_for(run.workflow_type)
        if run.graph_version != spec.graph_version:
            run.last_error_code = "GRAPH_VERSION_MISMATCH"
            target = RunStatus.HUMAN_REVIEW
            if status is RunStatus.QUEUED or status is RunStatus.RETRYING:
                change_run_status(s, run, RunStatus.RUNNING, "claimed for compatibility check", actor)
            change_run_status(
                s,
                run,
                target,
                f"checkpoint graph {run.graph_version} != deployed {spec.graph_version}",
                actor,
            )
            return RunOutcome.HUMAN_REVIEW
        if not _requester_authorized(s, run):
            run.last_error_code = "REQUESTER_NOT_AUTHORIZED"
            if status is not RunStatus.RUNNING:
                change_run_status(s, run, RunStatus.RUNNING, "claimed for authorization check", actor)
            change_run_status(
                s, run, RunStatus.HUMAN_REVIEW, "requester no longer authorized for this case", actor
            )
            return RunOutcome.HUMAN_REVIEW
        if status is RunStatus.RUNNING:  # takeover after the previous owner's lease expired
            s.add(
                WorkflowRunHistory(
                    tenant_id=run.tenant_id,
                    run_id=run.id,
                    from_status="RUNNING",
                    to_status="RUNNING",
                    reason=f"resumed from checkpoint by {deps.worker_id} (fencing token {lease.token})",
                    actor=actor.label,
                )
            )
            audit.record(
                s,
                tenant_id=run.tenant_id,
                action="WORKFLOW_RECOVERED",
                object_type="workflow_run",
                object_id=str(run.id),
                outcome="SUCCESS",
                actor_principal_id=None,
                actor_kind="SYSTEM",
                case_id=run.case_id,
                details={"fencing_token": lease.token, "worker_id": deps.worker_id},
            )
        else:
            run.attempt += 1
            change_run_status(s, run, RunStatus.RUNNING, f"claimed by {deps.worker_id}", actor)
        resume_payload = run.resume_payload
        case_id, requested_by, graph_version = run.case_id, run.requested_by, run.graph_version
        attempt, max_attempts = run.attempt, run.max_attempts

    rt = RunContext(
        factory=deps.factory,
        tenant_id=lease.tenant_id,
        run_id=lease.resource_id,
        case_id=case_id,
        requested_by=requested_by,
        lease=lease,
        worker_id=deps.worker_id,
        notifier=deps.notifier,
        max_steps=deps.settings.max_steps_per_run,
        faults=deps.faults,
        heartbeat=hb,
        services=deps.services if spec.budget_exempt else None,
        budget_exempt=spec.budget_exempt,
    )
    saver = TenantCheckpointer(
        deps.factory,
        tenant_id=lease.tenant_id,
        run_id=lease.resource_id,
        graph_version=graph_version,
        encryptor=deps.encryptor,
        lease=lease,
    )
    graph = spec.build(saver)
    config: RunnableConfig = {
        "configurable": {
            "thread_id": str(lease.resource_id),
            "checkpoint_ns": "",
            "tenant_id": str(lease.tenant_id),
            "graph_version": graph_version,
            "fencing_token": lease.token,
            "finagent_rt": rt,
        }
    }
    snapshot = graph.get_state(config) if saver.get_tuple(config) is not None else None
    pending_interrupt = bool(snapshot is not None and snapshot.interrupts)
    if pending_interrupt and resume_payload is not None:
        graph_input: Any = Command(resume=resume_payload)
    elif snapshot is not None:
        graph_input = None  # continue from the last durable checkpoint (crash recovery)
    else:
        graph_input = {
            "run_id": str(lease.resource_id),
            "case_id": str(case_id),
            "tenant_id": str(lease.tenant_id),
            "graph_version": graph_version,
            "completed": [],
        }

    try:
        deps.faults.hit("before_invoke", "*")
        graph.invoke(graph_input, config)
    except (SimulatedCrash, StaleLeaseError):
        raise
    except RunCancelledError:
        _finish(deps, lease, RunStatus.CANCELLED, "cancelled at a step boundary; committed effects kept")
        return RunOutcome.CANCELLED
    except StepBudgetExceededError:
        _finish(deps, lease, RunStatus.HUMAN_REVIEW, "step budget exhausted", "STEP_BUDGET_EXCEEDED")
        return RunOutcome.HUMAN_REVIEW
    except BudgetExceededError as exc:  # model token/cost budget: stop billable work, keep state
        _finish(
            deps, lease, RunStatus.HUMAN_REVIEW, f"model budget exhausted ({exc.scope})", "BUDGET_EXCEEDED"
        )
        return RunOutcome.HUMAN_REVIEW
    except (AgentEscalationError, CapabilityUnavailableError, FinalizationFailedError) as exc:
        _finish(
            deps, lease, RunStatus.HUMAN_REVIEW, f"escalated to a human: {exc}"[:300], type(exc).__name__[:64]
        )
        return RunOutcome.HUMAN_REVIEW
    except ModelUnavailableError as exc:
        if exc.kind in PERMANENT_MODEL_ERRORS or attempt >= max_attempts:
            _finish(
                deps, lease, RunStatus.HUMAN_REVIEW, f"model unavailable ({exc.kind})", "MODEL_UNAVAILABLE"
            )
            return RunOutcome.HUMAN_REVIEW
        delay = deps.settings.retry_backoff_seconds * (2 ** (attempt - 1))
        _finish(
            deps,
            lease,
            RunStatus.RETRYING,
            f"model unavailable; retry in {delay}s",
            "MODEL_UNAVAILABLE",
            next_attempt_at=datetime.now(UTC) + timedelta(seconds=delay),
        )
        return RunOutcome.RETRY_SCHEDULED
    except OutcomeUncertainError as exc:
        _finish(
            deps,
            lease,
            RunStatus.OUTCOME_UNCERTAIN,
            f"external operation {exc.operation_id} needs reconciliation",
            "OUTCOME_UNCERTAIN",
        )
        return RunOutcome.UNCERTAIN
    except (EffectRejectedError, PermissionError, ValueError) as exc:  # permanent: correction required
        _finish(
            deps, lease, RunStatus.FAILED, "permanent failure; correction required", type(exc).__name__[:64]
        )
        return RunOutcome.FAILED
    except Exception as exc:  # classified transient (DB blip, provider timeout before send, ...)
        code = type(exc).__name__[:64]
        if attempt >= max_attempts:
            _finish(deps, lease, RunStatus.FAILED, f"retries exhausted after {attempt} attempts", code)
            return RunOutcome.FAILED
        delay = deps.settings.retry_backoff_seconds * (2 ** (attempt - 1))
        _finish(
            deps,
            lease,
            RunStatus.RETRYING,
            f"transient failure; retry in {delay}s",
            code,
            next_attempt_at=datetime.now(UTC) + timedelta(seconds=delay),
        )
        return RunOutcome.RETRY_SCHEDULED

    after = graph.get_state(config)
    with transaction(deps.factory) as s:
        leases.assert_current(s, lease)
        run = _lock_run(s, lease.tenant_id, lease.resource_id)
        run.resume_payload = None
        if run.cancel_requested_at is not None:
            # Cancellation arrived after the last step boundary (e.g. just before an interrupt):
            # honour it instead of parking the run as waiting or reporting success (FR-035).
            change_run_status(s, run, RunStatus.CANCELLED, "cancelled after the last step boundary", actor)
            return RunOutcome.CANCELLED
        if after.interrupts:
            waiting = after.interrupts[0].value
            if isinstance(waiting, dict) and waiting.get("waiting_for") == "EVIDENCE":
                change_run_status(
                    s, run, RunStatus.WAITING_EVIDENCE, "waiting for the analyst to supply evidence", actor
                )
            else:
                change_run_status(s, run, RunStatus.WAITING_REVIEW, "waiting for review", actor)
            return RunOutcome.WAITING
        change_run_status(s, run, RunStatus.SUCCEEDED, "workflow completed", actor)
        return RunOutcome.SUCCEEDED
