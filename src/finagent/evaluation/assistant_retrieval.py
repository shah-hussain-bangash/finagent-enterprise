"""Synthetic retrieval evaluation for the disabled assistant; not a release gate."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from finagent.agents.assistant_retrieval import retrieve_snapshot_excerpts


def evaluate_retrieval_suite(suite: dict[str, Any]) -> dict[str, Any]:
    """Compare retrieved evidence references with hand-authored synthetic labels.

    Reference overlap measures retrieval only; it does not measure answer quality, evidence
    truth, model behavior, permission enforcement or deployment readiness.
    """
    encoded = json.dumps(suite, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    true_positive = false_positive = false_negative = 0
    results: list[dict[str, Any]] = []
    for scenario in suite["scenarios"]:
        response = retrieve_snapshot_excerpts({"items": scenario["items"]}, scenario["question"])
        observed = {match["evidence_ref"] for match in response["matches"]}
        expected = set(scenario["relevant_refs"])
        found, extra, missed = observed & expected, observed - expected, expected - observed
        true_positive += len(found)
        false_positive += len(extra)
        false_negative += len(missed)
        results.append(
            {
                "id": scenario["id"],
                "family": scenario["family"],
                "relevant_found": len(found),
                "irrelevant_returned": len(extra),
                "relevant_missed": len(missed),
                "search_limited": response["search_limited"],
            }
        )
    precision_denominator = true_positive + false_positive
    recall_denominator = true_positive + false_negative
    return {
        "dataset": suite["dataset"],
        "version": suite["version"],
        "dataset_sha256": hashlib.sha256(encoded).hexdigest(),
        "scenario_count": len(results),
        "precision": {"numerator": true_positive, "denominator": precision_denominator},
        "recall": {"numerator": true_positive, "denominator": recall_denominator},
        "results": results,
        "notice": "Synthetic, single-author retrieval labels; no model called. Not an acceptance gate "
        "or semantic-quality certification.",
    }
