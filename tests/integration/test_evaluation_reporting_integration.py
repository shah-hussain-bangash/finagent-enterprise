"""Reporting evidence for synthetic evaluation runs and omitted retrieval."""

from __future__ import annotations

import pytest

from tests.agent_helpers import PADMIN
from tests.helpers import subject
from tests.integration.test_governance import approve_all_policies, ok

pytestmark = pytest.mark.integration


@pytest.mark.req("AER-032")
def test_evaluation_report_persists_counts_slices_and_configuration(make_client, token, bank) -> None:  # type: ignore[no-untyped-def]
    client = make_client(bank_base_url=bank.url)
    approve_all_policies(client, token)
    run = ok(
        client.post(
            "/api/v1/evaluations",
            json={"dataset": "aml-synthetic-v1"},
            headers=token(PADMIN),
        ),
        201,
    )
    assert run["gate"] == "PASS_PROPOSED"
    accuracy = run["metrics"]["deterministic_value_accuracy"]
    # Dataset v1.1.0 includes the multi-currency quality case alongside the two original cases.
    calculation_cases = {
        row["id"] for row in run["results"] if row["kind"] == "quality" and row["expects_calculations"]
    }
    assert calculation_cases == {"struct-001", "benign-001", "multi-quality-001"}
    assert accuracy["numerator"] == accuracy["denominator"] == 3
    assert accuracy["failures"] == 0
    context = run["report"]["context"]
    assert context["configuration"]["model_provider"] == "mock"
    assert context["dataset_split_status"] == "REGISTERED_DEVELOPMENT"
    assert context["dataset_control"]["all_splits_present"] is False
    assert "TM_STRUCTURING" in run["report"]["slices"]["family"]
    assert run["report"]["support_precision"]["value"] is None
    saved = ok(client.get(f"/api/v1/evaluations/{run['evaluation_id']}", headers=token(PADMIN)))
    assert saved["report"] == run["report"]
    assert saved["metrics"] == run["metrics"]
    operator = ok(
        client.post(
            "/api/v1/evaluations",
            json={"dataset": "aml-synthetic-v1"},
            headers=token(subject("a", "operator1")),
        ),
        201,
    )
    assert operator["gate"] == "INCOMPLETE"
    omitted = [r for r in operator["results"] if r["status"] == "OMITTED"]
    assert len(omitted) == 3
    assert all(r["passed"] is None for r in omitted)
    assert operator["metrics"]["rag_applicability_accuracy"]["omitted"] == 2
    assert operator["metrics"]["rag_abstention_accuracy"]["omitted"] == 1
