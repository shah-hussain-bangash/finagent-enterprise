"""Report known retrieval limitations instead of concealing missed evidence."""

import copy
import json
from pathlib import Path

from finagent.evaluation.assistant_retrieval import evaluate_retrieval_suite


def test_synthetic_retrieval_suite_counts_false_positives_and_misses() -> None:
    path = Path(__file__).resolve().parents[1] / "fixtures" / "assistant-retrieval-v1.json"
    suite = json.loads(path.read_text(encoding="utf-8"))
    original = copy.deepcopy(suite)
    result = evaluate_retrieval_suite(suite)
    assert suite == original
    assert result["scenario_count"] == 6
    assert result["precision"] == {"numerator": 3, "denominator": 4}
    assert result["recall"] == {"numerator": 3, "denominator": 5}
    by_id = {row["id"]: row for row in result["results"]}
    assert by_id["cross-language"]["relevant_missed"] == 1
    assert by_id["split-field"]["relevant_missed"] == 1
    assert by_id["broad-query"]["irrelevant_returned"] == 1
    assert "no model called" in result["notice"]
    assert len(result["dataset_sha256"]) == 64
