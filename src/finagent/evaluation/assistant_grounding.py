"""Synthetic regression scoring for the experimental excerpt validator, not a release gate."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import ValidationError

from finagent.agents.assistant_grounding import ExtractiveDraft, validate_extractive_draft


def evaluate_grounding_suite(suite: dict[str, Any]) -> dict[str, Any]:
    """Score supplied synthetic drafts. Does not call or evaluate a model."""
    encoded = json.dumps(suite, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    scenarios = suite["scenarios"]
    results = []
    families: dict[str, dict[str, int]] = {}
    for scenario in scenarios:
        try:
            draft = ExtractiveDraft.model_validate(scenario["draft"])
        except ValidationError:
            actual = "SCHEMA_INVALID"
        else:
            checked = validate_extractive_draft(draft, scenario["retrieved"])
            actual = str(checked["status"] if checked["accepted"] else checked["reason"])
        passed = actual == scenario["expected"]
        family = scenario["family"]
        counts = families.setdefault(family, {"passed": 0, "total": 0})
        counts["total"] += 1
        counts["passed"] += int(passed)
        results.append(
            {
                "id": scenario["id"],
                "family": family,
                "expected": scenario["expected"],
                "actual": actual,
                "passed": passed,
            }
        )
    return {
        "dataset": suite["dataset"],
        "version": suite["version"],
        "dataset_sha256": hashlib.sha256(encoded).hexdigest(),
        "passed": sum(result["passed"] for result in results),
        "total": len(results),
        "families": families,
        "results": results,
        "notice": "Synthetic validator regression only; no model was called and this is not a release gate "
        "or security certification.",
    }
