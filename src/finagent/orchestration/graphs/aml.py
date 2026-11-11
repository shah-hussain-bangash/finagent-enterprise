"""AML investigation graph (prompt §6, §7; FR-005, FR-012..030; ADR-0004/0015).

    START -> authorize -> gather_evidence -> process_documents -> freeze_snapshot
          -> [BLOCKING gaps] await_evidence
                             -> EVIDENCE_SUPPLIED -> gather_evidence (next round)
          -> analyze_kyc -> analyze_aml -> analyze_documents -> analyze_compliance
          -> [authority boundary] -> escalated END (run HUMAN_REVIEW)
          -> draft_report -> notify_draft_ready
          -> await_review
               APPROVE        -> finalize -> END
               INFO_REQUESTED -> await_evidence
               REANALYZE      -> gather_evidence (next round)
               CLOSE          -> END

Every node's work goes through rt.run_step, so it is fenced, replay-safe and
cancellable at step boundaries. State holds references only. Interrupts are
resumed only by deterministic services, never by model output. Model/tool
steps count toward the step budget; bookkeeping steps do not.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import timedelta
from typing import Protocol, cast

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.constants import END, START
from langgraph.graph.state import CompiledStateGraph, StateGraph
from langgraph.types import interrupt
from sqlalchemy import and_, select

from finagent.agents.prep import CaseFacts, prepare
from finagent.agents.runner import (
    CapabilityResult,
    RunScope,
    run_compliance,
    run_report,
    run_simple,
)
from finagent.database.models import AgentOutput, Alert, Case
from finagent.database.session import transaction
from finagent.domain.fingerprint import fingerprint
from finagent.domain.states import CASE_TERMINAL, CaseStatus
from finagent.integrations.tool_gateway.gateway import ExecutionContext
from finagent.orchestration.runtime import RunContext
from finagent.orchestration.services import GraphServices
from finagent.orchestration.state import RunState
from finagent.security.authentication.resolver import resolve_principal_by_id
from finagent.security.authorization.policy import PrincipalContext
from finagent.services.case_service.transitions import Actor, change_status
from finagent.services.evidence_service.gathering import (
    gather_case_evidence,
    process_case_documents,
)
from finagent.services.evidence_service.snapshots import create_snapshot
from finagent.services.review_service.finalization import finalize as finalize_case
from finagent.services.review_service.reports import create_ai_draft
from finagent.services.workflow_service.external_ops import (
    OperationSpec,
    execute_once,
)

GRAPH_VERSION = "aml-1"

BUDGET_EXEMPT = frozenset(
    {
        "authorize",
        "process_documents",
        "freeze_snapshot",
        "block_case",
        "notify_draft_ready",
        "review_received",
        "evidence_received",
        "finalize",
    }
)

GATHER_TOOLS = frozenset(
    {
        "get_customer_profile",
        "list_kyc_documents",
        "get_transactions",
        "get_sanctions_summary",
        "fetch_kyc_document",
    }
)

REVIEW_DECISIONS = frozenset({"APPROVE", "INFO_REQUESTED", "REANALYZE", "CLOSE"})
EVIDENCE_DECISIONS = frozenset({"EVIDENCE_SUPPLIED", "CLOSE"})


class InvalidResumeError(ValueError):
    pass


class AgentEscalationError(Exception):
    """A capability crossed an authority boundary: hand to a human."""


def _rt(config: RunnableConfig) -> RunContext:
    configurable = config.get("configurable")
    if configurable is None:
        raise RuntimeError("workflow configurable context is missing")

    runtime: object = configurable.get("finagent_rt")
    if not isinstance(runtime, RunContext):
        raise RuntimeError("workflow RunContext is missing or invalid")

    return runtime


def _snapshot_id(state: RunState) -> str:
    snapshot_id = state.get("snapshot_id")
    if snapshot_id is None:
        raise KeyError("snapshot_id")
    return snapshot_id


def _step_output(
    rt: RunContext,
    node: str,
    work: Callable[[], dict[str, object]],
    *,
    iteration: int = 0,
) -> dict[str, object]:
    return cast(
        dict[str, object],
        rt.run_step(node, work, iteration=iteration),
    )


def _agent_outputs(state: RunState) -> dict[str, dict[str, object]]:
    return cast(
        dict[str, dict[str, object]],
        state.get("agent_outputs", {}),
    )


def _resume_payload(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return cast(dict[str, object], value)
    return {}


def _fault_callback(rt: RunContext, target: str) -> Callable[[str], None]:
    def callback(point: str) -> None:
        rt.faults.hit(point, target)

    return callback


def _svc(rt: RunContext) -> GraphServices:
    services = cast(GraphServices | None, rt.services)
    if services is None:
        raise RuntimeError("graph services not configured for this worker")
    return services


def _requester(rt: RunContext) -> PrincipalContext:
    with transaction(rt.factory) as s:
        # Resolve current rights for every operation.
        return resolve_principal_by_id(
            s,
            rt.tenant_id,
            rt.requested_by,
        )


def authorize(
    state: RunState,
    config: RunnableConfig,
) -> dict[str, object]:
    rt = _rt(config)

    def work() -> dict[str, object]:
        with transaction(rt.factory) as s:
            case = s.scalar(
                select(Case).where(
                    Case.tenant_id == rt.tenant_id,
                    Case.id == rt.case_id,
                )
            )

            if case is None or CaseStatus(case.status) in CASE_TERMINAL:
                raise PermissionError("case no longer eligible for investigation")

            return {"case_status": case.status}

    _ = rt.run_step("authorize", work)

    return {
        "completed": ["authorize"],
        "evidence_round": state.get("evidence_round", 0),
    }


def gather_evidence(
    state: RunState,
    config: RunnableConfig,
) -> dict[str, object]:
    rt, rnd = _rt(config), state.get("evidence_round", 0)

    def work() -> dict[str, object]:
        svc = _svc(rt)

        with transaction(rt.factory) as s:
            alert = s.execute(
                select(Alert)
                .join(
                    Case,
                    and_(
                        Case.tenant_id == Alert.tenant_id,
                        Case.alert_id == Alert.id,
                    ),
                )
                .where(
                    Case.tenant_id == rt.tenant_id,
                    Case.id == rt.case_id,
                )
            ).scalar_one()
            subjects = list(alert.subject_refs)

        ctx = ExecutionContext(
            tenant_id=rt.tenant_id,
            case_id=rt.case_id,
            run_id=rt.run_id,
            step_id=f"gather_evidence:{rnd}",
            capability_id="SVC-EVIDENCE",
            principal=_requester(rt),
            allowed_tools=GATHER_TOOLS,
            data_classes=frozenset({"CONFIDENTIAL", "RESTRICTED"}),
        )

        return gather_case_evidence(
            rt.factory,
            svc.tool_gateway,
            ctx,
            subjects,
            svc.ports,
        ).as_dict()

    out = _step_output(
        rt,
        "gather_evidence",
        work,
        iteration=rnd,
    )

    return {
        "completed": ["gather_evidence"],
        "gather_summary": {
            "failures": len(cast(list[object], out.get("failures", []))),
        },
    }


def process_documents(
    state: RunState,
    config: RunnableConfig,
) -> dict[str, object]:
    rt, rnd = _rt(config), state.get("evidence_round", 0)

    def work() -> dict[str, object]:
        svc = _svc(rt)

        return cast(
            dict[str, object],
            process_case_documents(
                rt.factory,
                svc.ports.store,
                svc.processing,
                rt.tenant_id,
                rt.case_id,
            ),
        )

    _ = rt.run_step(
        "process_documents",
        work,
        iteration=rnd,
    )

    return {"completed": ["process_documents"]}


def freeze_snapshot(
    state: RunState,
    config: RunnableConfig,
) -> dict[str, object]:
    rt, rnd = _rt(config), state.get("evidence_round", 0)

    def work() -> dict[str, object]:
        max_age = timedelta(hours=_svc(rt).settings.source_max_age_hours)

        with transaction(rt.factory) as s:
            snap, _ = create_snapshot(
                s,
                tenant_id=rt.tenant_id,
                case_id=rt.case_id,
                created_by=f"workflow:{rt.run_id}",
                max_age=max_age,
                run_id=rt.run_id,
            )

            blocking = [
                gap for gap in cast(list[dict[str, object]], snap.gaps) if gap["severity"] == "BLOCKING"
            ]

            return {
                "snapshot_id": str(snap.id),
                "completeness": snap.completeness_status,
                "blocking": [f"{gap['code']}:{gap['source_type']}" for gap in blocking],
            }

    out = _step_output(
        rt,
        "freeze_snapshot",
        work,
        iteration=rnd,
    )

    return {
        "completed": ["freeze_snapshot"],
        "snapshot_id": out["snapshot_id"],
        "blocking": bool(out["blocking"]),
        "blocking_reasons": out["blocking"],
    }


def await_evidence(
    state: RunState,
    config: RunnableConfig,
) -> dict[str, object]:
    rt, rnd = _rt(config), state.get("evidence_round", 0)

    def block() -> dict[str, object]:
        with transaction(rt.factory) as s:
            rt.fence(s)

            case = s.scalar(
                select(Case)
                .where(
                    Case.tenant_id == rt.tenant_id,
                    Case.id == rt.case_id,
                )
                .with_for_update()
            )

            if case is not None and case.status in (
                CaseStatus.INVESTIGATING.value,
                CaseStatus.REWORK.value,
            ):
                reason = "missing or incomplete evidence: " + ", ".join(state.get("blocking_reasons", []))

                change_status(
                    s,
                    case,
                    CaseStatus.BLOCKED,
                    Actor.system(f"workflow:{rt.run_id}"),
                    reason[:300],
                )

                case.blocked_reason = reason[:300]
                case.blocked_owner_role = "ANALYST"

            return {
                "case_status": case.status if case else None,
            }

    _ = rt.run_step(
        "block_case",
        block,
        iteration=rnd,
    )

    value = _resume_payload(
        cast(
            object,
            interrupt(
                {
                    "waiting_for": "EVIDENCE",
                    "run_id": str(rt.run_id),
                }
            ),
        )
    )
    decision = value.get("decision")

    if decision not in EVIDENCE_DECISIONS:
        raise InvalidResumeError("evidence resume decision is not a permitted value")

    _ = rt.run_step(
        "evidence_received",
        lambda: {"decision": decision},
        iteration=rnd,
    )

    return {
        "completed": ["await_evidence"],
        "evidence_decision": decision,
        "evidence_round": rnd + 1,
    }


def _scope(rt: RunContext, step: str) -> RunScope:
    svc = _svc(rt)

    return RunScope(
        rt.factory,
        svc.model_gateway,
        _requester(rt),
        rt.run_id,
        step,
        svc.retriever,
    )


def _facts(rt: RunContext, snapshot_id: str) -> CaseFacts:
    with transaction(rt.factory) as s:
        facts = prepare(
            s,
            rt.tenant_id,
            rt.case_id,
            uuid.UUID(snapshot_id),
            _svc(rt).settings,
        )
        s.expunge_all()

    return facts


def _summary(r: CapabilityResult) -> dict[str, object]:
    return {
        "output_id": str(r.output_id) if r.output_id else None,
        "status": r.status,
        "boundary": r.validation.get("boundary_violations", []),
    }


class AnalysisNode(Protocol):
    def __call__(
        self,
        state: RunState,
        config: RunnableConfig,
    ) -> dict[str, object]: ...


def _analyze(cap: str, node: str) -> AnalysisNode:
    def node_fn(
        state: RunState,
        config: RunnableConfig,
    ) -> dict[str, object]:
        rt, rnd = _rt(config), state.get("evidence_round", 0)

        def work() -> dict[str, object]:
            facts = _facts(rt, _snapshot_id(state))
            scope = _scope(rt, f"{node}:{rnd}")

            result = run_compliance(facts, scope) if cap == "AG-COMPLIANCE" else run_simple(cap, facts, scope)

            return _summary(result)

        out = _step_output(
            rt,
            node,
            work,
            iteration=rnd,
        )
        outputs = dict(_agent_outputs(state))
        outputs[cap] = out

        return {
            "completed": [node],
            "agent_outputs": outputs,
        }

    return node_fn


def _result_from_row(row: AgentOutput) -> CapabilityResult:
    output = cast(dict[str, object], row.output)
    validation = cast(dict[str, object], row.validation)
    lineage = cast(dict[str, object], row.lineage)
    return CapabilityResult(
        capability_id=row.capability_id,
        status=row.status,
        accepted=list(cast(list[dict[str, object]], output.get("findings", []))),
        rejected=list(cast(list[dict[str, object]], validation.get("rejected", []))),
        validation=validation,
        output_id=row.id,
        usage=dict(cast(dict[str, object], lineage.get("usage", {}))),
        recommendations=list(cast(list[str], output.get("recommendations", []))),
        limitations=dict(cast(dict[str, object], output.get("limitations", {}))),
        policy_versions=list(cast(list[str], lineage.get("policy_versions", []))),
    )


def draft_report(
    state: RunState,
    config: RunnableConfig,
) -> dict[str, object]:
    rt, rnd = _rt(config), state.get("evidence_round", 0)

    def work() -> dict[str, object]:
        ids = [
            uuid.UUID(cast(str, value["output_id"]))
            for value in _agent_outputs(state).values()
            if value.get("output_id")
        ]

        with transaction(rt.factory) as s:
            rows = s.scalars(
                select(AgentOutput).where(
                    AgentOutput.tenant_id == rt.tenant_id,
                    AgentOutput.id.in_(ids),
                )
            ).all()

            upstream = [_result_from_row(row) for row in rows]
            s.expunge_all()

        facts = _facts(rt, _snapshot_id(state))

        result = run_report(
            facts,
            _scope(rt, f"draft_report:{rnd}"),
            upstream,
        )

        if result.status == "FAILED" or not result.sections:
            raise AgentEscalationError("report draft failed validation")

        content = {
            "sections": result.sections,
            "findings": result.accepted,
            "limitations": result.limitations,
        }

        lineage = {
            "report_output_id": str(result.output_id),
            "upstream_outputs": [str(item) for item in ids],
            "policy_versions": result.policy_versions,
            "run_id": str(rt.run_id),
            "snapshot_id": _snapshot_id(state),
        }

        with transaction(rt.factory) as s:
            rt.fence(s)

            version = create_ai_draft(
                s,
                tenant_id=rt.tenant_id,
                case_id=rt.case_id,
                snapshot_id=uuid.UUID(_snapshot_id(state)),
                snapshot_version=facts.snapshot.version_no,
                content=content,
                lineage=lineage,
            )

            return {
                "report_version_id": str(version.id),
                "version_no": version.version_no,
                "status": result.status,
            }

    out = _step_output(
        rt,
        "draft_report",
        work,
        iteration=rnd,
    )

    return {
        "completed": ["draft_report"],
        "report_version_id": out["report_version_id"],
    }


def check_escalation(state: RunState) -> str:
    boundary = [cap for cap, value in _agent_outputs(state).items() if value.get("boundary")]

    return "escalate" if boundary else "draft_report"


def escalate(
    state: RunState,
    config: RunnableConfig,
) -> dict[str, object]:
    _ = config
    caps = sorted(cap for cap, value in _agent_outputs(state).items() if value.get("boundary"))

    raise AgentEscalationError(f"authority boundary crossed by {', '.join(caps)}")


def notify_draft_ready(
    state: RunState,
    config: RunnableConfig,
) -> dict[str, object]:
    rt = _rt(config)

    from finagent.orchestration.graphs.skeleton import NotificationEffect

    version_id = state.get("report_version_id", "")

    def work() -> dict[str, object]:
        op = execute_once(
            rt.factory,
            OperationSpec(
                tenant_id=rt.tenant_id,
                operation_type="NOTIFICATION",
                operation_key=(f"run:{rt.run_id}:draft:{version_id}"),
                request_hash=fingerprint(
                    {
                        "template": "report.draft_ready",
                        "version": version_id,
                    }
                ),
                case_id=rt.case_id,
                run_id=rt.run_id,
            ),
            NotificationEffect(
                rt.notifier,
                rt,
                "report.draft_ready",
            ),
            lease=rt.lease,
            fault=_fault_callback(rt, "notify_draft_ready"),
        )

        return {"operation_id": str(op.id)}

    _ = rt.run_step(
        "notify_draft_ready",
        work,
        iteration=state.get("evidence_round", 0),
    )

    return {"completed": ["notify_draft_ready"]}


def await_review(
    state: RunState,
    config: RunnableConfig,
) -> dict[str, object]:
    rt = _rt(config)

    value = _resume_payload(
        cast(
            object,
            interrupt(
                {
                    "waiting_for": "REVIEW",
                    "run_id": str(rt.run_id),
                }
            ),
        )
    )
    decision = value.get("decision")

    if decision not in REVIEW_DECISIONS:
        raise InvalidResumeError("review resume decision is not a permitted value")

    rnd = state.get("review_round", 0)
    approval = value.get("approval_request_id")

    _ = rt.run_step(
        "review_received",
        lambda: {
            "decision": decision,
            "approval_request_id": approval,
        },
        iteration=rnd,
    )

    out: dict[str, object] = {
        "completed": ["await_review"],
        "review_decision": decision,
        "review_round": rnd + 1,
        "approval_request_id": approval,
    }

    if decision == "REANALYZE":
        out["evidence_round"] = state.get("evidence_round", 0) + 1

    return out


def finalize(
    state: RunState,
    config: RunnableConfig,
) -> dict[str, object]:
    rt = _rt(config)

    def work() -> dict[str, object]:
        svc = _svc(rt)

        return finalize_case(
            rt.factory,
            tenant_id=rt.tenant_id,
            case_id=rt.case_id,
            approval_request_id=uuid.UUID(str(state.get("approval_request_id"))),
            run_id=rt.run_id,
            gateway=svc.tool_gateway,
            settings=svc.settings,
            lease=rt.lease,
            fault=_fault_callback(rt, "finalize"),
        )

    out = _step_output(
        rt,
        "finalize",
        work,
        iteration=state.get("review_round", 0),
    )

    return {
        "completed": ["finalize"],
        "outcome": out["status"],
    }


def _after_snapshot(state: RunState) -> str:
    return "await_evidence" if state.get("blocking") else "analyze_kyc"


def _after_evidence(state: RunState) -> str:
    return END if state.get("evidence_decision") == "CLOSE" else "gather_evidence"


def _after_review(state: RunState) -> str:
    return {
        "APPROVE": "finalize",
        "INFO_REQUESTED": "await_evidence",
        "REANALYZE": "gather_evidence",
    }.get(
        str(state.get("review_decision")),
        END,
    )


def _after_finalize(state: RunState) -> str:
    # Revalidation failure invalidated the approval: await the next decision.
    return "await_review" if state.get("outcome") == "REVALIDATION_FAILED" else END


class _GraphBuilder(Protocol):
    def add_node(
        self,
        node: str,
        action: AnalysisNode,
    ) -> object: ...

    def add_edge(
        self,
        start_key: str,
        end_key: str,
    ) -> object: ...

    def add_conditional_edges(
        self,
        source: str,
        path: Callable[[RunState], str],
        path_map: list[str],
    ) -> object: ...

    def compile[V: (str, int, float)](
        self,
        checkpointer: BaseCheckpointSaver[V],
    ) -> CompiledStateGraph[RunState, None, RunState, RunState]: ...


def build[V: (str, int, float)](
    checkpointer: BaseCheckpointSaver[V],
) -> CompiledStateGraph[RunState, None, RunState, RunState]:

    g = cast(_GraphBuilder, cast(object, StateGraph(RunState)))

    _ = g.add_node("authorize", authorize)
    _ = g.add_node("gather_evidence", gather_evidence)
    _ = g.add_node("process_documents", process_documents)
    _ = g.add_node("freeze_snapshot", freeze_snapshot)
    _ = g.add_node("await_evidence", await_evidence)

    _ = g.add_node(
        "analyze_kyc",
        _analyze("AG-KYC", "analyze_kyc"),
    )
    _ = g.add_node(
        "analyze_aml",
        _analyze("AG-AML", "analyze_aml"),
    )
    _ = g.add_node(
        "analyze_documents",
        _analyze("CAP-DOCUMENT", "analyze_documents"),
    )
    _ = g.add_node(
        "analyze_compliance",
        _analyze("AG-COMPLIANCE", "analyze_compliance"),
    )

    _ = g.add_node("escalate", escalate)
    _ = g.add_node("draft_report", draft_report)
    _ = g.add_node("notify_draft_ready", notify_draft_ready)
    _ = g.add_node("await_review", await_review)
    _ = g.add_node("finalize", finalize)

    _ = g.add_edge(START, "authorize")
    _ = g.add_edge("authorize", "gather_evidence")
    _ = g.add_edge("gather_evidence", "process_documents")
    _ = g.add_edge("process_documents", "freeze_snapshot")

    _ = g.add_conditional_edges(
        "freeze_snapshot",
        _after_snapshot,
        ["await_evidence", "analyze_kyc"],
    )
    _ = g.add_conditional_edges(
        "await_evidence",
        _after_evidence,
        ["gather_evidence", END],
    )

    _ = g.add_edge("analyze_kyc", "analyze_aml")
    _ = g.add_edge("analyze_aml", "analyze_documents")
    _ = g.add_edge("analyze_documents", "analyze_compliance")

    _ = g.add_conditional_edges(
        "analyze_compliance",
        check_escalation,
        ["escalate", "draft_report"],
    )

    _ = g.add_edge("draft_report", "notify_draft_ready")
    _ = g.add_edge("notify_draft_ready", "await_review")

    _ = g.add_conditional_edges(
        "await_review",
        _after_review,
        ["finalize", "await_evidence", "gather_evidence", END],
    )
    _ = g.add_conditional_edges(
        "finalize",
        _after_finalize,
        ["await_review", END],
    )

    _ = g.add_edge("escalate", END)

    return g.compile(checkpointer=checkpointer)
