"""Regression dataset remains synthetic and reports counts without claiming model quality."""

import copy
import json
from pathlib import Path

from finagent.evaluation.assistant_grounding import evaluate_grounding_suite


def test_synthetic_suite_scores_and_reports_its_limits() -> None:
    path = Path(__file__).resolve().parents[1] / "fixtures" / "assistant-grounding-v1.json"
    suite = json.loads(path.read_text(encoding="utf-8"))
    original = copy.deepcopy(suite)
    result = evaluate_grounding_suite(suite)
    assert suite == original
    assert result["passed"] == result["total"] == 12
    assert result["families"]["bilingual"] == {"passed": 2, "total": 2}
    assert len(result["dataset_sha256"]) == 64
    assert "no model was called" in result["notice"]
    altered = copy.deepcopy(suite)
    altered["scenarios"][0]["expected"] = "EXCERPT_NOT_IN_SOURCE"
    changed = evaluate_grounding_suite(altered)
    assert changed["passed"] == 11
    assert changed["dataset_sha256"] != result["dataset_sha256"]
