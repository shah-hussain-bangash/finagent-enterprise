"""Evaluation runner (AER-029..035; prompt §29; AI-D04 thresholds PROPOSED, not approved).

Evaluates the CURRENT configuration (registry, prompts, provider, validators, calculations, retrieval)
on a versioned SYNTHETIC dataset. Capability scenarios go through the real model gateway and the real
deterministic validator; adversarial scenarios script a misbehaving MOCK model to prove the controls
catch it (they are reported NOT_APPLICABLE against a live provider, which cannot be scripted).
Retrieval scenarios query the tenant's approved corpus as the caller (permission-filtered).
The gate compares against PROPOSED thresholds; a pass is never an acceptance decision.
"""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

from sqlalchemy.orm import Session, sessionmaker

from finagent.agents.contracts import (
    AgentInput,
    AgentOutputModel,
    EvidenceBundle,
    EvidenceRef,
    ExecutionContextRef,
    Limits,
)
from finagent.agents.gateway import BudgetExceededError, CallContext, ModelGateway, ModelOutputInvalidError
from finagent.agents.prep import CHECKLIST_VERSION, INDICATOR_VERSION, _indicators, threshold_of
from finagent.agents.providers import MockModelProvider, ModelProvider
from finagent.agents.registry import CONFIG_VERSION, require_enabled
from finagent.agents.validation import ValidationContext, validate_output
from finagent.core.config import Settings
from finagent.core.correlation import get_correlation_id
from finagent.core.errors import AppError, ErrorCode
from finagent.database.models import EvaluationRun
from finagent.database.session import transaction
from finagent.domain.aml_calculations import RULE_VERSION, compute
from finagent.domain.fingerprint import fingerprint
from finagent.evaluation.dataset_control import DatasetControlError, read_dataset
from finagent.evaluation.reporting import build_report, metric_summary
from finagent.security.authorization.policy import Permission, PrincipalContext
from finagent.services.audit_service import writer as audit
from finagent.services.governance_service.config import current_configuration
from finagent.services.knowledge_service import retrieval

HERE = Path(__file__).parent
THRESHOLDS: dict[str, Any] = json.loads((HERE / "thresholds.json").read_text(encoding="utf-8"))


def list_datasets() -> list[str]:
    return sorted(p.stem for p in (HERE / "datasets").glob("*.json"))


def load_dataset(
    name: str,
    *,
    purpose: str = "development",
) -> tuple[dict[str, Any], str]:
    try:
        data, control = read_dataset(HERE / "datasets", name, purpose=purpose)
    except DatasetControlError as exc:
        raise AppError(ErrorCode.SEMANTIC_VALIDATION_FAILED, str(exc), {"field": "dataset"}) from exc
    data["_dataset_control"] = control
    return data, fingerprint(data)


def _facts(
    scn: dict[str, Any], window: dict[str, str], settings: Settings
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    items = [
        {
            "source_transaction_id": t[0],
            "amount": t[1],
            "currency": t[2],
            "direction": t[3],
            "status": t[4],
            "channel": t[5],
            "value_date": t[6],
            "booking_time": f"{t[6]}T09:00:00+04:00",
            "account_ref": "acct-eval-01",
        }
        for t in scn.get("transactions", [])
    ]
    threshold = threshold_of(settings)
    calcs = compute(
        items,
        threshold=threshold,
        window_start=datetime.fromisoformat(window["start"]),
        window_end=datetime.fromisoformat(window["end"]),
        expected_turnover=scn.get("expected_turnover"),
    )
    ev_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"eval:{scn['id']}:transactions"))
    evidence = {ev_id: {"items": items}}
    facts: dict[str, Any] = {
        "calculations": calcs.as_dict(),
        "indicators": _indicators(calcs, threshold, settings),
        "indicator_version": INDICATOR_VERSION,
        "customer": {"customer_type": "INDIVIDUAL"},
    }
    if scn["capability"] == "AG-KYC":
        facts = {
            "checklist": [],
            "checklist_version": CHECKLIST_VERSION,
            "review_date": date.today().isoformat(),
        }
    if scn.get("injected_document_text"):
        facts["untrusted_document_excerpt"] = scn["injected_document_text"]  # data, never instructions
    calc_map = {k: v.as_dict() for k, v in calcs.values.items()}
    return facts, evidence, calc_map


def _input(scn: dict[str, Any], facts: dict[str, Any], evidence: dict[str, Any]) -> AgentInput:
    cap = require_enabled(scn["capability"])
    return AgentInput(
        execution_context=ExecutionContextRef(
            run_id=None,
            step_id=f"eval:{scn['id']}",
            case_id=f"eval-{scn['id']}",
            tenant_ref="evaluation",
            capability_id=cap.capability_id,
            capability_version=cap.version,
            correlation_id=get_correlation_id(),
        ),
        task={"task_type": cap.capability_id, "objective": cap.purpose, "evaluation_scenario": scn["id"]},
        evidence=EvidenceBundle(
            snapshot_id=f"eval-{scn['id']}",
            completeness_status="INCOMPLETE" if scn.get("known_gaps") else "COMPLETE",
            evidence_refs=[
                EvidenceRef(
                    ref=f"ev:{k}",
                    source_type="TRANSACTIONS",
                    source_ref=scn["customer_lineage"],
                    version_no=1,
                    content_hash=fingerprint(v),
                )
                for k, v in evidence.items()
            ],
            known_gaps=list(scn.get("known_gaps", [])),
        ),
        facts=facts,
        policy_configuration={"config_version": CONFIG_VERSION, "calculation_rules": RULE_VERSION},
        limits=Limits(
            max_steps=cap.max_model_calls,
            token_budget=cap.token_budget,
            cost_budget=str(cap.cost_budget),
            deadline_s=cap.timeout_s,
        ),
    )


def _capability(
    factory: sessionmaker[Session],
    settings: Settings,
    live: ModelProvider | None,
    tenant_id: uuid.UUID,
    scn: dict[str, Any],
    window: dict[str, str],
) -> dict[str, Any]:
    scripted = scn.get("script", ["ok"]) != ["ok"]
    if live is not None and scripted:
        return {
            "id": scn["id"],
            "kind": scn["kind"],
            "status": "NOT_APPLICABLE",
            "passed": None,
            "detail": "scripted model fault cannot be injected into a live provider",
        }
    provider = live or MockModelProvider(list(scn.get("script", ["ok"])))
    gw = ModelGateway(
        factory,
        provider,
        settings.model_copy(update={"model_retry_backoff_seconds": 0}),
        sleep=lambda _s: None,
    )
    facts, evidence, calcs = _facts(scn, window, settings)
    gaps = list(scn.get("known_gaps", []))
    ctx = ValidationContext(
        evidence=evidence,
        calculations=calcs,
        citations={},
        gap_codes={g["code"] for g in gaps},
        blocking_gaps=any(g["severity"] == "BLOCKING" for g in gaps),
    )
    try:
        output, _ = gw.invoke(
            scn["capability"],
            _input(scn, facts, evidence),
            AgentOutputModel,
            CallContext(tenant_id, None, None, f"eval:{scn['id']}"),
        )
    except ModelOutputInvalidError:
        return {
            "id": scn["id"],
            "kind": scn["kind"],
            "status": "ESCALATED",
            "passed": False,
            "detail": "invalid output",
        }
    report = validate_output(output, ctx)
    exp = scn["expect"]
    checks: dict[str, bool] = {}
    if "status_in" in exp:
        checks["status"] = report.status in exp["status_in"]
    if "calc_values" in exp:
        accepted_values = {
            cast(list[str], f["evidence_refs"])[0][5:]: f.get("value")
            for f in report.accepted
            if f["claim_type"] == "DETERMINISTIC_RESULT" and f["evidence_refs"]
        }
        checks["calc_values"] = all(
            calcs.get(k, {}).get("value") == v and accepted_values.get(k) == v
            for k, v in exp["calc_values"].items()
        )
    if "indicators_observed" in exp:
        observed = sorted(i["indicator_id"] for i in facts.get("indicators", []) if i["observed"])
        checks["indicators"] = observed == sorted(exp["indicators_observed"])
    if "rejected_max" in exp:
        checks["rejected_max"] = len(report.rejected) <= exp["rejected_max"]
    if "rejected_min" in exp:
        checks["rejected_min"] = len(report.rejected) >= exp["rejected_min"]
    if "forbidden_text" in exp:
        checks["no_forbidden_text_accepted"] = all(
            exp["forbidden_text"] not in cast(str, f["claim"]) for f in report.accepted
        )
    if "escalated" in exp:
        checks["escalated"] = (report.status == "ESCALATED") == exp["escalated"] and not report.accepted
    if "gap_codes" in exp:
        disclosed = {
            r[4:]
            for f in report.accepted
            for r in cast(list[str], f["evidence_refs"])
            if r.startswith("gap:")
        }
        checks["gaps_disclosed"] = set(exp["gap_codes"]) <= disclosed
    return {
        "id": scn["id"],
        "kind": scn["kind"],
        "status": report.status,
        "passed": all(checks.values()),
        "checks": checks,
        "generated_claims": len(output.findings),
        "rejected": report.rejected,
        "boundary_violations": report.boundary_violations,
    }


def _budget(
    factory: sessionmaker[Session],
    settings: Settings,
    tenant_id: uuid.UUID,
    scn: dict[str, Any],
    window: dict[str, str],
) -> dict[str, Any]:
    gw = ModelGateway(
        factory,
        MockModelProvider(),
        settings.model_copy(update={"run_token_budget": scn["run_token_budget"]}),
        sleep=lambda _s: None,
    )
    probe = {"id": "budget-probe", "capability": "AG-AML", "transactions": [], "kind": "budget"}
    probe["customer_lineage"] = scn["customer_lineage"]
    facts, evidence, _ = _facts(probe, window, settings)
    call = CallContext(tenant_id, None, uuid.uuid4(), "eval:budget")
    gw.invoke("AG-AML", _input(probe, facts, evidence), AgentOutputModel, call)
    try:
        gw.invoke("AG-AML", _input(probe, facts, evidence), AgentOutputModel, call)
    except BudgetExceededError as exc:
        return {"id": scn["id"], "kind": "budget", "status": exc.scope, "passed": exc.scope == "RUN_TOKENS"}
    return {"id": scn["id"], "kind": "budget", "status": "NOT_ENFORCED", "passed": False}


def _retrieval(
    session: Session, ctx: PrincipalContext, retriever: retrieval.Retriever, scn: dict[str, Any]
) -> dict[str, Any]:
    out = retrieval.search(
        session,
        ctx,
        retrieval.SearchRequest(
            query=scn["query"], as_of=date.fromisoformat(scn["as_of"]), alert_type=scn["alert_type"], top_k=3
        ),
        retriever,
    )
    exp = scn["expect"]
    ok = out["outcome"] == exp["outcome"]
    if ok and "policy_key" in exp:
        top = out["citations"][0]
        ok = top["policy_key"] == exp["policy_key"] and top["version_label"] == exp["version_label"]
    return {
        "id": scn["id"],
        "kind": "retrieval_abstain" if exp["outcome"] != "ANSWERED" else "retrieval",
        "status": out["outcome"],
        "passed": ok,
    }


def run(
    factory: sessionmaker[Session],
    settings: Settings,
    ctx: PrincipalContext,
    dataset: str,
    retriever: retrieval.Retriever,
    live_provider: ModelProvider | None = None,
    *,
    purpose: str = "development",
) -> dict[str, Any]:
    data, dataset_hash = load_dataset(dataset, purpose=purpose)
    control = data["_dataset_control"]
    for group in ("capability_scenarios", "budget_scenarios", "retrieval_scenarios"):
        for scenario in data[group]:
            scenario["customer_lineage"] = control["lineages"][scenario["id"]]["customer"]
    window = data["window"]
    results = [
        _capability(factory, settings, live_provider, ctx.tenant_id, s, window)
        for s in data["capability_scenarios"]
    ]
    results += [_budget(factory, settings, ctx.tenant_id, s, window) for s in data["budget_scenarios"]]
    if Permission.KNOWLEDGE_SEARCH in ctx.permissions:
        with transaction(factory) as s:
            results += [_retrieval(s, ctx, retriever, scn) for scn in data["retrieval_scenarios"]]
    else:
        results += [
            {
                "id": scn["id"],
                "kind": "retrieval_abstain" if scn["expect"]["outcome"] != "ANSWERED" else "retrieval",
                "status": "OMITTED",
                "passed": None,
                "detail": "KNOWLEDGE_SEARCH permission is required; retrieval was not executed",
            }
            for scn in data["retrieval_scenarios"]
        ]
    scenarios = (
        [("capability", scn) for scn in data["capability_scenarios"]]
        + [("budget", scn) for scn in data["budget_scenarios"]]
        + [("retrieval", scn) for scn in data["retrieval_scenarios"]]
    )
    for result, (scenario_type, scenario) in zip(results, scenarios, strict=True):
        result["scenario_type"] = scenario_type
        result["family"] = control["lineages"][scenario["id"]]["family"]
        result["capability"] = scenario.get("capability", "NOT_APPLICABLE")
        result["expects_calculations"] = scenario_type == "capability" and "calc_values" in scenario.get(
            "expect", {}
        )
    values = {name: metric_summary(results, name)["value"] for name in THRESHOLDS["metrics"]}
    metrics: dict[str, Any] = {}
    for name, threshold in THRESHOLDS["metrics"].items():
        value = values.get(name)
        metrics[name] = {
            "value": value,
            "threshold": threshold,
            "threshold_status": THRESHOLDS["status"],
            "passed": None if value is None else Decimal(str(value)) >= Decimal(str(threshold)),
        }
        metrics[name].update(metric_summary(results, name))
    gate = (
        "INCOMPLETE"
        if any(m["passed"] is None for m in metrics.values())
        else "PASS_PROPOSED"
        if all(m["passed"] for m in metrics.values())
        else "FAIL_PROPOSED"
    )
    config = current_configuration(settings)
    if results:
        results[0]["evaluation_context"] = {
            "configuration": config["content"],
            "dataset_labels": data.get("labels"),
            "dataset_version": data["version"],
            "dataset_split_status": "REGISTERED_" + control["split"].upper(),
            "evaluation_purpose": purpose,
            "dataset_control": control,
            "report_version": "evaluation-report-2",
        }
    with transaction(factory) as s:
        row = EvaluationRun(
            tenant_id=ctx.tenant_id,
            dataset=data["dataset"],
            dataset_version=data["version"],
            dataset_hash=dataset_hash,
            config_hash=config["config_hash"],
            thresholds_version=THRESHOLDS["version"],
            gate=gate,
            metrics=metrics,
            results=results,
            created_by=ctx.principal_id,
        )
        s.add(row)
        s.flush()
        audit.record(
            s,
            tenant_id=ctx.tenant_id,
            action="EVALUATION_RUN_RECORDED",
            object_type="evaluation_run",
            object_id=str(row.id),
            outcome="SUCCESS",
            actor_principal_id=ctx.principal_id,
            actor_kind=ctx.kind.value,
            details={
                "dataset": data["dataset"],
                "version": data["version"],
                "gate": gate,
                "config_hash": config["config_hash"],
            },
        )
        return view(row)


def view(row: EvaluationRun) -> dict[str, Any]:
    return {
        "evaluation_id": str(row.id),
        "dataset": row.dataset,
        "dataset_version": row.dataset_version,
        "dataset_hash": row.dataset_hash,
        "config_hash": row.config_hash,
        "thresholds_version": row.thresholds_version,
        "status": "COMPLETED",
        "gate": row.gate,
        "metrics": row.metrics,
        "results": row.results,
        "report": build_report(row.results),
        "created_at": row.created_at.isoformat(),
        "created_by": str(row.created_by),
        "notice": "SYNTHETIC dataset; PROPOSED thresholds (AI-D04 OPEN) - not an acceptance decision.",
    }
