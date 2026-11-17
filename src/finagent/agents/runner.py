"""Run one capability through preparation, gateway, validation and recording.

Capabilities receive minimized inputs. This orchestration prepares those inputs,
retrieves policy as the requester and persists validated output with lineage.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import cast

from sqlalchemy.orm import Session, sessionmaker

from finagent.agents.contracts import (
    AgentInput,
    AgentOutputModel,
    EvidenceBundle,
    EvidenceRef,
    ExecutionContextRef,
    Limits,
    ReportDraftModel,
)
from finagent.agents.gateway import (
    BudgetExceededError,
    CallContext,
    ModelGateway,
    ModelOutputInvalidError,
)
from finagent.agents.prep import (
    CHECKLIST_VERSION,
    INDICATOR_VERSION,
    CaseFacts,
    evidence_refs_for,
    minimized_facts,
)
from finagent.agents.registry import (
    CONFIG_VERSION,
    CapabilityUnavailableError,
    require_enabled,
)
from finagent.agents.validation import (
    ValidationContext,
    ValidationReport,
    numbers,
    numbers_in,
    validate_output,
    validate_report,
)
from finagent.core.correlation import get_correlation_id
from finagent.database.models import AgentOutput
from finagent.database.session import transaction
from finagent.domain.aml_calculations import RULE_VERSION
from finagent.security.authorization.policy import PrincipalContext
from finagent.services.audit_service import writer as audit
from finagent.services.knowledge_service import retrieval
from finagent.services.knowledge_service.citations import check_citation

JsonDict = dict[str, object]
CitationChecker = Callable[[JsonDict, str], tuple[bool, list[str]]]

COMPLIANCE_QUERIES = {
    "STRUCTURING_PATTERN": ("repeated cash deposits just below the reporting threshold indicator"),
    "TURNOVER_ABOVE_EXPECTED": ("activity inconsistent with expected turnover review"),
    "LARGE_OUTGOING_TRANSFER": ("large outgoing transfer after cash deposits"),
}

KYC_QUERY = "proof of address must be dated within days of the review"


@dataclass
class CapabilityResult:
    capability_id: str
    status: str
    accepted: list[JsonDict]
    rejected: list[JsonDict]
    validation: JsonDict
    output_id: uuid.UUID | None
    usage: JsonDict
    recommendations: list[str] = field(default_factory=list)
    limitations: JsonDict = field(default_factory=dict)
    sections: list[JsonDict] = field(default_factory=list)
    policy_versions: list[str] = field(default_factory=list)


@dataclass
class RunScope:
    factory: sessionmaker[Session]
    gateway: ModelGateway
    principal: PrincipalContext
    run_id: uuid.UUID | None
    step_id: str
    retriever: retrieval.Retriever


def _input(
    cap_id: str,
    facts: CaseFacts,
    scope: RunScope,
    task: JsonDict,
    data: JsonDict,
) -> AgentInput:
    cap = require_enabled(cap_id)

    refs = cast(
        list[object],
        evidence_refs_for(
            cap_id,
            facts,
            cap.permitted_source_types,
        ),
    )

    return AgentInput(
        execution_context=ExecutionContextRef(
            run_id=str(scope.run_id) if scope.run_id else None,
            step_id=scope.step_id,
            case_id=str(facts.case.id),
            tenant_ref=str(facts.tenant_id),
            capability_id=cap.capability_id,
            capability_version=cap.version,
            correlation_id=get_correlation_id(),
        ),
        task=task,
        evidence=EvidenceBundle(
            snapshot_id=str(facts.snapshot.id),
            completeness_status=facts.snapshot.completeness_status,
            evidence_refs=[EvidenceRef.model_validate(ref) for ref in refs],
            known_gaps=list(facts.snapshot.gaps),
        ),
        facts=data,
        policy_configuration={
            "config_version": CONFIG_VERSION,
            "checklist_version": CHECKLIST_VERSION,
            "indicator_version": INDICATOR_VERSION,
            "calculation_rules": RULE_VERSION,
            "allowed_tools": sorted(cap.permitted_tools),
        },
        limits=Limits(
            max_steps=cap.max_model_calls,
            token_budget=cap.token_budget,
            cost_budget=str(cap.cost_budget),
            deadline_s=cap.timeout_s,
        ),
    )


def _persist(
    scope: RunScope,
    facts: CaseFacts,
    cap_id: str,
    status: str,
    output: JsonDict,
    validation: JsonDict,
    lineage: JsonDict,
) -> uuid.UUID:
    cap_version = require_enabled(cap_id).version if cap_id != "AG-RISK" else "n/a"

    with transaction(scope.factory) as session:
        row = AgentOutput(
            tenant_id=facts.tenant_id,
            case_id=facts.case.id,
            run_id=scope.run_id,
            snapshot_id=facts.snapshot.id,
            capability_id=cap_id,
            capability_version=cap_version,
            status=status,
            output=output,
            validation=validation,
            lineage=lineage,
        )

        session.add(row)
        session.flush()

        _ = audit.record(
            session,
            tenant_id=facts.tenant_id,
            action="AGENT_OUTPUT_RECORDED",
            object_type="agent_output",
            object_id=str(row.id),
            outcome=("SUCCESS" if status in ("SUCCEEDED", "PARTIAL") else "FAILED"),
            actor_principal_id=None,
            actor_kind="SYSTEM",
            case_id=facts.case.id,
            details={
                "capability": cap_id,
                "status": status,
                "accepted": len(cast(list[object], output.get("findings", []))),
                "rejected": len(cast(list[object], validation.get("rejected", []))),
                "boundary_violations": validation.get("boundary_violations", []),
                "snapshot_id": str(facts.snapshot.id),
            },
        )

        return row.id


def _lineage(
    cap_id: str,
    scope: RunScope,
    facts: CaseFacts,
    usage: JsonDict,
    **extra: object,
) -> JsonDict:
    cap = require_enabled(cap_id)

    return {
        "capability_id": cap_id,
        "capability_version": cap.version,
        "prompt_version": cap.prompt_version,
        "prompt_hash": cap.prompt_hash,
        "config_version": CONFIG_VERSION,
        "provider": scope.gateway.provider_name,
        "model_alias": cap.model_alias,
        "snapshot_id": str(facts.snapshot.id),
        "snapshot_version": facts.snapshot.version_no,
        "calculation_rules": RULE_VERSION,
        "checklist_version": CHECKLIST_VERSION,
        **(
            {"document_selection": facts.document_selection}
            if cap_id == "AG-KYC" and facts.document_selection is not None
            else {}
        ),
        "usage": usage,
        **extra,
    }


def _call[OutputModel: (AgentOutputModel, ReportDraftModel)](
    cap_id: str,
    facts: CaseFacts,
    scope: RunScope,
    task: JsonDict,
    data: JsonDict,
    model: type[OutputModel],
) -> tuple[OutputModel, JsonDict] | CapabilityResult:
    try:
        agent_input = _input(
            cap_id,
            facts,
            scope,
            task,
            data,
        )

        ctx = CallContext(
            facts.tenant_id,
            facts.case.id,
            scope.run_id,
            scope.step_id,
        )

        output, usage = scope.gateway.invoke(
            cap_id,
            agent_input,
            model,
            ctx,
        )

        return output, cast(JsonDict, usage.as_dict())

    except CapabilityUnavailableError as exc:
        return CapabilityResult(
            cap_id,
            "FAILED",
            [],
            [],
            {"error": exc.reason},
            None,
            {},
        )

    except ModelOutputInvalidError as exc:
        oid = _persist(
            scope,
            facts,
            cap_id,
            "ESCALATED",
            {"findings": []},
            {
                "error": "INVALID_OUTPUT",
                "errors": exc.errors,
            },
            _lineage(cap_id, scope, facts, {}),
        )

        return CapabilityResult(
            cap_id,
            "ESCALATED",
            [],
            [],
            {"error": "INVALID_OUTPUT"},
            oid,
            {},
        )

    except BudgetExceededError as exc:
        _ = _persist(
            scope,
            facts,
            cap_id,
            "FAILED",
            {"findings": []},
            {
                "error": "BUDGET_EXCEEDED",
                "scope": exc.scope,
            },
            _lineage(cap_id, scope, facts, {}),
        )
        raise


def _finish(
    cap_id: str,
    facts: CaseFacts,
    scope: RunScope,
    output: AgentOutputModel,
    usage: JsonDict,
    report: ValidationReport,
    **lineage: object,
) -> CapabilityResult:
    recommendations = list(output.recommendations) if report.passed else []
    limitations = cast(
        JsonDict,
        output.limitations.model_dump(),
    )

    stored: JsonDict = {
        "findings": report.accepted,
        "limitations": limitations,
        "recommendations": recommendations,
        "model_status": output.status,
    }

    oid = _persist(
        scope,
        facts,
        cap_id,
        report.status,
        stored,
        report.as_dict(),
        _lineage(
            cap_id,
            scope,
            facts,
            usage,
            **lineage,
        ),
    )

    return CapabilityResult(
        cap_id,
        report.status,
        report.accepted,
        report.rejected,
        report.as_dict(),
        oid,
        usage,
        recommendations=recommendations,
        limitations=limitations,
        policy_versions=list(cast(list[str], lineage.get("policy_versions", []))),
    )


def _citation_adapter(
    checker: CitationChecker,
) -> Callable[[JsonDict, str], tuple[bool, list[str]]]:
    def check(
        citation: JsonDict,
        claim: str,
    ) -> tuple[bool, list[str]]:
        return checker(citation, claim)

    return check


def _ctx(
    facts: CaseFacts,
    citations: dict[str, JsonDict] | None = None,
    checker: CitationChecker | None = None,
    extra: set[Decimal] | None = None,
) -> ValidationContext:
    return ValidationContext(
        evidence=cast(
            dict[str, JsonDict],
            facts.evidence_map,
        ),
        calculations={key: cast(JsonDict, value.as_dict()) for key, value in facts.calcs.values.items()},
        citations=citations or {},
        gap_codes=facts.gap_codes,
        blocking_gaps=facts.blocking,
        citation_check=_citation_adapter(checker) if checker is not None else None,
        extra_numbers=extra or set(),
    )


def run_simple(
    cap_id: str,
    facts: CaseFacts,
    scope: RunScope,
) -> CapabilityResult:
    """AG-KYC, AG-AML and CAP-DOCUMENT use deterministic facts."""
    task: JsonDict = {
        "task_type": cap_id,
        "objective": require_enabled(cap_id).purpose,
    }

    result = _call(
        cap_id,
        facts,
        scope,
        task,
        cast(JsonDict, minimized_facts(cap_id, facts)),
        AgentOutputModel,
    )

    if isinstance(result, CapabilityResult):
        return result

    output, usage = result

    extra: set[Decimal] = facts.checklist_numbers if cap_id == "AG-KYC" else set()

    return _finish(
        cap_id,
        facts,
        scope,
        output,
        usage,
        validate_output(
            output,
            _ctx(facts, extra=extra),
        ),
    )


def run_compliance(
    facts: CaseFacts,
    scope: RunScope,
) -> CapabilityResult:
    indicators = cast(list[JsonDict], facts.indicators)

    queries = [
        COMPLIANCE_QUERIES[cast(str, indicator["indicator_id"])]
        for indicator in indicators
        if indicator.get("observed")
    ]
    queries.append(KYC_QUERY)

    citations: dict[str, JsonDict] = {}
    outcomes: list[str] = []
    retrieval_ids: set[uuid.UUID] = set()

    with transaction(scope.factory) as session:
        for query in queries:
            out = cast(
                JsonDict,
                retrieval.search(
                    session,
                    scope.principal,
                    retrieval.SearchRequest(
                        query=query,
                        case_id=facts.case.id,
                        top_k=3,
                    ),
                    scope.retriever,
                    run_id=scope.run_id,
                ),
            )

            outcomes.append(cast(str, out["outcome"]))
            retrieval_ids.add(uuid.UUID(cast(str, out["retrieval_id"])))

            for citation in cast(list[JsonDict], out["citations"]):
                _ = citations.setdefault(
                    cast(str, citation["chunk_id"]),
                    citation,
                )

    if "CONFLICTING_GUIDANCE" in outcomes:
        outcome = "CONFLICTING_GUIDANCE"
    elif citations:
        outcome = "ANSWERED"
    else:
        outcome = "INSUFFICIENT_EVIDENCE"

    data: JsonDict = {
        "retrieval_outcome": outcome,
        "citations": [
            {
                key: citation[key]
                for key in (
                    "chunk_id",
                    "policy_key",
                    "version_label",
                    "section",
                    "heading",
                    "excerpt",
                    "historical",
                )
            }
            for citation in citations.values()
        ],
        "indicators": [indicator["label"] for indicator in indicators if indicator.get("observed")],
    }

    task: JsonDict = {
        "task_type": "AG-COMPLIANCE",
        "objective": require_enabled("AG-COMPLIANCE").purpose,
    }

    result = _call(
        "AG-COMPLIANCE",
        facts,
        scope,
        task,
        data,
        AgentOutputModel,
    )

    if isinstance(result, CapabilityResult):
        return result

    output, usage = result

    def checker(
        citation: JsonDict,
        claim: str,
    ) -> tuple[bool, list[str]]:
        with transaction(scope.factory) as session:
            check = check_citation(
                session,
                scope.principal,
                citation,
                claim,
                retrieval_ids=retrieval_ids,
            )

        return check.valid, check.reasons

    report = validate_output(
        output,
        _ctx(facts, citations, checker),
    )

    if outcome != "ANSWERED" and report.status == "SUCCEEDED":
        report.status = "INSUFFICIENT_EVIDENCE"

    versions = sorted({cast(str, citation["policy_version_id"]) for citation in citations.values()})

    return _finish(
        "AG-COMPLIANCE",
        facts,
        scope,
        output,
        usage,
        report,
        policy_versions=versions,
        retrieval_ids=sorted(str(retrieval_id) for retrieval_id in retrieval_ids),
        retrieval_outcome=outcome,
    )


def run_report(
    facts: CaseFacts,
    scope: RunScope,
    upstream: list[CapabilityResult],
) -> CapabilityResult:
    accepted: dict[str, JsonDict] = {}

    for upstream_result in upstream:
        for finding in upstream_result.accepted:
            key = cast(str, finding["claim_id"])
            accepted[key] = {
                **finding,
                "capability_id": upstream_result.capability_id,
            }

    missing: list[str] = []

    for upstream_result in upstream:
        missing += list(
            cast(
                list[str],
                upstream_result.limitations.get("missing_evidence", []),
            )
        )

    alert = facts.alert

    case_info = {
        "source_alert_id": alert.source_alert_id,
        "alert_type": alert.alert_type,
        "severity": alert.severity,
        "subject_refs": list(alert.subject_refs),
        "window_start": alert.window_start.date().isoformat(),
        "window_end": alert.window_end.date().isoformat(),
    }

    questions = [question for upstream_result in upstream for question in upstream_result.recommendations][
        :10
    ]

    sources = sorted({item.source_type for item in facts.items})
    missing_evidence = sorted(set(missing))[:50]

    data: JsonDict = {
        "case": case_info,
        "accepted_findings": list(accepted.values()),
        "recommendations": questions,
        "missing_evidence": missing_evidence,
        "upstream_statuses": sorted({item.status for item in upstream}),
        "snapshot_version": facts.snapshot.version_no,
        "snapshot_completeness": (facts.snapshot.completeness_status),
        "sources": sources,
    }

    task: JsonDict = {
        "task_type": "AG-REPORT",
        "objective": require_enabled("AG-REPORT").purpose,
    }

    call_result = _call(
        "AG-REPORT",
        facts,
        scope,
        task,
        data,
        ReportDraftModel,
    )

    if isinstance(call_result, CapabilityResult):
        return call_result

    draft, usage = call_result

    allowed = (
        numbers_in(case_info)
        | {Decimal(facts.snapshot.version_no)}
        | numbers_in(questions)
        | numbers_in(missing_evidence)
    )

    trusted_sections = {
        "alert_context": (
            f"Alert {case_info['source_alert_id']} "
            + f"({case_info['alert_type']}, "
            + f"severity {case_info['severity']}) "
            + f"for subject(s) {', '.join(alert.subject_refs)}; "
            + f"window {case_info['window_start']} "
            + f"to {case_info['window_end']}."
        ),
        "evidence_scope": (
            f"Evidence snapshot version {facts.snapshot.version_no} "
            + f"({facts.snapshot.completeness_status}); "
            + f"sources: {', '.join(sources)}."
        ),
    }

    if questions:
        trusted_sections["analyst_questions"] = "\n".join(f"- {question}" for question in questions)

    report = validate_report(
        draft,
        {
            key: {name: value for name, value in finding.items() if name != "capability_id"}
            for key, finding in accepted.items()
        },
        allowed,
        facts.blocking,
        trusted_sections=trusted_sections,
        trusted_limitations={
            "missing_evidence": missing_evidence,
        },
    )

    if report.status == "SUCCEEDED" and any(item.status != "SUCCEEDED" for item in upstream):
        report.status = "PARTIAL"

    retain_content = report.passed and report.status in (
        "SUCCEEDED",
        "PARTIAL",
    )

    sections: list[JsonDict] = (
        [cast(JsonDict, section.model_dump()) for section in draft.sections] if retain_content else []
    )

    limitations: JsonDict = (
        cast(JsonDict, draft.limitations.model_dump())
        if retain_content
        else {
            "missing_evidence": [],
            "contradictions": [],
            "unsupported_claims": [],
            "unresolved_questions": [],
        }
    )

    stored: JsonDict = {
        "sections": sections,
        "findings": report.accepted,
        "limitations": limitations,
        "model_status": draft.status,
    }

    policy_versions = sorted({version for item in upstream for version in item.policy_versions})

    oid = _persist(
        scope,
        facts,
        "AG-REPORT",
        report.status,
        stored,
        report.as_dict(),
        _lineage(
            "AG-REPORT",
            scope,
            facts,
            usage,
            policy_versions=policy_versions,
            upstream_outputs=[str(item.output_id) for item in upstream if item.output_id],
        ),
    )

    return CapabilityResult(
        "AG-REPORT",
        report.status,
        report.accepted,
        report.rejected,
        report.as_dict(),
        oid,
        usage,
        limitations=limitations,
        sections=sections,
        policy_versions=policy_versions,
    )


_ = numbers  # re-exported for callers building allowed-number sets
