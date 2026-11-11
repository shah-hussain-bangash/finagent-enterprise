"""API evaluation-purpose restrictions for the known development fixture."""

from __future__ import annotations

import pytest

from tests.agent_helpers import PADMIN

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("purpose", ["validation", "release"])
def test_development_dataset_cannot_be_used_as_validation_or_release(
    make_client, token, bank, purpose: str
) -> None:  # type: ignore[no-untyped-def]
    client = make_client(bank_base_url=bank.url)
    response = client.post(
        "/api/v1/evaluations",
        json={"dataset": "aml-synthetic-v1", "purpose": purpose},
        headers=token(PADMIN),
    )
    assert response.status_code == 422, response.text
    assert "purpose" in response.json()["error"]["message"]
