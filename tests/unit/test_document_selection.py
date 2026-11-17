"""KYC selects usable subject-scoped evidence without hiding failed uploads."""

from __future__ import annotations

import copy
import uuid
from datetime import date
from itertools import permutations
from typing import Any

import pytest

from finagent.agents.prep import CaseFacts, _checklist, _select_document
from finagent.database.models import Alert, Case, EvidenceConflict, EvidenceItem, EvidenceSnapshot
from finagent.domain.aml_calculations import CalcSet

TODAY = date(2026, 11, 11)
SUBJECT = "customer-demo-001"
pytestmark = pytest.mark.req("AER-006", "AER-007", "FR-014")


def document(
    ref: str,
    issued: str | None,
    *,
    kind: str = "PROOF_OF_ADDRESS",
    status: str = "PARSED",
    owner: str | None = SUBJECT,
    validation: str = "VALID",
    origin: str = "EXTRACTED",
) -> EvidenceItem:
    fields = []
    if issued is not None:
        fields.append(
            {
                "field_key": "expiry_date" if kind == "PASSPORT" else "issue_date",
                "value": issued,
                "value_state": "PRESENT",
                "validation_status": validation,
                "origin": origin,
            }
        )
    return EvidenceItem(
        id=uuid.uuid5(uuid.NAMESPACE_URL, ref),
        source_type="DOCUMENT_EXTRACTION",
        source_ref=ref,
        content={
            "document_type": kind,
            "status": status,
            "customer_ref": owner,
            "fields": fields,
        },
    )


def facts(docs: list[EvidenceItem], subjects: list[str] | None = None) -> CaseFacts:
    return CaseFacts(
        tenant_id=uuid.uuid4(),
        case=Case(),
        alert=Alert(subject_refs=subjects or [SUBJECT]),
        snapshot=EvidenceSnapshot(),
        items=docs,
        calcs=CalcSet(),
    )


def checklist_item(case_facts: CaseFacts, name: str) -> dict[str, Any]:
    return next(row for row in _checklist(case_facts, [], TODAY) if row["item"] == name)


def test_corrected_poa_wins_independent_of_evidence_order() -> None:
    unreadable = document("a-quarantined", None, status="QUARANTINED")
    older = document("b-connector", "2026-08-02")
    corrected = document("z-clean", "2026-11-10", owner=None, origin="CORRECTED")
    before = [copy.deepcopy(d.content) for d in (unreadable, older, corrected)]

    for ordered in permutations((unreadable, older, corrected)):
        case_facts = facts(list(ordered))
        result = checklist_item(case_facts, "PROOF_OF_ADDRESS")
        assert result["state"] == "PRESENT"
        assert result["detail"] == "issued 2026-11-10 (1 days before the review date)"
        assert result["refs"] == [case_facts.ref(corrected)]
        selection = case_facts.document_selection
        assert selection is not None
        poa = next(item for item in selection["items"] if item["document_type"] == "PROOF_OF_ADDRESS")
        assert poa["evidence_ref"] == case_facts.ref(corrected)
        assert poa["source_ref"] == corrected.source_ref
        assert selection["review_date"] == TODAY.isoformat()
        # Selection never deletes failures, changes the snapshot or marks a field verified.
        assert case_facts.items == list(ordered)
    assert [d.content for d in (unreadable, older, corrected)] == before


@pytest.mark.parametrize("failed_status", ["QUARANTINED", "PARSE_FAILED", "STORED", "PARSING"])
def test_unreadable_document_cannot_outrank_parsed_document(failed_status: str) -> None:
    failed = document("a-failed", "2026-11-11", status=failed_status)
    clean = document("z-clean", "2026-11-10")
    assert (
        _select_document([failed, clean], "PROOF_OF_ADDRESS", SUBJECT, single_subject=True, today=TODAY)
        is clean
    )


def test_passport_selection_ignores_misclassified_poa_missing_expiry() -> None:
    wrong = document("a-poa-uploaded-as-passport", None, kind="PASSPORT", owner=None)
    passport = document("z-connector-passport", "2036-09-28", kind="PASSPORT")
    case_facts = facts([wrong, passport])
    result = checklist_item(case_facts, "IDENTITY_DOCUMENT")
    assert result["state"] == "PRESENT"
    assert result["refs"] == [case_facts.ref(passport)]
    assert wrong in case_facts.items


@pytest.mark.parametrize("invalid_date", ["not-a-date", "2026-02-30", "2026-11-12"])
def test_bad_or_future_poa_date_cannot_outrank_valid_evidence(invalid_date: str) -> None:
    bad = document("z-bad", invalid_date)
    clean = document("a-clean", "2026-11-10")
    assert (
        _select_document([bad, clean], "PROOF_OF_ADDRESS", SUBJECT, single_subject=True, today=TODAY) is clean
    )


def test_future_date_alone_is_unverified_not_current() -> None:
    result = checklist_item(facts([document("future", "2026-11-12")]), "PROOF_OF_ADDRESS")
    assert result["state"] == "UNVERIFIED"
    assert result["detail"] == "issue date is after the review date"


def test_invalid_date_validation_is_not_treated_as_current() -> None:
    result = checklist_item(
        facts([document("invalid", "2026-11-10", validation="INVALID")]), "PROOF_OF_ADDRESS"
    )
    assert result["state"] == "UNVERIFIED"


def test_unreadable_date_alone_is_not_treated_as_current() -> None:
    case_facts = facts([document("quarantined", "2026-11-10", status="QUARANTINED")])
    result = checklist_item(case_facts, "PROOF_OF_ADDRESS")
    assert result["state"] == "UNVERIFIED"
    assert case_facts.document_selection is not None
    poa = next(
        item for item in case_facts.document_selection["items"] if item["document_type"] == "PROOF_OF_ADDRESS"
    )
    assert poa["selection"] == "SELECTED" and poa["processing_status"] == "QUARANTINED"


def test_stale_poa_remains_missing() -> None:
    result = checklist_item(facts([document("old", "2026-08-02")]), "PROOF_OF_ADDRESS")
    assert result["state"] == "MISSING"
    assert "101 days" in result["detail"]


def test_ties_are_stable_without_input_order_dependence() -> None:
    a = document("a", "2026-11-10")
    b = document("b", "2026-11-10")
    assert _select_document(
        [a, b], "PROOF_OF_ADDRESS", SUBJECT, single_subject=True, today=TODAY
    ) is _select_document([b, a], "PROOF_OF_ADDRESS", SUBJECT, single_subject=True, today=TODAY)


def test_other_customers_document_is_not_selected() -> None:
    own = document("a-own", "2026-08-02")
    other = document("z-other", "2026-11-10", owner="customer-demo-002")
    result = checklist_item(facts([other, own]), "PROOF_OF_ADDRESS")
    assert result["state"] == "MISSING"
    assert result["refs"] == [f"ev:{own.id}"]


def test_unscoped_upload_is_not_assigned_to_first_customer_in_multi_subject_case() -> None:
    unscoped = document("upload", "2026-11-10", owner=None, origin="CORRECTED")
    result = checklist_item(facts([unscoped], [SUBJECT, "customer-demo-002"]), "PROOF_OF_ADDRESS")
    assert result["state"] == "MISSING"
    assert result["refs"] == []


def test_non_current_source_status_is_not_overridden_by_upload() -> None:
    clean = document("clean", "2026-11-10", owner=None, origin="CORRECTED")
    source = EvidenceItem(
        id=uuid.uuid4(),
        source_type="KYC_DOCUMENT_LIST",
        source_ref=SUBJECT,
        content={"documents": [{"document_type": "PROOF_OF_ADDRESS", "status": "EXPIRED"}]},
    )
    result = checklist_item(facts([source, clean]), "PROOF_OF_ADDRESS")
    assert result["state"] == "MISSING"
    assert "source status EXPIRED" in result["detail"]


def test_open_identity_conflict_is_preserved_after_document_selection() -> None:
    clean = document("clean", "2026-11-10", origin="CORRECTED")
    conflict = EvidenceConflict(
        status="OPEN", field_key="full_name", item_a_id=uuid.uuid4(), item_b_id=clean.id
    )
    rows = _checklist(facts([clean]), [conflict], TODAY)
    result = next(row for row in rows if row["item"] == "IDENTITY_DATA_CONSISTENCY")
    assert result["state"] == "CONFLICTING"
    assert f"ev:{clean.id}" in result["refs"]
