"""Phase-4 skeleton graph: proves durable execution end to end WITHOUT agents (prompt §50: no
agents before workflow persistence exists). Phase 9 replaces it with the full AML graph.

    START -> authorize -> record_context -> notify_assignee -> await_review (interrupt)
          -> [decision == CONTINUE] finalize -> END
          -> [decision == STOP]     END

`notify_assignee` is a real externally visible effect (a notification) executed through the
external-operation ledger, so crash/replay tests can prove "no duplicate effect".
"""

from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from sqlalchemy import and_, select

from finagent.database.models import Alert, Case
from finagent.database.session import transaction
from finagent.domain.fingerprint import fingerprint
from finagent.domain.states import CASE_TERMINAL, CaseStatus
from finagent.integrations.notifications.port import DeliveryStatus, Notification, NotificationPort
from finagent.orchestration.runtime import RunContext
from finagent.orchestration.state import RunState
from finagent.services.workflow_service.external_ops import OperationSpec, ProbeResult, execute_once

GRAPH_VERSION = "skeleton-1"
REVIEW_DECISIONS = frozenset({"CONTINUE", "STOP"})


class InvalidResumeError(ValueError):
    """Resume input did not come from the deterministic service contract."""


def _rt(config: RunnableConfig) -> RunContext:
    rt: RunContext = config["configurable"]["finagent_rt"]
    return rt


class NotificationEffect:
    target_system = "notifications"

    def __init__(self, notifier: NotificationPort, rt: RunContext, template: str) -> None:
        self._n, self._rt, self._template = notifier, rt, template

    def apply(self, operation_key: str) -> str | None:
        self._n.send(
            Notification(
                tenant_id=self._rt.tenant_id,
                idempotency_key=operation_key,
                recipient_role="ANALYST",
                template=self._template,
                resource_ref=str(self._rt.case_id),
            )
        )
        return None

    def probe(self, operation_key: str) -> ProbeResult:
        status = self._n.status(self._rt.tenant_id, operation_key)
        return {
            DeliveryStatus.DELIVERED: ProbeResult.APPLIED,
            DeliveryStatus.NOT_FOUND: ProbeResult.NOT_FOUND,
        }.get(status, ProbeResult.UNKNOWN)


def authorize(state: RunState, config: RunnableConfig) -> dict[str, Any]:
    rt = _rt(config)

    def work() -> dict[str, Any]:
        with transaction(rt.factory) as s:
            case = s.scalar(select(Case).where(Case.tenant_id == rt.tenant_id, Case.id == rt.case_id))
            if case is None or CaseStatus(case.status) in CASE_TERMINAL:
                raise PermissionError("case no longer eligible for investigation")
            return {"case_status": case.status}

    rt.run_step("authorize", work)
    return {"completed": ["authorize"]}


def record_context(state: RunState, config: RunnableConfig) -> dict[str, Any]:
    rt = _rt(config)

    def work() -> dict[str, Any]:
        with transaction(rt.factory) as s:
            alert = s.execute(
                select(Alert)
                .join(Case, and_(Case.tenant_id == Alert.tenant_id, Case.alert_id == Alert.id))
                .where(Case.tenant_id == rt.tenant_id, Case.id == rt.case_id)
            ).scalar_one()
            return {  # references only (prompt §7)
                "alert_id": str(alert.id),
                "window_start": alert.window_start.isoformat(),
                "window_end": alert.window_end.isoformat(),
                "subject_count": len(alert.subject_refs),
            }

    ref = rt.run_step("record_context", work)
    return {"completed": ["record_context"], "context_ref": ref}


def notify_assignee(state: RunState, config: RunnableConfig) -> dict[str, Any]:
    rt = _rt(config)

    def work() -> dict[str, Any]:
        key = f"run:{rt.run_id}:notify-started"
        op = execute_once(
            rt.factory,
            OperationSpec(
                tenant_id=rt.tenant_id,
                operation_type="NOTIFICATION",
                operation_key=key,
                request_hash=fingerprint({"template": "workflow.started", "case": str(rt.case_id)}),
                case_id=rt.case_id,
                run_id=rt.run_id,
            ),
            NotificationEffect(rt.notifier, rt, "workflow.started"),
            lease=rt.lease,
            fault=lambda point: rt.faults.hit(point, "notify_assignee"),
        )
        return {"operation_id": str(op.id)}

    out = rt.run_step("notify_assignee", work)
    return {"completed": ["notify_assignee"], "notification_operation_id": out["operation_id"]}


def await_review(state: RunState, config: RunnableConfig) -> dict[str, Any]:
    rt = _rt(config)
    value = interrupt({"waiting_for": "REVIEW", "run_id": str(rt.run_id)})
    decision = value.get("decision") if isinstance(value, dict) else None
    if decision not in REVIEW_DECISIONS:
        raise InvalidResumeError("resume decision is not a permitted value")
    rt.run_step("review_received", lambda: {"decision": decision})
    return {"completed": ["await_review"], "review_decision": decision}


def finalize(state: RunState, config: RunnableConfig) -> dict[str, Any]:
    rt = _rt(config)
    rt.run_step("finalize", lambda: {"outcome": "COMPLETED_SKELETON"})
    return {"completed": ["finalize"], "outcome": "COMPLETED_SKELETON"}


def _route_after_review(state: RunState) -> str:
    return "finalize" if state.get("review_decision") == "CONTINUE" else END


def build(checkpointer: BaseCheckpointSaver[Any]) -> Any:
    graph = StateGraph(RunState)
    graph.add_node("authorize", authorize)
    graph.add_node("record_context", record_context)
    graph.add_node("notify_assignee", notify_assignee)
    graph.add_node("await_review", await_review)
    graph.add_node("finalize", finalize)
    graph.add_edge(START, "authorize")
    graph.add_edge("authorize", "record_context")
    graph.add_edge("record_context", "notify_assignee")
    graph.add_edge("notify_assignee", "await_review")
    graph.add_conditional_edges("await_review", _route_after_review, {"finalize": "finalize", END: END})
    graph.add_edge("finalize", END)
    return graph.compile(checkpointer=checkpointer)
