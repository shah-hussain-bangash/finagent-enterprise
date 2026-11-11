"""Promotion evidence checks, independent of model execution and database I/O."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from finagent.core.errors import AppError
from finagent.database.models import EvaluationRun
from finagent.domain.fingerprint import fingerprint
from finagent.evaluation.dataset_control import read_dataset
from finagent.services.governance_service import config as config_service
from tests.unit.test_evaluation_dataset_control import fixtures, write_fixture


class ReleaseEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.folder = Path(self.directory.name) / "datasets"
        documents, catalog = fixtures()
        write_fixture(self.folder, documents, catalog)
        self.control_patch = patch.object(config_service, "_EVALUATION_DATASETS", self.folder)
        self.control_patch.start()
        self.addCleanup(self.control_patch.stop)
        data, control = read_dataset(self.folder, "fixture-3", purpose="release")
        self.row = EvaluationRun(
            dataset="fixture-3",
            dataset_version=data["version"],
            dataset_hash=fingerprint({**data, "_dataset_control": control}),
            results=[
                {
                    "evaluation_context": {
                        "evaluation_purpose": "release",
                        "dataset_control": control,
                    }
                }
            ],
        )

    def test_current_release_evidence_is_eligible(self) -> None:
        config_service.require_release_evidence(self.row)

    def test_development_or_legacy_run_cannot_authorize_promotion(self) -> None:
        for results in ([], [{"evaluation_context": {"evaluation_purpose": "development"}}]):
            with self.subTest(results=results):
                self.row.results = results
                with self.assertRaises(AppError):
                    config_service.require_release_evidence(self.row)

    def test_dataset_hash_mismatch_requires_new_evidence(self) -> None:
        self.row.dataset_hash = "changed"
        with self.assertRaises(AppError):
            config_service.require_release_evidence(self.row)

    def test_snapshot_mismatch_requires_new_evidence(self) -> None:
        self.row.results[0]["evaluation_context"]["dataset_control"] = {}
        with self.assertRaises(AppError):
            config_service.require_release_evidence(self.row)

    def test_version_mismatch_requires_new_evidence(self) -> None:
        self.row.dataset_version = "old"
        with self.assertRaises(AppError):
            config_service.require_release_evidence(self.row)

    def test_forged_release_purpose_does_not_promote_development_data(self) -> None:
        self.row.dataset = "fixture-1"
        with self.assertRaises(AppError):
            config_service.require_release_evidence(self.row)
