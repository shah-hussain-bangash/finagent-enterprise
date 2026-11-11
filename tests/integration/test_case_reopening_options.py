"""Case-page reopening hints come from the authenticated API identity."""

import pytest

from tests.helpers import subject
from tests.integration.test_case_reopening import completed as completed

pytestmark = pytest.mark.integration


def test_reopening_hints_follow_permissions_and_mfa(completed) -> None:  # type: ignore[no-untyped-def]
    env, case_id, analyst, _, _, version, _ = completed
    url = f"/api/v1/cases/{case_id}"
    supervisor = subject("a", "supervisor1")
    for actor, expected in (
        (supervisor, True),
        (analyst, False),
        (subject("a", "reviewer1"), False),
    ):
        response = env.client.get(url, headers=env.token(actor))
        assert response.status_code == 200, response.text
        assert response.json()["can_reopen"] is expected
        assert response.json()["reopening_requires_mfa"] is False
    denied = env.client.get(url, headers=env.token(subject("b", "supervisor1")))
    assert denied.status_code == 404
    assert "can_reopen" not in denied.json()
    session = env.client.post("/api/v1/dev/session", json={"subject": supervisor, "mfa": False})
    assert session.is_success, session.text
    low_mfa = env.client.get(url, headers={"Authorization": f"Bearer {session.json()['access_token']}"})
    assert low_mfa.status_code == 200, low_mfa.text
    assert low_mfa.json()["can_reopen"] is True
    assert low_mfa.json()["reopening_requires_mfa"] is True
    reopened = env.client.post(
        f"{url}/reopen",
        json={"reason": "Synthetic case-page eligibility regression"},
        headers={**env.token(supervisor), "If-Match": str(version)},
    )
    assert reopened.status_code == 200, reopened.text
    after = env.client.get(url, headers=env.token(supervisor))
    assert after.status_code == 200, after.text
    assert after.json()["can_reopen"] is False
    assert after.json()["reopening_requires_mfa"] is False
