"""Regression checks for unsupported evidence and authority boundaries."""

from finagent.agents.contracts import AgentOutputModel
from finagent.agents.validation import ValidationContext, validate_output


def test_unknown_evidence_cannot_support_a_customer_fact() -> None:
    output = AgentOutputModel.model_validate(
        {
            "status": "SUCCEEDED",
            "findings": [
                {
                    "claim_id": "unsupported-customer-fact",
                    "claim_type": "FACT",
                    "claim": "The customer owns an overseas trading company.",
                    "support_status": "SUPPORTED",
                    "evidence_refs": ["ev:missing-company-record"],
                }
            ],
            "limitations": {},
            "recommendations": [],
        }
    )
    context = ValidationContext(
        evidence={},
        calculations={},
        citations={},
        gap_codes=set(),
        blocking_gaps=False,
    )

    report = validate_output(output, context)

    assert report.status == "FAILED"
    assert not report.accepted
    assert len(report.rejected) == 1
    assert report.rejected[0]["claim_id"] == "unsupported-customer-fact"
    assert not report.boundary_violations


def test_guilt_and_freezing_claim_escalates_the_output() -> None:
    output = AgentOutputModel.model_validate(
        {
            "status": "SUCCEEDED",
            "findings": [
                {
                    "claim_id": "prohibited-determination",
                    "claim_type": "INTERPRETATION",
                    "claim": (
                        "The customer is guilty of money laundering "
                        "and the account should be frozen immediately."
                    ),
                    "support_status": "SUPPORTED",
                    "evidence_refs": [],
                }
            ],
            "limitations": {},
            "recommendations": [],
        }
    )
    context = ValidationContext(
        evidence={},
        calculations={},
        citations={},
        gap_codes=set(),
        blocking_gaps=False,
    )

    report = validate_output(output, context)

    assert report.status == "ESCALATED"
    assert not report.accepted
    assert report.rejected
    assert report.boundary_violations
