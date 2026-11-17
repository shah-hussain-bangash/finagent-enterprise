"""Keep observed retrieval weaknesses visible on a separate synthetic challenge set."""

import json
from pathlib import Path

from finagent.evaluation.assistant_retrieval import evaluate_retrieval_suite


def test_challenge_reports_misses_and_irrelevant_matches() -> None:
    path = Path(__file__).resolve().parents[1] / "fixtures" / "assistant-retrieval-challenge-v1.json"
    suite = json.loads(path.read_text(encoding="utf-8"))
    result = evaluate_retrieval_suite(suite)
    assert result["scenario_count"] == 10
    assert result["precision"] == {"numerator": 7, "denominator": 8}
    assert result["recall"] == {"numerator": 7, "denominator": 9}
    by_id = {row["id"]: row for row in result["results"]}
    assert by_id["english-paraphrase"]["relevant_missed"] == 1
    assert by_id["arabic-variant"]["relevant_missed"] == 1
    assert by_id["two-term-decoy"]["irrelevant_returned"] == 1
    assert by_id["no-cross-record-combination"]["irrelevant_returned"] == 0
