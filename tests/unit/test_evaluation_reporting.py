from finagent.evaluation.reporting import build_report, metric_summary


def test_metric_counts_include_failed_and_excluded_scenarios() -> None:
    rows = [
        {"id": "a", "kind": "retrieval", "status": "ANSWERED", "passed": True},
        {"id": "b", "kind": "retrieval", "status": "FAILED", "passed": False},
        {"id": "c", "kind": "retrieval", "status": "OMITTED", "passed": None},
        {"id": "d", "kind": "retrieval", "status": "NOT_APPLICABLE", "passed": None},
    ]
    result = metric_summary(rows, "rag_applicability_accuracy")
    assert result["value"] == 0.5
    assert (result["numerator"], result["denominator"], result["failures"]) == (1, 2, 1)
    assert result["failure_ids"] == ["b"]
    assert result["omitted"] == result["not_applicable"] == 1
    low, high = result["uncertainty"]["interval"]
    assert 0 < low < 0.5 < high < 1


def test_zero_denominator_is_not_a_perfect_score() -> None:
    result = metric_summary([], "budget_enforcement")
    assert result["value"] is None
    assert result["denominator"] == 0
    assert result["uncertainty"]["interval"] is None


def test_invalid_output_remains_a_calculation_failure() -> None:
    result = metric_summary(
        [
            {
                "id": "bad",
                "kind": "quality",
                "status": "ESCALATED",
                "passed": False,
                "expects_calculations": True,
            }
        ],
        "deterministic_value_accuracy",
    )
    assert result["denominator"] == result["failures"] == 1
    assert result["value"] == 0.0


def test_calculation_metric_uses_calculation_check() -> None:
    result = metric_summary(
        [
            {
                "id": "a",
                "kind": "quality",
                "status": "FAILED",
                "passed": False,
                "checks": {"calc_values": True},
            }
        ],
        "deterministic_value_accuracy",
    )
    assert result["value"] == 1.0


def test_slices_preserve_context_and_zero_claims_are_na() -> None:
    context = {"configuration": {"model_provider": "mock"}, "dataset_version": "1.0.0"}
    report = build_report(
        [
            {
                "id": "a",
                "kind": "quality",
                "status": "SUCCEEDED",
                "passed": True,
                "family": "FAMILY_A",
                "capability": "AG-AML",
                "scenario_type": "capability",
                "generated_claims": 0,
                "evaluation_context": context,
            },
            {"id": "b", "kind": "retrieval", "status": "OMITTED", "passed": None, "family": "FAMILY_B"},
        ]
    )
    assert report["context"] == context
    assert report["slices"]["family"]["FAMILY_B"]["omitted"] == 1
    assert report["slices"]["capability"]["AG-AML"]["passed"] == 1
    assert report["support_precision"]["value"] is None
    assert report["support_precision"]["status"] == "NOT_APPLICABLE"
    assert report["support_precision"]["reason"] == "zero_generated_claims"


def test_unknown_or_unlabelled_claims_do_not_receive_precision_scores() -> None:
    for count in (None, 3):
        report = build_report(
            [
                {
                    "id": "a",
                    "kind": "quality",
                    "status": "SUCCEEDED",
                    "passed": True,
                    "scenario_type": "capability",
                    "generated_claims": count,
                },
            ]
        )
        assert report["support_precision"]["value"] is None
        assert report["support_precision"]["status"] == "NOT_MEASURED"


def test_legacy_results_remain_readable() -> None:
    report = build_report(
        [
            {"id": "old", "kind": "budget", "status": "RUN_TOKENS", "passed": True},
        ]
    )
    assert report["context"] == {}
    assert report["slices"]["family"]["UNSPECIFIED"]["passed"] == 1
