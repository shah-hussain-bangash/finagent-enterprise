"""Lineage, use-purpose, change-history and contamination controls."""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from finagent.evaluation.dataset_control import (
    DatasetControlError,
    read_dataset,
    record_for,
    validate_catalog,
)


def fixtures() -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    documents: dict[str, dict[str, Any]] = {}
    entries: dict[str, dict[str, Any]] = {}
    for index, split in enumerate(("development", "validation", "locked_release"), 1):
        name = f"fixture-{index}"
        scenario = {
            "id": f"scenario-{index}",
            "family": f"FAMILY_{index}",
            "kind": "quality",
            "capability": "AG-AML",
            "transactions": [[f"t{index}", str(index), "AED"]],
            "expect": {},
        }
        document = {
            "dataset": name,
            "version": "1.0.0",
            "window": {"start": "2026-01-01"},
            "capability_scenarios": [scenario],
            "budget_scenarios": [],
            "retrieval_scenarios": [],
        }
        lineage = {
            scenario["id"]: {
                "family": scenario["family"],
                "customer": f"customer-{index}",
                "story": f"story-{index}",
            }
        }
        documents[name] = document
        entries[name] = record_for(name, document, split, lineage, "Independent test fixture")
    return documents, {"schema_version": 1, "datasets": entries, "history": list(entries.values())}


def write_fixture(folder: Path, documents: dict[str, dict[str, Any]], catalog: dict[str, Any]) -> None:
    folder.mkdir()
    for name, document in documents.items():
        (folder / f"{name}.json").write_text(json.dumps(document), encoding="utf-8")
    (folder.parent / "dataset-catalog.json").write_text(json.dumps(catalog), encoding="utf-8")


class DatasetControlTests(unittest.TestCase):
    def test_independent_splits_validate(self) -> None:
        documents, catalog = fixtures()
        controls = validate_catalog(documents, catalog)
        self.assertTrue(all(control["all_splits_present"] for control in controls.values()))
        self.assertEqual(controls["fixture-3"]["split"], "locked_release")

    def test_cross_split_lineage_is_rejected(self) -> None:
        for dimension in ("family", "customer", "story"):
            with self.subTest(dimension=dimension):
                documents, catalog = fixtures()
                catalog["datasets"]["fixture-2"]["lineages"]["scenario-2"][dimension] = catalog["datasets"][
                    "fixture-1"
                ]["lineages"]["scenario-1"][dimension].upper()
                with self.assertRaisesRegex(DatasetControlError, "leakage"):
                    validate_catalog(documents, catalog)

    def test_renaming_ids_and_labels_does_not_hide_exact_input_reuse(self) -> None:
        documents, catalog = fixtures()
        documents["fixture-2"]["capability_scenarios"][0]["transactions"][0][1] = "1"
        old = catalog["datasets"]["fixture-2"]
        replacement = record_for(
            "fixture-2", documents["fixture-2"], old["split"], old["lineages"], old["reason"]
        )
        catalog["datasets"]["fixture-2"] = replacement
        catalog["history"][1] = replacement
        with self.assertRaisesRegex(DatasetControlError, "input leakage"):
            validate_catalog(documents, catalog)

    def test_unregistered_content_change_is_rejected(self) -> None:
        documents, catalog = fixtures()
        documents["fixture-1"]["capability_scenarios"][0]["expect"] = {"changed": True}
        with self.assertRaisesRegex(DatasetControlError, "differs"):
            validate_catalog(documents, catalog)

    def test_missing_lineage_is_rejected(self) -> None:
        documents, catalog = fixtures()
        catalog["datasets"]["fixture-1"]["lineages"] = {}
        with self.assertRaises(DatasetControlError):
            validate_catalog(documents, catalog)

    def test_unregistered_dataset_is_rejected(self) -> None:
        documents, catalog = fixtures()
        documents["unknown"] = documents["fixture-1"]
        with self.assertRaisesRegex(DatasetControlError, "registrations"):
            validate_catalog(documents, catalog)

    def test_same_version_cannot_change_content(self) -> None:
        documents, catalog = fixtures()
        previous = copy.deepcopy(catalog["history"][0])
        previous["content_sha256"] = "old-content"
        catalog["history"].insert(0, previous)
        with self.assertRaisesRegex(DatasetControlError, "new version"):
            validate_catalog(documents, catalog)

    def test_development_change_requires_recorded_new_version(self) -> None:
        documents, catalog = fixtures()
        previous = copy.deepcopy(catalog["history"][0])
        previous["version"] = "0.9.0"
        previous["content_sha256"] = "previous-content"
        catalog["history"].insert(0, previous)
        self.assertIn("fixture-1", validate_catalog(documents, catalog))

    def test_locked_dataset_cannot_be_rewritten(self) -> None:
        documents, catalog = fixtures()
        previous = copy.deepcopy(catalog["history"][2])
        previous["version"] = "0.9.0"
        previous["content_sha256"] = "old-content"
        catalog["history"].insert(0, previous)
        with self.assertRaisesRegex(DatasetControlError, "immutable"):
            validate_catalog(documents, catalog)

    def test_contaminated_release_is_refused(self) -> None:
        documents, catalog = fixtures()
        catalog["datasets"]["fixture-3"]["contaminated"] = True
        with self.assertRaisesRegex(DatasetControlError, "Contaminated"):
            validate_catalog(documents, catalog)

    def test_contamination_cannot_be_cleared_in_place(self) -> None:
        documents, catalog = fixtures()
        incident = copy.deepcopy(catalog["history"][0])
        incident["contaminated"] = True
        incident["reason"] = "Recorded test incident"
        catalog["history"].insert(0, incident)
        with self.assertRaisesRegex(DatasetControlError, "contamination"):
            validate_catalog(documents, catalog)

    def test_retired_lineage_cannot_move_to_another_split(self) -> None:
        documents, catalog = fixtures()
        retired = copy.deepcopy(catalog["history"][1])
        retired["dataset"] = "retired-development"
        retired["split"] = "development"
        catalog["history"].insert(0, retired)
        with self.assertRaisesRegex(DatasetControlError, "leakage"):
            validate_catalog(documents, catalog)

    def test_wrong_purpose_and_missing_splits_are_refused(self) -> None:
        documents, catalog = fixtures()
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / "datasets"
            write_fixture(folder, documents, catalog)
            with self.assertRaisesRegex(DatasetControlError, "purpose"):
                read_dataset(folder, "fixture-3", purpose="development")
            with self.assertRaisesRegex(DatasetControlError, "purpose"):
                read_dataset(folder, "fixture-1", purpose="release")
            _, control = read_dataset(folder, "fixture-3", purpose="release")
            self.assertEqual(control["split"], "locked_release")
            (folder / "fixture-2.json").unlink()
            del documents["fixture-2"]
            del catalog["datasets"]["fixture-2"]
            (folder.parent / "dataset-catalog.json").write_text(json.dumps(catalog), encoding="utf-8")
            with self.assertRaisesRegex(DatasetControlError, "separate development"):
                read_dataset(folder, "fixture-3", purpose="release")

    def test_duplicate_ids_and_family_mismatch_are_refused(self) -> None:
        documents, catalog = fixtures()
        row = documents["fixture-1"]["capability_scenarios"][0]
        documents["fixture-1"]["capability_scenarios"].append(copy.deepcopy(row))
        with self.assertRaisesRegex(DatasetControlError, "unique"):
            validate_catalog(documents, catalog)
        documents["fixture-1"]["capability_scenarios"].pop()
        row["family"] = "FALSE_FAMILY"
        with self.assertRaisesRegex(DatasetControlError, "family differs"):
            validate_catalog(documents, catalog)
