"""Create draft evaluation templates and check candidates without registering them."""

from __future__ import annotations

import argparse
import copy
import json
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from finagent.evaluation.dataset_control import (
    GROUPS,
    record_for,
    validate_catalog,
)

SPLITS = {"validation": "validation", "release-candidate": "locked_release"}
DIRECTORIES = {"validation": "validation", "locked_release": "release-candidates"}
NAME_PATTERN = re.compile(r"[a-z0-9][a-z0-9-]{2,99}\Z")


def new_candidate(name: str, split: str) -> dict[str, Any]:
    if not NAME_PATTERN.fullmatch(name):
        raise ValueError("Use 3-100 lowercase letters, digits or hyphens; start with a letter or digit.")
    if split not in DIRECTORIES:
        raise ValueError("Unknown candidate split.")
    return {
        "preparation_version": 1,
        "status": "DRAFT",
        "intended_split": split,
        "author": "",
        "source_provenance": "",
        "independence_notes": "",
        "dataset": {
            "dataset": name,
            "version": "0.1.0-draft",
            "labels": "SYNTHETIC, DRAFT, UNREVIEWED; not release evidence",
            "provenance": "",
            "window": {"start": "", "end": ""},
            "capability_scenarios": [],
            "budget_scenarios": [],
            "retrieval_scenarios": [],
        },
        "lineages": {},
        "review_notes": [],
    }


def _filled(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip()) and value == value.strip()


def _expect_errors(expected: object) -> list[str]:
    allowed = {
        "status_in",
        "calc_values",
        "indicators_observed",
        "rejected_max",
        "rejected_min",
        "forbidden_text",
        "escalated",
        "gap_codes",
    }
    if not isinstance(expected, dict) or not expected:
        return ["provide nonempty expected checks."]
    errors = []
    for key, value in expected.items():
        if key not in allowed:
            errors.append(f"unsupported expected check: {key}.")
        elif key in {"status_in", "indicators_observed", "gap_codes"}:
            if not isinstance(value, list) or not all(_filled(item) for item in value):
                errors.append(f"{key} requires a list of nonempty strings.")
            elif key != "indicators_observed" and not value:
                errors.append(f"{key} cannot be empty.")
        elif key == "calc_values":
            if (
                not isinstance(value, dict)
                or not value
                or not all(_filled(name) and _filled(amount) for name, amount in value.items())
            ):
                errors.append("calc_values requires nonempty string keys and values.")
        elif key in {"rejected_max", "rejected_min"}:
            if type(value) is not int or value < 0:
                errors.append(f"{key} requires a nonnegative integer.")
        elif key == "escalated" and not isinstance(value, bool):
            errors.append("escalated requires a boolean.")
        elif key == "forbidden_text" and not _filled(value):
            errors.append("forbidden_text requires a nonempty string.")
    return errors


def candidate_errors(candidate: object) -> list[str]:
    if not isinstance(candidate, dict):
        return ["Candidate must be a JSON object."]
    errors = []
    if candidate.get("preparation_version") != 1 or candidate.get("status") != "DRAFT":
        errors.append("Candidate must use preparation_version 1 and status DRAFT.")
    intended_split = candidate.get("intended_split")
    if not isinstance(intended_split, str) or intended_split not in DIRECTORIES:
        errors.append("intended_split must be validation or locked_release.")
    for field in ("author", "source_provenance", "independence_notes"):
        if not _filled(candidate.get(field)):
            errors.append(f"Complete {field}.")
    document = candidate.get("dataset")
    if not isinstance(document, dict):
        return [*errors, "dataset must be an object."]
    name = document.get("dataset")
    if not isinstance(name, str) or not NAME_PATTERN.fullmatch(name):
        errors.append("Complete dataset.dataset with a valid dataset name.")
    for field in ("version", "labels", "provenance"):
        if not _filled(document.get(field)):
            errors.append(f"Complete dataset.{field}.")
    try:
        window = document["window"]
        start = datetime.fromisoformat(window["start"])
        end = datetime.fromisoformat(window["end"])
        if start.utcoffset() is None or end.utcoffset() is None or start >= end:
            raise ValueError("Window must be ordered and timezone-aware.")
    except (KeyError, TypeError, ValueError):
        errors.append("Provide a valid timezone-aware [start, end) window.")
    all_rows: list[dict[str, Any]] = []
    for group in GROUPS:
        rows = document.get(group)
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            errors.append(f"{group} must be a list of scenario objects.")
            continue
        all_rows.extend(rows)
        for row in rows:
            label = str(row.get("id", "MISSING_ID"))
            if not _filled(row.get("id")):
                errors.append(f"{group}: provide a scenario id.")
            if group == "capability_scenarios":
                if any(not _filled(row.get(field)) for field in ("family", "kind", "capability")):
                    errors.append(f"{label}: complete family, kind and capability.")
                errors.extend(f"{label}: {message}" for message in _expect_errors(row.get("expect")))
                if not isinstance(row.get("kind"), str) or row["kind"] not in {
                    "quality",
                    "gap",
                    "robustness",
                    "adversarial_reject",
                    "adversarial_boundary",
                }:
                    errors.append(f"{label}: unsupported capability scenario kind.")
                transactions = row.get("transactions", [])
                valid_transactions = isinstance(transactions, list) and all(
                    isinstance(item, list)
                    and len(item) == 7
                    and all(isinstance(value, str) for value in item)
                    for item in transactions
                )
                if not valid_transactions:
                    errors.append(f"{label}: transactions require seven string columns per row.")
                else:
                    for item in transactions:
                        try:
                            if not Decimal(item[1]).is_finite():
                                raise ValueError("Nonfinite amount.")
                            date.fromisoformat(item[6])
                        except (InvalidOperation, ValueError):
                            errors.append(f"{label}: invalid transaction amount or value date.")
                script = row.get("script", ["ok"])
                if not isinstance(script, list) or not script or not all(_filled(item) for item in script):
                    errors.append(f"{label}: script must be a nonempty list of strings.")
            elif group == "budget_scenarios":
                if type(row.get("run_token_budget")) is not int or row["run_token_budget"] <= 0:
                    errors.append(f"{label}: run_token_budget must be a positive integer.")
                if row.get("expect") != "BUDGET_EXCEEDED_ON_SECOND_CALL":
                    errors.append(f"{label}: specify BUDGET_EXCEEDED_ON_SECOND_CALL.")
            else:
                if any(not _filled(row.get(field)) for field in ("query", "as_of", "alert_type")):
                    errors.append(f"{label}: complete query, as_of and alert_type.")
                try:
                    date.fromisoformat(row.get("as_of", ""))
                except (TypeError, ValueError):
                    errors.append(f"{label}: as_of must be an ISO date.")
                expected = row.get("expect")
                if not isinstance(expected, dict) or not _filled(expected.get("outcome")):
                    errors.append(f"{label}: provide an expected retrieval outcome.")
                elif expected["outcome"] == "ANSWERED" and any(
                    not _filled(expected.get(field)) for field in ("policy_key", "version_label")
                ):
                    errors.append(f"{label}: answered retrieval requires policy_key and version_label.")
    if not all_rows:
        errors.append("Add independently authored scenarios; empty templates cannot be used.")
    ids = [row.get("id") for row in all_rows]
    if all(isinstance(identifier, str) for identifier in ids) and len(set(ids)) != len(ids):
        errors.append("Scenario ids must be unique within a candidate.")
    lineages = candidate.get("lineages")
    if not isinstance(lineages, dict):
        errors.append("lineages must be an object keyed by scenario id.")
    else:
        if all(isinstance(identifier, str) for identifier in ids) and set(lineages) != set(ids):
            errors.append("Provide exactly one lineage entry for every scenario id.")
        for identifier, lineage in lineages.items():
            if not isinstance(lineage, dict) or any(
                not _filled(lineage.get(field)) for field in ("family", "customer", "story")
            ):
                errors.append(f"{identifier}: lineage requires family, customer and story.")
    return errors


def check_candidates(project: Path, paths: list[Path]) -> list[str]:
    problems: list[str] = []
    folder = project / "src/finagent/evaluation"
    try:
        catalog = json.loads((folder / "dataset-catalog.json").read_text(encoding="utf-8-sig"))
        documents = {
            path.stem: json.loads(path.read_text(encoding="utf-8-sig"))
            for path in (folder / "datasets").glob("*.json")
        }
        if not isinstance(catalog, dict) or any(not isinstance(item, dict) for item in documents.values()):
            raise ValueError("Active catalog and dataset files must be JSON objects.")
        validate_catalog(documents, catalog)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return [f"Active catalog check failed: {exc}"]
    if not paths:
        return ["No candidate files found."]
    for path in paths:
        try:
            candidate = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            problems.append(f"{path.name}: cannot read candidate: {exc}")
            continue
        errors = candidate_errors(candidate)
        if errors:
            problems.extend(f"{path.name}: {message}" for message in errors)
            continue
        document = candidate["dataset"]
        name = document["dataset"]
        if name in documents:
            problems.append(f"{path.name}: dataset name is already active or repeated in this check.")
            continue
        record = record_for(
            name,
            document,
            candidate["intended_split"],
            candidate["lineages"],
            "CANDIDATE CHECK ONLY; " + candidate["independence_notes"],
        )
        trial_documents = {**documents, name: document}
        trial_catalog = copy.deepcopy(catalog)
        trial_catalog["datasets"][name] = record
        trial_catalog["history"].append(record)
        try:
            validate_catalog(trial_documents, trial_catalog)
        except (KeyError, TypeError, ValueError) as exc:
            problems.append(f"{path.name}: {exc}")
            continue
        documents, catalog = trial_documents, trial_catalog
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[1])
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("init", help="Create an empty draft template.")
    create.add_argument("--split", choices=SPLITS, required=True)
    create.add_argument("--name", required=True)
    check = commands.add_parser("check", help="Check supplied drafts, or both preparation folders.")
    check.add_argument("files", nargs="*", type=Path)
    args = parser.parse_args()
    project = args.project.resolve()
    if args.command == "init":
        split = SPLITS[args.split]
        try:
            candidate = new_candidate(args.name, split)
            destination = project / "evaluation-preparation" / DIRECTORIES[split] / f"{args.name}.json"
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("x", encoding="utf-8") as handle:
                handle.write(json.dumps(candidate, indent=2) + "\n")
        except (OSError, ValueError) as exc:
            print(f"Not created: {exc}")
            return 2
        print(f"Created DRAFT: {destination}")
        print("Complete independent content and lineage before checking. Nothing was registered.")
        return 0
    paths = args.files or sorted(
        path
        for directory in DIRECTORIES.values()
        for path in (project / "evaluation-preparation" / directory).glob("*.json")
    )
    problems = check_candidates(project, paths)
    for message in problems:
        print(f"INCOMPLETE / BLOCKED: {message}")
    if problems:
        print("No dataset was registered, approved or sealed.")
        return 1
    print("READY_FOR_REVIEW: declared structure and registered overlap checks passed.")
    print("Semantic correctness, near-duplicate review, labeling and approval remain human review tasks.")
    print("Nothing was registered, approved or sealed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
