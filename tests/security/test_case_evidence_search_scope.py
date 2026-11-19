"""Search is case scoped and rejects unauthorized requests in the disposable test database."""

import pytest

from tests.agent_helpers import prepared_case

pytestmark = pytest.mark.integration


def test_search_requires_assigned_case_access(make_client, make_settings, token, bank):
    prep = prepared_case(make_client(bank_base_url=bank.url), token, make_settings(bank_base_url=bank.url))
    path = f"/api/v1/cases/{prep.case_id}/evidence-search"
    response = prep.client.post(path, json={"query": "customer"}, headers=token(prep.analyst))
    assert response.status_code == 200
    result = response.json()
    assert result["matches"] and all(row["evidence_ref"].startswith("ev:") for row in result["matches"])
    other = "a.analyst2@synthetic" if prep.analyst == "a.analyst1@synthetic" else "a.analyst1@synthetic"
    other_response = prep.client.post(path, json={"query": "customer"}, headers=token(other))
    tenant_response = prep.client.post(
        path, json={"query": "customer"}, headers=token("b.supervisor1@synthetic")
    )
    anonymous_response = prep.client.post(path, json={"query": "customer"})
    invalid_response = prep.client.post(path, json={"query": "  "}, headers=token(prep.analyst))
    assert other_response.status_code == 404
    assert tenant_response.status_code == 404
    assert anonymous_response.status_code == 401
    assert invalid_response.status_code == 400
    assert invalid_response.json()["error"]["code"] == "SCHEMA_VALIDATION_FAILED"
