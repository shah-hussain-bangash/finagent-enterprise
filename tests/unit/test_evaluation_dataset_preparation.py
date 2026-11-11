"""Draft generation and candidate checks never register evaluation data."""

from __future__ import annotations

import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any
from unittest.mock import patch

from scripts.prepare_evaluation_dataset import candidate_errors, check_candidates, main, new_candidate

from tests.unit.test_evaluation_dataset_control import fixtures, write_fixture


def complete_candidate(name: str = "candidate-four", split: str = "validation") -> dict[str, Any]:
    candidate = new_candidate(name, split)
    candidate.update(
        author="Synthetic test author",
        source_provenance="Fresh synthetic test fixture",
        independence_notes="Distinct test family, customer and story.",
    )
    document = candidate["dataset"]
    document["provenance"] = "Synthetic test inputs."
    document["window"] = {"start": "2026-01-01T00:00:00+00:00", "end": "2026-02-01T00:00:00+00:00"}
    document["capability_scenarios"] = [
        {
            "id": "candidate-scenario",
            "family": "FAMILY_FOUR",
            "kind": "quality",
            "capability": "AG-AML",
            "transactions": [["t4", "40.00", "AED", "CREDIT", "POSTED", "CARD", "2026-01-10"]],
            "expect": {"status_in": ["SUCCEEDED"]},
        }
    ]
    candidate["lineages"] = {
        "candidate-scenario": {
            "family": "FAMILY_FOUR",
            "customer": "customer-four",
            "story": "story-four",
        }
    }
    return candidate


class DatasetPreparationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.active = self.root / "src/finagent/evaluation/datasets"
        self.active.parent.mkdir(parents=True)
        documents, catalog = fixtures()
        write_fixture(self.active, documents, catalog)
        self.before = {str(path): path.read_bytes() for path in self.active.parent.rglob("*.json")}

    def candidate_file(self, candidate: dict[str, Any], filename: str = "candidate.json") -> Path:
        path = self.root / filename
        path.write_text(json.dumps(candidate), encoding="utf-8")
        return path

    def test_empty_template_is_explicitly_incomplete(self) -> None:
        candidate = new_candidate("candidate-four", "validation")
        self.assertEqual(candidate["status"], "DRAFT")
        self.assertTrue(candidate_errors(candidate))
        self.assertIn("author", " ".join(candidate_errors(candidate)))

    def test_valid_structure_and_checks_leave_active_data_unchanged(self) -> None:
        path = self.candidate_file(complete_candidate())
        self.assertEqual(check_candidates(self.root, [path]), [])
        after = {str(path): path.read_bytes() for path in self.active.parent.rglob("*.json")}
        self.assertEqual(after, self.before)

    def test_cross_split_overlap_with_active_history_is_blocked(self) -> None:
        candidate = complete_candidate()
        candidate["lineages"]["candidate-scenario"]["customer"] = "customer-1"
        result = check_candidates(self.root, [self.candidate_file(candidate)])
        self.assertIn("leakage", " ".join(result))

    def test_overlap_between_supplied_candidates_is_blocked(self) -> None:
        validation = complete_candidate()
        release = complete_candidate("candidate-five", "locked_release")
        release["dataset"]["capability_scenarios"][0]["transactions"][0][1] = "50.00"
        files = [
            self.candidate_file(validation, "validation.json"),
            self.candidate_file(release, "release.json"),
        ]
        self.assertIn("leakage", " ".join(check_candidates(self.root, files)))

    def test_duplicate_candidate_name_is_blocked(self) -> None:
        first = self.candidate_file(complete_candidate(), "one.json")
        second = self.candidate_file(complete_candidate(), "two.json")
        self.assertIn("already active or repeated", " ".join(check_candidates(self.root, [first, second])))

    def test_invalid_or_empty_expected_checks_are_blocked(self) -> None:
        for expected in ({}, {"invented_check": True}, {"rejected_min": -1}, {"escalated": "yes"}):
            with self.subTest(expected=expected):
                candidate = complete_candidate()
                candidate["dataset"]["capability_scenarios"][0]["expect"] = expected
                self.assertTrue(candidate_errors(candidate))

    def test_invalid_transactions_and_naive_window_are_blocked(self) -> None:
        candidate = complete_candidate()
        candidate["dataset"]["capability_scenarios"][0]["transactions"] = [["short"]]
        candidate["dataset"]["window"]["start"] = "2026-01-01T00:00:00"
        errors = " ".join(candidate_errors(candidate))
        self.assertIn("transactions", errors)
        self.assertIn("timezone-aware", errors)

    def test_missing_lineage_and_duplicate_ids_are_blocked(self) -> None:
        candidate = complete_candidate()
        candidate["dataset"]["capability_scenarios"].append(
            copy.deepcopy(candidate["dataset"]["capability_scenarios"][0])
        )
        candidate["lineages"] = {}
        errors = " ".join(candidate_errors(candidate))
        self.assertIn("unique", errors)
        self.assertIn("lineage", errors)

    def test_malformed_json_reports_error(self) -> None:
        path = self.root / "bad.json"
        path.write_text("not json", encoding="utf-8")
        self.assertIn("cannot read candidate", " ".join(check_candidates(self.root, [path])))

    def test_unsafe_dataset_names_are_refused(self) -> None:
        for name in ("../escape", "UPPERCASE", "a/b", "x"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                new_candidate(name, "validation")

    def test_init_creates_draft_and_refuses_overwrite(self) -> None:
        args = [
            "prepare",
            "--project",
            str(self.root),
            "init",
            "--split",
            "validation",
            "--name",
            "candidate-four",
        ]
        with patch("sys.argv", args), redirect_stdout(io.StringIO()):
            self.assertEqual(main(), 0)
            self.assertEqual(main(), 2)
        created = self.root / "evaluation-preparation/validation/candidate-four.json"
        self.assertEqual(json.loads(created.read_text())["status"], "DRAFT")
        self.assertEqual(
            {str(path): path.read_bytes() for path in self.active.parent.rglob("*.json")}, self.before
        )

    def test_non_object_candidates_and_unknown_split_are_reported(self) -> None:
        self.assertTrue(candidate_errors([]))
        candidate = complete_candidate()
        candidate["intended_split"] = []
        self.assertTrue(candidate_errors(candidate))
