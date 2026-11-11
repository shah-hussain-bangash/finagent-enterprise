"""Specialist capabilities through the model gateway with deterministic validation (P8).

AER-001..027 (subset), SPR-028, SPR-031, NQ-048, MST:AG-002, FR-021..026 (subset).
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select, text

from finagent.agents.gateway import BudgetExceededError, CallContext, ModelUnavailableError
from finagent.agents.providers import MockModelProvider
from finagent.agents.registry import CapabilityUnavailableError
from finagent.agents.runner import run_compliance, run_report, run_simple
from finagent.database.models import AgentOutput, ModelRequest
from finagent.database.session import session_factory, transaction
from tests.agent_helpers import Prepared, prepared_case

pytestmark = pytest.mark.integration


@pytest.fixture
def prep(make_client, make_settings, token, bank) -> Prepared:  # type: ignore[no-untyped-def]
    settings = make_settings(bank_base_url=bank.url)
    client = make_client(bank_base_url=bank.url)
    return prepared_case(client, token, settings)


def requests_of(factory: Any, capability: str) -> list[tuple[str, str]]:
    with transaction(factory) as s:
        rows = s.scalars(
            select(ModelRequest)
            .where(ModelRequest.capability_id == capability)
            .order_by(ModelRequest.created_at)
        ).all()
        return [(r.purpose, r.status) for r in rows]


@pytest.mark.req(
    "AER-005",
    "AER-006",
    "AER-008",
    "AER-014",
    "AER-015",
    "AER-009",
    "AER-026",
    "SPR-031",
    "FR-021",
    "FR-022",
    "FR-023",
    "FR-026",
    "AER-019",
)
@pytest.mark.release_blocker
def test_capabilities_produce_validated_findings_with_lineage(prep: Prepared) -> None:
    facts = prep.facts()
    scope, provider = prep.scope()
    kyc = run_simple("AG-KYC", facts, scope)
    aml = run_simple("AG-AML", facts, scope)
    doc = run_simple("CAP-DOCUMENT", facts, scope)
    cmp_ = run_compliance(facts, scope)
    for r in (kyc, aml, doc, cmp_):
        assert r.rejected == [] and r.validation["boundary_violations"] == [], (r.capability_id, r.rejected)
    # deterministic values are copied, never computed by the model (AER-009)
    det = {f["claim_id"]: f for f in aml.accepted if f["claim_type"] == "DETERMINISTIC_RESULT"}
    calcs = {k: v.value for k, v in facts.calcs.values.items()}
    assert det["aml-posted_credit_total_AED"]["value"] == calcs["posted_credit_total_AED"]
    assert det["aml-cash_below_threshold_count"]["value"] == calcs["cash_below_threshold_count"] == "12"
    assert "posted_credit_total_USD" in calcs  # currencies never merged
    assert any(
        f["claim_type"] == "INTERPRETATION" and "not a determination" in f["claim"] for f in aml.accepted
    )
    # KYC: the EXPIRED proof of address is an explicit gap, never "complete"
    assert kyc.status == "PARTIAL" and any(
        "PROOF_OF_ADDRESS" in f["claim"] for f in kyc.accepted if f["claim_type"] == "GAP"
    )
    assert cmp_.status == "SUCCEEDED" and cmp_.policy_versions
    assert all(r.startswith("cite:") for f in cmp_.accepted for r in f["evidence_refs"])
    report = run_report(facts, scope, [kyc, aml, doc, cmp_])
    assert report.status == "PARTIAL"  # upstream PARTIAL is never upgraded
    assert {s["section_id"] for s in report.sections} >= {
        "alert_context",
        "calculations",
        "policy_considerations",
    }
    with transaction(prep.factory) as s:
        out = s.get(AgentOutput, report.output_id)
        assert out is not None
        lineage = out.lineage
    assert lineage["prompt_hash"] and lineage["provider"] == "mock-model-provider"
    assert (
        lineage["snapshot_id"] == str(facts.snapshot.id)
        and lineage["policy_versions"] == cmp_.policy_versions
    )
    assert provider.calls == 5 and requests_of(prep.factory, "AG-AML") == [("INITIAL", "SUCCEEDED")]


@pytest.mark.req("AER-020", "NQ-033", "MST:AG-002", "FR-033")
def test_transient_errors_retry_bounded_and_every_attempt_is_recorded(prep: Prepared) -> None:
    facts = prep.facts()
    scope, _ = prep.scope(MockModelProvider(["timeout", "rate_limited", "ok"]))
    assert run_simple("AG-AML", facts, scope).status in ("SUCCEEDED", "PARTIAL")
    assert requests_of(prep.factory, "AG-AML") == [
        ("INITIAL", "TIMEOUT"),
        ("RETRY", "FAILED"),
        ("RETRY", "SUCCEEDED"),
    ]
    scope, _ = prep.scope(MockModelProvider(["timeout", "timeout", "timeout", "ok"]))
    with pytest.raises(ModelUnavailableError):
        run_simple("AG-KYC", facts, scope)
    assert len(requests_of(prep.factory, "AG-KYC")) == 3  # 1 + max 2 retries, then stop
    scope, _ = prep.scope(MockModelProvider(["content_filtered"]))
    with pytest.raises(ModelUnavailableError):
        run_simple("CAP-DOCUMENT", facts, scope)
    assert requests_of(prep.factory, "CAP-DOCUMENT") == [("INITIAL", "FAILED")]  # permanent: no retry


@pytest.mark.req("AER-019", "MST:AG-002", "AER-004")
def test_one_bounded_schema_repair_then_escalation(prep: Prepared) -> None:
    facts = prep.facts()
    scope, _ = prep.scope(MockModelProvider(["invalid_json", "ok"]))
    assert run_simple("AG-AML", facts, scope).status in ("SUCCEEDED", "PARTIAL")
    assert requests_of(prep.factory, "AG-AML") == [("INITIAL", "INVALID_OUTPUT"), ("REPAIR", "SUCCEEDED")]
    scope, _ = prep.scope(MockModelProvider(["schema_violation", "invalid_json"]))
    result = run_simple("AG-KYC", facts, scope)
    assert result.status == "ESCALATED" and result.accepted == []
    assert requests_of(prep.factory, "AG-KYC") == [
        ("INITIAL", "INVALID_OUTPUT"),
        ("REPAIR", "INVALID_OUTPUT"),
    ]


@pytest.mark.req("AER-009", "AER-013", "AER-026", "DIR-017", "SPR-031")
@pytest.mark.release_blocker
def test_invented_numbers_citations_and_evidence_are_rejected(prep: Prepared) -> None:
    facts = prep.facts()
    scope, _ = prep.scope(MockModelProvider(["hallucinated_number"]))
    aml = run_simple("AG-AML", facts, scope)
    bad = {r["claim_id"]: r["reasons"] for r in aml.rejected}
    assert any(
        "VALUE_NOT_EQUAL_TO_DETERMINISTIC_RESULT" in v or "NUMBER_NOT_IN_REFERENCED_SOURCES" in v
        for v in bad.values()
    )
    assert all("987654" not in f["claim"] for f in aml.accepted) and aml.status == "PARTIAL"
    scope, _ = prep.scope(MockModelProvider(["hallucinated_number"]))
    doc = run_simple("CAP-DOCUMENT", facts, scope)  # FACT claims: only the number rule can catch this
    assert any("NUMBER_NOT_IN_REFERENCED_SOURCES" in r["reasons"] for r in doc.rejected)
    scope, _ = prep.scope(MockModelProvider(["invented_citation"]))
    cmp_ = run_compliance(facts, scope)
    assert {r["claim_id"] for r in cmp_.rejected} == {"x-cite"}
    scope, _ = prep.scope(MockModelProvider(["unknown_evidence"]))
    kyc = run_simple("AG-KYC", facts, scope)
    assert [r["claim_id"] for r in kyc.rejected] == ["x-ev"] and kyc.rejected[0]["reasons"][0].startswith(
        "UNKNOWN_EVIDENCE"
    )


@pytest.mark.req("AER-018", "SPR-028", "FR-025", "BR-007", "MST:SEC-002", "AER-028")
@pytest.mark.release_blocker
def test_authority_boundary_and_obeyed_injection_escalate(prep: Prepared) -> None:
    facts = prep.facts()
    for mode, code in (("guilt_claim", "GUILT_DETERMINATION"), ("injection_obeyed", "APPROVAL_CLAIM")):
        scope, _ = prep.scope(MockModelProvider([mode]))
        r = run_simple("AG-AML", facts, scope)
        assert r.status == "ESCALATED" and r.accepted == [] and code in r.validation["boundary_violations"]
        assert r.recommendations == []  # nothing from a rejected output reaches a reviewer as advice


@pytest.mark.req("AER-002", "AER-020", "MST:AG-001")
@pytest.mark.release_blocker
def test_future_capability_fails_closed_without_any_call(prep: Prepared) -> None:
    scope, provider = prep.scope()
    with pytest.raises(CapabilityUnavailableError) as err:
        scope.gateway.invoke("AG-RISK", None, None, CallContext(prep.factory and None, None, None, "x"))  # type: ignore[arg-type]
    assert err.value.reason == "CAPABILITY_FUTURE" and provider.calls == 0
    with pytest.raises(CapabilityUnavailableError):
        scope.gateway.invoke("AG-FREEZE-ACCOUNTS", None, None, None)  # type: ignore[arg-type]


@pytest.mark.req("NQ-048", "AER-020", "NQ-028", "MST:AG-002")
@pytest.mark.release_blocker
def test_budgets_stop_billable_work_and_are_recorded(prep: Prepared) -> None:
    facts = prep.facts()
    scope, provider = prep.scope(run_token_budget=1)
    scope.run_id = facts.case.id  # any stable id groups the run's usage
    run_simple("AG-KYC", facts, scope)  # first call allowed (nothing spent yet)
    with pytest.raises(BudgetExceededError) as err:
        run_simple("AG-AML", facts, scope)
    assert err.value.scope == "RUN_TOKENS" and provider.calls == 1
    assert ("INITIAL", "BUDGET_DENIED") in requests_of(prep.factory, "AG-AML")
    scope, provider = prep.scope(model_price_per_1k_input="1000", tenant_monthly_cost_budget="0.01")
    run_simple("CAP-DOCUMENT", facts, scope)
    with pytest.raises(BudgetExceededError) as err:
        run_simple("CAP-DOCUMENT", facts, scope)
    assert err.value.scope == "TENANT_MONTHLY_COST"
    with transaction(prep.factory) as s:
        cost = s.scalar(
            select(ModelRequest.estimated_cost).where(
                ModelRequest.status == "SUCCEEDED", ModelRequest.capability_id == "CAP-DOCUMENT"
            )
        )
    assert cost is not None and cost > Decimal("0")


@pytest.mark.req("AER-027", "SPR-019", "AER-003")
def test_context_is_minimized_and_carries_no_authority(prep: Prepared) -> None:
    seen: list[dict[str, Any]] = []

    class Spy(MockModelProvider):
        def complete(self, request):  # type: ignore[no-untyped-def]
            seen.append({"cap": request.capability_id, "user": json.loads(request.user)})
            return super().complete(request)

    scope, _ = prep.scope(Spy())
    facts = prep.facts()
    run_simple("AG-AML", facts, scope)
    aml_input = json.dumps(seen[0]["user"]["facts"])
    for secret_field in ("full_name", "date_of_birth", "nationality", "SYNTHETIC Person"):
        assert secret_field not in aml_input
    ctx = seen[0]["user"]["execution_context"]
    assert set(ctx) == {
        "run_id",
        "step_id",
        "case_id",
        "tenant_ref",
        "capability_id",
        "capability_version",
        "correlation_id",
    }


@pytest.mark.req("AER-016", "FR-036")
def test_agent_outputs_and_usage_are_immutable(prep: Prepared, db_url: str) -> None:
    scope, _ = prep.scope()
    out = run_simple("AG-AML", prep.facts(), scope)
    for stmt in ("UPDATE agent_outputs SET status = 'SUCCEEDED' WHERE id = :i", "DELETE FROM model_requests"):
        with (
            pytest.raises(Exception, match=r"immutable|permission denied"),
            transaction(session_factory(db_url)) as s,
        ):
            s.execute(text(stmt), {"i": out.output_id})
