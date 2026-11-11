"""Regression tests for deterministic report narrative validation."""

from decimal import Decimal
from unittest import TestCase

from finagent.agents.contracts import (
    Finding,
    Limitations,
    ReportDraftModel,
    ReportSection,
)
from finagent.agents.validation import ValidationReport, validate_report


class ReportValidationTests(TestCase):
    def setUp(self) -> None:
        self.finding = Finding(
            claim_id="count",
            claim_type="DETERMINISTIC_RESULT",
            claim="Posted transaction count: 3.",
            support_status="SUPPORTED",
            evidence_refs=["calc:count"],
            value="3",
        )
        self.draft = ReportDraftModel(
            status="SUCCEEDED",
            findings=[self.finding],
            sections=[
                ReportSection(
                    section_id="calculations",
                    title="Validated calculations",
                    text="- Posted transaction count: 3.",
                    claim_ids=["count"],
                )
            ],
            limitations=Limitations(),
        )

    def validate(self, draft: ReportDraftModel) -> ValidationReport:
        return validate_report(
            draft,
            {self.finding.claim_id: self.finding.model_dump()},
            {Decimal("3"), Decimal("99")},
            False,
        )

    def test_verbatim_report_passes(self) -> None:
        report = self.validate(self.draft)
        self.assertEqual(report.status, "SUCCEEDED")
        self.assertTrue(report.passed)

    def test_unsupported_words_fail_even_without_new_numbers(self) -> None:
        section = self.draft.sections[0].model_copy(
            update={"text": ("- Posted transaction count: 3. " + "The customer hid the source.")}
        )
        report = self.validate(self.draft.model_copy(update={"sections": [section]}))
        self.assertEqual(report.status, "FAILED")
        self.assertFalse(report.passed)

    def test_globally_allowed_number_does_not_support_section(self) -> None:
        section = self.draft.sections[0].model_copy(update={"text": "- Posted transaction count: 99."})
        report = self.validate(self.draft.model_copy(update={"sections": [section]}))
        self.assertEqual(report.status, "FAILED")
        self.assertIn(
            "NUMBER_NOT_IN_VALIDATED_INPUTS",
            report.rejected[0]["reasons"],
        )

    def test_changed_value_and_type_are_rejected(self) -> None:
        for update in ({"value": "99"}, {"claim_type": "FACT"}):
            with self.subTest(update=update):
                finding = self.finding.model_copy(update=update)
                report = self.validate(self.draft.model_copy(update={"findings": [finding]}))
                self.assertEqual(report.status, "FAILED")
                self.assertEqual(report.accepted, [])

    def test_unknown_claim_link_fails(self) -> None:
        section = self.draft.sections[0].model_copy(update={"claim_ids": ["unknown"]})
        report = self.validate(self.draft.model_copy(update={"sections": [section]}))
        self.assertEqual(report.status, "FAILED")

    def test_unlinked_claim_section_fails(self) -> None:
        section = self.draft.sections[0].model_copy(update={"claim_ids": []})
        report = self.validate(self.draft.model_copy(update={"sections": [section]}))
        self.assertEqual(report.status, "FAILED")

    def test_duplicate_sections_and_claim_links_fail(self) -> None:
        duplicate = self.draft.model_copy(update={"sections": self.draft.sections * 2})
        self.assertEqual(self.validate(duplicate).status, "FAILED")

        section = self.draft.sections[0].model_copy(update={"claim_ids": ["count", "count"]})
        report = self.validate(self.draft.model_copy(update={"sections": [section]}))
        self.assertEqual(report.status, "FAILED")

    def test_context_requires_exact_trusted_text(self) -> None:
        section = ReportSection(
            section_id="alert_context",
            title="Alert context",
            text="Alert DEMO.",
        )
        draft = self.draft.model_copy(update={"sections": [section]})
        self.assertEqual(self.validate(draft).status, "FAILED")

        report = validate_report(
            draft,
            {self.finding.claim_id: self.finding.model_dump()},
            set(),
            False,
            trusted_sections={"alert_context": "Alert DEMO."},
        )
        self.assertEqual(report.status, "SUCCEEDED")

        changed = section.model_copy(update={"text": "Alert DEMO. The customer hid funds."})
        report = validate_report(
            draft.model_copy(update={"sections": [changed]}),
            {self.finding.claim_id: self.finding.model_dump()},
            set(),
            False,
            trusted_sections={"alert_context": "Alert DEMO."},
        )
        self.assertEqual(report.status, "FAILED")

    def test_invented_title_fails(self) -> None:
        section = self.draft.sections[0].model_copy(update={"title": "Confirmed misconduct"})
        report = self.validate(self.draft.model_copy(update={"sections": [section]}))
        self.assertEqual(report.status, "FAILED")

    def test_authority_boundary_escalates(self) -> None:
        section = self.draft.sections[0].model_copy(update={"text": "Freeze the account."})
        report = self.validate(self.draft.model_copy(update={"sections": [section]}))
        self.assertEqual(report.status, "ESCALATED")
        self.assertEqual(report.accepted, [])
        self.assertFalse(report.passed)

    def test_invented_limitation_fails(self) -> None:
        draft = self.draft.model_copy(
            update={"limitations": Limitations(missing_evidence=["Unrecorded overseas activity."])}
        )
        self.assertEqual(self.validate(draft).status, "FAILED")

    def test_blocking_gaps_downgrade_success(self) -> None:
        report = validate_report(
            self.draft,
            {self.finding.claim_id: self.finding.model_dump()},
            set(),
            True,
        )
        self.assertEqual(report.status, "PARTIAL")
