"""Authorization checks for the existing endpoint used by the connected risk view.

These tests require the disposable finagent_test database. The shared fixture resets its schema.
"""

import pytest

from tests.helpers import assigned_analyst, ingest

pytestmark = [pytest.mark.integration, pytest.mark.release_blocker]


def test_recorded_agent_outputs_follow_case_and_tenant_scope(client, token) -> None:  # type: ignore[no-untyped-def]
    case_id = ingest(client, token)["case_id"]
    owner = assigned_analyst(client, token, case_id)
    other = "a.analyst2@synthetic" if owner == "a.analyst1@synthetic" else "a.analyst1@synthetic"
    path = f"/api/v1/cases/{case_id}/agent-outputs"

    allowed = client.get(path, headers=token(owner))
    assert allowed.status_code == 200
    assert allowed.json() == {"items": []}
    assert client.get(path, headers=token(other)).status_code == 404
    assert client.get(path, headers=token("b.supervisor1@synthetic")).status_code == 404
    assert client.get(path, headers=token("a.operator1@synthetic")).status_code == 403
    assert client.get(path).status_code == 401
