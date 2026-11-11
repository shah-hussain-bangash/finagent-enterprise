import pytest

from finagent.agents.contracts import AgentOutputModel
from finagent.agents.providers import RESPONDERS


@pytest.mark.req("AER-008")
def test_mock_aml_questions_do_not_assume_specific_transactions() -> None:
    output = RESPONDERS["AG-AML"](
        {
            "facts": {
                "calculations": {
                    "values": {},
                    "disclosures": [],
                },
                "indicators": [],
            },
            "evidence": {
                "known_gaps": [],
            },
        }
    )

    validated = AgentOutputModel.model_validate(output)
    questions = " ".join(validated.recommendations).lower()

    assert "large outgoing wire" not in questions
    assert "source of the cash deposits" not in questions
    assert "cash-intensive business" not in questions
    assert "transaction id" in questions
