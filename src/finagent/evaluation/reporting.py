"""Evaluation counts and descriptive uncertainty; thresholds remain proposed."""

from __future__ import annotations

from math import sqrt
from typing import Any

KINDS = {
    "deterministic_value_accuracy": {"quality"},
    "unsupported_claim_rejection_rate": {"adversarial_reject"},
    "boundary_escalation_rate": {"adversarial_boundary"},
    "gap_disclosure_rate": {"gap"},
    "schema_robustness_rate": {"robustness"},
    "budget_enforcement": {"budget"},
    "rag_applicability_accuracy": {"retrieval"},
    "rag_abstention_accuracy": {"retrieval_abstain"},
}


def metric_summary(results: list[dict[str, Any]], name: str) -> dict[str, Any]:
    relevant = [r for r in results if r["kind"] in KINDS[name]]
    if name == "deterministic_value_accuracy":
        relevant = [
            r for r in relevant if r.get("expects_calculations", "calc_values" in r.get("checks", {}))
        ]
    evaluated = [r for r in relevant if r["passed"] is not None]
    failed = [
        r["id"]
        for r in evaluated
        if not (
            r.get("checks", {}).get("calc_values", False)
            if name == "deterministic_value_accuracy"
            else r["passed"]
        )
    ]
    denominator = len(evaluated)
    numerator = denominator - len(failed)
    interval = None
    if denominator:
        z = 1.959963984540054
        proportion = numerator / denominator
        scale = 1 + z * z / denominator
        centre = (proportion + z * z / (2 * denominator)) / scale
        margin = (
            z
            * sqrt(proportion * (1 - proportion) / denominator + z * z / (4 * denominator * denominator))
            / scale
        )
        interval = [round(max(0.0, centre - margin), 6), round(min(1.0, centre + margin), 6)]
    return {
        "value": round(numerator / denominator, 4) if denominator else None,
        "numerator": numerator,
        "denominator": denominator,
        "failures": len(failed),
        "failure_ids": failed,
        "omitted": sum(r["status"] == "OMITTED" for r in relevant),
        "not_applicable": sum(r["status"] == "NOT_APPLICABLE" for r in relevant),
        "uncertainty": {
            "method": "Wilson 95% interval",
            "interval": interval,
            "limitation": "Descriptive only; related synthetic scenarios are not independent samples.",
        },
    }


def build_report(results: list[dict[str, Any]]) -> dict[str, Any]:
    slices: dict[str, dict[str, Any]] = {}
    for dimension in ("family", "kind", "capability"):
        groups: dict[str, list[dict[str, Any]]] = {}
        for result in results:
            label = str(result.get(dimension, "UNSPECIFIED"))
            groups.setdefault(label, []).append(result)
        slices[dimension] = {
            label: {
                "total": len(rows),
                "passed": sum(r["passed"] is True for r in rows),
                "failed": sum(r["passed"] is False for r in rows),
                "omitted": sum(r["status"] == "OMITTED" for r in rows),
                "not_applicable": sum(r["status"] == "NOT_APPLICABLE" for r in rows),
                "metrics": {name: metric_summary(rows, name) for name in KINDS},
            }
            for label, rows in sorted(groups.items())
        }
    capabilities = [r for r in results if r.get("scenario_type") == "capability"]
    known = bool(capabilities) and all(isinstance(r.get("generated_claims"), int) for r in capabilities)
    generated = sum(r["generated_claims"] for r in capabilities) if known else None
    return {
        "version": "evaluation-report-2",
        "context": results[0].get("evaluation_context", {}) if results else {},
        "slices": slices,
        "support_precision": {
            "value": None,
            "generated_claims": generated,
            "status": "NOT_APPLICABLE" if generated == 0 else "NOT_MEASURED",
            "reason": "zero_generated_claims" if generated == 0 else "independent_support_labels_unavailable",
        },
        "notice": "Scenario pass rates are not independently labelled claim-support precision.",
    }
