"""Mock/gateway regression for the provisional source-gap case, not release evidence."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from finagent.agents.contracts import (
    AgentInput,
    AgentOutputModel,
    EvidenceBundle,
    EvidenceRef,
    ExecutionContextRef,
    Limits,
)
from finagent.agents.gateway import CallContext
from finagent.agents.prep import INDICATOR_VERSION, threshold_of
from finagent.agents.providers import MockModelProvider
from finagent.agents.registry import CONFIG_VERSION, require_enabled
from finagent.agents.validation import ValidationContext, validate_output
from finagent.database.models import ModelRequest
from finagent.database.session import transaction
from finagent.domain.aml_calculations import RULE_VERSION, compute
from finagent.domain.fingerprint import fingerprint
from tests.agent_helpers import Prepared, prepared_case

pytestmark = pytest.mark.integration

WINDOW = {
    "start": "2026-01-01T00:00:00+00:00",
    "end": "2026-02-01T00:00:00+00:00",
}
SCENARIO = {
    "id": "validation-source-gap-003",
    "family": "TM_MULTI_CURRENCY_ACTIVITY",
    "kind": "gap",
    "capability": "AG-AML",
    "script": ["ok"],
    "transactions": [
        ["val-gap-001", "215.30", "EUR", "CREDIT", "POSTED", "WIRE", "2026-01-12"],
    ],
    "known_gaps": [
        {
            "code": "SOURCE_INCOMPLETE",
            "severity": "BLOCKING",
            "source_type": "TRANSACTIONS",
            "source_ref": "synthetic-validation-customer-003",
            "detail": (
                "Synthetic fixture intentionally omits a source page; "
                "supplied rows are not a complete transaction history."
            ),
        },
    ],
    "expect": {"status_in": ["PARTIAL"], "gap_codes": ["SOURCE_INCOMPLETE"]},
}


@pytest.fixture
def gap_prepared(make_client, make_settings, token, bank) -> Prepared:  # type: ignore[no-untyped-def]
    settings = make_settings(bank_base_url=bank.url)
    client = make_client(bank_base_url=bank.url)
    return prepared_case(client, token, settings)


def test_provisional_gap_is_generated_validated_and_recorded(gap_prepared: Prepared) -> None:
    # If the local draft is present, ensure the tested snapshot still matches it.
    # CI can exercise the frozen test fixture without personal preparation files.
    draft = (
        Path(__file__).resolve().parents[2]
        / "evaluation-preparation/validation/aml-validation-candidate-v1.json"
    )
    if draft.exists():
        document = json.loads(draft.read_text(encoding="utf-8-sig"))["dataset"]
        matches = [row for row in document["capability_scenarios"] if row["id"] == SCENARIO["id"]]
        assert matches == [SCENARIO], "Draft gap case changed; review and update the test snapshot."
        assert document["window"] == WINDOW

    seen: list[dict[str, Any]] = []

    class Spy(MockModelProvider):
        def complete(self, request: Any) -> Any:
            seen.append(json.loads(request.user))
            return super().complete(request)

    scope, provider = gap_prepared.scope(Spy(["ok"]))
    principal = gap_prepared.principal()
    columns = (
        "source_transaction_id",
        "amount",
        "currency",
        "direction",
        "status",
        "channel",
        "value_date",
    )
    items = [dict(zip(columns, row, strict=True)) for row in SCENARIO["transactions"]]
    calculations = compute(
        items,
        threshold=threshold_of(gap_prepared.settings),
        window_start=datetime.fromisoformat(WINDOW["start"]),
        window_end=datetime.fromisoformat(WINDOW["end"]),
        expected_turnover=None,
    )
    evidence_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "gap-probe:" + SCENARIO["id"]))
    evidence = {evidence_id: {"items": items}}
    capability = require_enabled("AG-AML")
    agent_input = AgentInput(
        execution_context=ExecutionContextRef(
            run_id=None,
            step_id="test:provisional-source-gap",
            case_id="eval-" + SCENARIO["id"],
            tenant_ref="evaluation",
            capability_id=capability.capability_id,
            capability_version=capability.version,
            correlation_id=str(uuid.uuid4()),
        ),
        task={
            "task_type": "AG-AML",
            "objective": capability.purpose,
            "evaluation_scenario": SCENARIO["id"],
        },
        evidence=EvidenceBundle(
            snapshot_id="eval-" + SCENARIO["id"],
            completeness_status="INCOMPLETE",
            evidence_refs=[
                EvidenceRef(
                    ref="ev:" + evidence_id,
                    source_type="TRANSACTIONS",
                    source_ref="synthetic-validation-customer-003",
                    version_no=1,
                    content_hash=fingerprint(evidence[evidence_id]),
                )
            ],
            known_gaps=SCENARIO["known_gaps"],
        ),
        facts={
            "calculations": calculations.as_dict(),
            "indicators": [],
            "indicator_version": INDICATOR_VERSION,
            "customer": {"customer_type": "INDIVIDUAL"},
        },
        policy_configuration={"config_version": CONFIG_VERSION, "calculation_rules": RULE_VERSION},
        limits=Limits(
            max_steps=capability.max_model_calls,
            token_budget=capability.token_budget,
            cost_budget=str(capability.cost_budget),
            deadline_s=capability.timeout_s,
        ),
    )
    output, _ = scope.gateway.invoke(
        "AG-AML",
        agent_input,
        AgentOutputModel,
        CallContext(principal.tenant_id, None, None, "test:provisional-source-gap"),
    )
    assert provider.calls == 1
    assert len(seen) == 1
    assert seen[0]["evidence"]["known_gaps"] == SCENARIO["known_gaps"]
    assert any(
        finding.claim_type == "GAP" and "gap:SOURCE_INCOMPLETE" in finding.evidence_refs
        for finding in output.findings
    ), "Mock output did not generate the expected gap finding."

    report = validate_output(
        output,
        ValidationContext(
            evidence=evidence,
            calculations={key: value.as_dict() for key, value in calculations.values.items()},
            citations={},
            gap_codes={"SOURCE_INCOMPLETE"},
            blocking_gaps=True,
        ),
    )
    assert report.status == "PARTIAL", report
    assert report.rejected == [], report.rejected
    assert report.boundary_violations == []
    assert any(
        finding["claim_type"] == "GAP" and "gap:SOURCE_INCOMPLETE" in finding["evidence_refs"]
        for finding in report.accepted
    )
    with transaction(gap_prepared.factory) as session:
        rows = session.scalars(
            select(ModelRequest).where(
                ModelRequest.capability_id == "AG-AML",
            )
        ).all()
        assert [(row.purpose, row.status) for row in rows] == [("INITIAL", "SUCCEEDED")]
