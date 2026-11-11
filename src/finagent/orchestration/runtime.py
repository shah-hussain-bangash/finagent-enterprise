"""Per-run execution context used by graph nodes (FR-031, FR-034, FR-035, AER-021, MST:AG-002).

`run_step` is the only way a node does work:
  1. fenced txn: refuse if cancelled; if this step already SUCCEEDED (a replay after a crash),
     return its stored output WITHOUT redoing the work; enforce the step budget; mark RUNNING.
  2. do the work (effects go through the external-operation ledger).
  3. fenced txn: if cancellation arrived meanwhile, the output is DISCARDED (never accepted);
     otherwise SUCCEEDED with an output reference.
Every protected commit re-checks the fencing token, so a superseded worker cannot commit.
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from finagent.database.models import WorkflowRun, WorkflowStep
from finagent.database.session import transaction
from finagent.integrations.notifications.port import NotificationPort
from finagent.orchestration.faults import FaultInjector
from finagent.worker import leases
from finagent.worker.leases import LeaseHandle, StaleLeaseError

log = logging.getLogger("finagent.worker")


class RunCancelledError(Exception):
    pass


class StepBudgetExceededError(Exception):
    pass


@dataclass
class Heartbeater:
    """Background lease renewal while a run executes. `lost` is set if renewal ever fails."""

    factory: sessionmaker[Session]
    lease: LeaseHandle
    ttl_s: int
    lost: threading.Event = field(default_factory=threading.Event)
    _stop: threading.Event = field(default_factory=threading.Event)
    _thread: threading.Thread | None = None

    def __enter__(self) -> Heartbeater:
        self._thread = threading.Thread(target=self._loop, name="lease-heartbeat", daemon=True)
        self._thread.start()
        return self

    def _loop(self) -> None:
        interval = max(1.0, self.ttl_s / 3)
        while not self._stop.wait(interval):
            try:
                with transaction(self.factory) as s:
                    renewed = leases.heartbeat(s, self.lease, self.ttl_s)
            except Exception as exc:  # DB blip: retry next interval; fencing still protects commits
                log.warning("lease_heartbeat_failed", extra={"error_type": type(exc).__name__})
                continue
            if renewed is None:
                self.lost.set()
                return

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)


@dataclass
class RunContext:
    factory: sessionmaker[Session]
    tenant_id: uuid.UUID
    run_id: uuid.UUID
    case_id: uuid.UUID
    requested_by: uuid.UUID
    lease: LeaseHandle
    worker_id: str
    notifier: NotificationPort
    max_steps: int
    faults: FaultInjector = field(default_factory=FaultInjector)
    heartbeat: Heartbeater | None = None
    services: Any = None  # GraphServices for graphs that need gateways (aml-1)
    budget_exempt: frozenset[str] = frozenset()  # bookkeeping nodes that are not model/tool steps

    def fence(self, session: Session) -> None:
        if self.heartbeat is not None and self.heartbeat.lost.is_set():
            raise StaleLeaseError("lease renewal failed")
        leases.assert_current(session, self.lease)

    def _run_row(self, session: Session) -> WorkflowRun:
        return session.scalars(
            select(WorkflowRun)
            .where(WorkflowRun.tenant_id == self.tenant_id, WorkflowRun.id == self.run_id)
            .with_for_update()
        ).one()

    def run_step(
        self, node: str, work: Callable[[], dict[str, Any]], *, iteration: int = 0
    ) -> dict[str, Any]:
        key = f"{node}:{iteration}"
        with transaction(self.factory) as s:
            self.fence(s)
            run = self._run_row(s)
            if run.cancel_requested_at is not None:
                raise RunCancelledError(node)
            step = s.scalar(
                select(WorkflowStep).where(
                    WorkflowStep.tenant_id == self.tenant_id,
                    WorkflowStep.run_id == self.run_id,
                    WorkflowStep.step_key == key,
                )
            )
            if step is not None and step.status == "SUCCEEDED":
                return dict(step.output_ref or {})  # replay: committed result, no repeated work
            done = int(
                s.scalar(
                    select(func.count()).where(
                        WorkflowStep.tenant_id == self.tenant_id,
                        WorkflowStep.run_id == self.run_id,
                        WorkflowStep.status == "SUCCEEDED",
                        WorkflowStep.node.not_in(self.budget_exempt or {""}),
                    )
                )
                or 0
            )
            if step is None and node not in self.budget_exempt and done + 1 > self.max_steps:
                raise StepBudgetExceededError(f"step budget of {self.max_steps} exhausted")
            if step is None:
                step = WorkflowStep(
                    tenant_id=self.tenant_id,
                    run_id=self.run_id,
                    step_key=key,
                    node=node,
                    status="RUNNING",
                    attempt=1,
                    fencing_token=self.lease.token,
                    worker_id=self.worker_id,
                )
                s.add(step)
            else:
                step.status, step.attempt = "RUNNING", step.attempt + 1
                step.fencing_token, step.worker_id, step.error_code = self.lease.token, self.worker_id, None
        self.faults.hit("before_effect", node)
        output = work()
        self.faults.hit("after_effect", node)
        cancelled = False
        with transaction(self.factory) as s:
            self.fence(s)
            run = self._run_row(s)
            step = s.scalars(
                select(WorkflowStep).where(
                    WorkflowStep.tenant_id == self.tenant_id,
                    WorkflowStep.run_id == self.run_id,
                    WorkflowStep.step_key == key,
                )
            ).one()
            step.finished_at = datetime.now(UTC)
            if run.cancel_requested_at is not None:
                step.status, step.error_code = "DISCARDED", "CANCELLED_DURING_STEP"  # NQ-021
                cancelled = True
            else:
                step.status, step.output_ref = "SUCCEEDED", output
        if cancelled:
            raise RunCancelledError(node)
        self.faults.hit("after_commit", node)
        return output
