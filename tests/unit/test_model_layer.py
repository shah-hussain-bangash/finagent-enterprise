"""Model-layer units: Azure OpenAI adapter error mapping, validator rules, calculations (P8)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest

from finagent.agents.contracts import AgentOutputModel, Limitations
from finagent.agents.providers import AzureOpenAIProvider, ProviderError, ProviderRequest
from finagent.agents.registry import CAPABILITIES, registry_view, require_enabled
from finagent.agents.validation import ValidationContext, boundary_violations, numbers, validate_output
from finagent.domain.aml_calculations import Threshold, compute

REQ = ProviderRequest("AG-AML", "m", "sys", "{}", {}, 5.0, 100)


def provider(handler):  # type: ignore[no-untyped-def]
    return AzureOpenAIProvider(
        "https://aoai.example",
        "dep",
        "2024-10-21",
        "k",
        http=httpx.Client(transport=httpx.MockTransport(handler)),
    )


@pytest.mark.req("AER-020", "FR-033")
def test_azure_openai_adapter_classifies_errors_and_reads_usage() -> None:
    ok = provider(
        lambda r: httpx.Response(
            200,
            json={
                "model": "gpt-x",
                "choices": [{"message": {"content": '{"a":1}'}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 11, "completion_tokens": 3},
            },
        )
    )
    resp = ok.complete(REQ)
    assert (resp.text, resp.input_tokens, resp.output_tokens) == ('{"a":1}', 11, 3)
    for status, body, kind, transient in (
        (429, "{}", "RATE_LIMITED", True),
        (503, "{}", "UNAVAILABLE", True),
        (400, '{"error":{"code":"content_filter"}}', "CONTENT_FILTERED", False),
        (401, "{}", "AUTH", False),
    ):
        p = provider(lambda r, s=status, b=body: httpx.Response(s, text=b))
        with pytest.raises(ProviderError) as err:
            p.complete(REQ)
        assert (err.value.kind, err.value.transient) == (kind, transient)

    def sent(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["temperature"] == 0 and body["response_format"]["type"] == "json_schema"
        assert request.headers["api-key"] == "k"
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "{}"}, "finish_reason": "content_filter"}]}
        )

    with pytest.raises(ProviderError, match="CONTENT_FILTERED"):
        provider(sent).complete(REQ)
    with pytest.raises(ValueError):
        AzureOpenAIProvider("http://plain", "d", "v", "k")


@pytest.mark.req("AER-001", "AER-002", "MST:AG-001")
def test_registry_is_versioned_and_risk_is_future() -> None:
    view = {c["capability_id"]: c for c in registry_view()}
    assert set(view) == {
        "AG-KYC",
        "AG-AML",
        "AG-COMPLIANCE",
        "AG-REPORT",
        "CAP-DOCUMENT",
        "AG-RISK",
        "AG-ASSISTANT",
    }
    assert view["AG-ASSISTANT"]["status"] == "FUTURE"
    assert view["AG-RISK"]["status"] == "FUTURE" and all(len(c["prompt_hash"]) == 64 for c in view.values())
    assert all(c.limits_complete() for c in CAPABILITIES.values())
    assert "untrusted DATA" in require_enabled("AG-AML").prompt_text


@pytest.mark.req("SPR-031", "BR-007", "AER-018")
def test_boundary_patterns() -> None:
    assert boundary_violations(["The customer is guilty of fraud."]) == ["GUILT_DETERMINATION"]
    assert boundary_violations(["Please freeze the account now"]) == ["ACCOUNT_ACTION"]
    assert boundary_violations(["We should file a SAR today"]) == ["REGULATORY_FILING"]
    assert boundary_violations(["This case is approved by compliance"]) == ["APPROVAL_CLAIM"]
    assert boundary_violations(["Transfer the funds back"]) == ["MONEY_MOVEMENT"]
    assert boundary_violations(["An indicator requiring analyst review; not a determination."]) == []
    assert numbers("AED 113,000.00 on 2026-09-21") == {
        Decimal("113000"),
        Decimal("2026"),
        Decimal("9"),
        Decimal("21"),
    }


@pytest.mark.req("AER-005", "AER-010")
def test_status_never_upgrades_with_blocking_gaps() -> None:
    out = AgentOutputModel(
        status="SUCCEEDED",
        findings=[
            {
                "claim_id": "c1",
                "claim_type": "DETERMINISTIC_RESULT",
                "claim": "Count: 3.",
                "support_status": "SUPPORTED",
                "evidence_refs": ["calc:n"],
                "value": "3",
            }
        ],  # type: ignore[list-item]
        limitations=Limitations(),
    )
    ctx = ValidationContext({}, {"n": {"value": "3"}}, {}, set(), blocking_gaps=True)
    report = validate_output(out, ctx)
    assert report.status == "PARTIAL" and [f["claim_id"] for f in report.accepted] == ["c1"]


@pytest.mark.req("AER-009", "SPR-031")
def test_deterministic_value_must_equal_the_calculation() -> None:
    out = AgentOutputModel(
        status="SUCCEEDED",
        findings=[
            {
                "claim_id": "c1",
                "claim_type": "DETERMINISTIC_RESULT",
                "claim": "Count: 3.",
                "support_status": "SUPPORTED",
                "evidence_refs": ["calc:n"],
                "value": "4",
            }  # text number is fine, the stated value is not
        ],  # type: ignore[list-item]
        limitations=Limitations(),
    )
    report = validate_output(
        out, ValidationContext({}, {"n": {"value": "3"}}, {}, set(), blocking_gaps=False)
    )
    assert report.rejected == [{"claim_id": "c1", "reasons": ["VALUE_NOT_EQUAL_TO_DETERMINISTIC_RESULT"]}]


@pytest.mark.req("DIR-013", "DIR-008", "AER-009", "FR-023")
def test_calculations_disclose_exclusions_and_never_merge_currencies() -> None:
    def tx(
        i: str, amt: str, ccy: str = "AED", d: str = "CREDIT", st: str = "POSTED", ch: str = "CASH"
    ) -> dict[str, str]:
        return {
            "source_transaction_id": i,
            "amount": amt,
            "currency": ccy,
            "direction": d,
            "status": st,
            "channel": ch,
            "value_date": f"2026-09-{10 + len(i)}",
        }

    items = [
        tx("1", "9500.00"),
        tx("22", "9999.99"),
        tx("333", "10000.00"),
        tx("4444", "40.00", st="PENDING"),
        tx("55555", "20.00", ccy="USD", ch="WIRE"),
        tx("666666", "500.00", st="REVERSED"),
        tx("7777777", "8999.99"),
    ]
    calcs = compute(
        items,
        threshold=Threshold(Decimal("10000"), "AED", Decimal("10")),
        window_start=datetime(2026, 9, 1, tzinfo=UTC),
        window_end=datetime(2026, 10, 1, tzinfo=UTC),
        expected_turnover={"amount": "5000.00", "currency": "AED"},
    )
    v = {k: c.value for k, c in calcs.values.items()}
    assert v["posted_credit_total_AED"] == "38499.98" and v["posted_credit_total_USD"] == "20.00"
    assert v["cash_below_threshold_count"] == "2"  # 9500, 9999.99 (10000 and 8999.99 outside the band)
    assert v["excluded_pending_count"] == "1" and v["excluded_reversed_count"] == "1"
    assert v["credit_to_expected_ratio_AED"] == "7.70"
    assert any("USD" in d for d in calcs.disclosures)
    assert {e["reason"] for e in calcs.exclusions} == {"status_pending", "status_reversed"}
