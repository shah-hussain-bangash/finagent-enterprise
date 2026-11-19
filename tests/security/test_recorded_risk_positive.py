"""Check recorded synthetic AML output and case isolation in the disposable test DB."""

import pytest

from finagent.agents.runner import run_simple
from tests.agent_helpers import prepared_case

pytestmark = pytest.mark.integration


def test_recorded_indicator_is_visible_only_with_case_access(make_client, make_settings, token, bank):
    settings = make_settings(bank_base_url=bank.url)
    prep = prepared_case(make_client(bank_base_url=bank.url), token, settings)
    scope, _ = prep.scope()
    result = run_simple("AG-AML", prep.facts(), scope)
    path = f"/api/v1/cases/{prep.case_id}/agent-outputs"

    response = prep.client.get(path, headers=token(prep.analyst))
    assert response.status_code == 200
    output = next(item for item in response.json()["items"] if item["output_id"] == str(result.output_id))
    assert output["capability_id"] == "AG-AML"
    indicator = next(f for f in output["findings"] if f["claim_id"] == "aml-ind-STRUCTURING_PATTERN")
    assert indicator["claim_type"] == "INTERPRETATION"
    assert indicator["evidence_refs"]
    assert output["snapshot_id"] == str(prep.snapshot_id)

    other = "a.analyst2@synthetic" if prep.analyst == "a.analyst1@synthetic" else "a.analyst1@synthetic"
    assert prep.client.get(path, headers=token(other)).status_code == 404
    assert prep.client.get(path, headers=token("b.supervisor1@synthetic")).status_code == 404
    assert prep.client.get(path).status_code == 401
