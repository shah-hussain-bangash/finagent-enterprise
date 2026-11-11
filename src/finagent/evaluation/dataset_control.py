"""Dataset lineage and use controls (AER-030).

The catalog is a reviewed source artifact, not an approval or access-control boundary.
Lineage declarations require human review; exact-input checks cannot detect every near duplicate.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

SPLITS = {"development", "validation", "locked_release"}
PURPOSE_SPLIT = {
    "development": "development",
    "validation": "validation",
    "release": "locked_release",
}
GROUPS = ("capability_scenarios", "budget_scenarios", "retrieval_scenarios")


class DatasetControlError(ValueError):
    """A dataset is unregistered, altered, contaminated or used for the wrong purpose."""


def digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _text(value: object) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise DatasetControlError("Dataset metadata requires nonempty, trimmed strings.")
    return value


def scenarios(document: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for group in GROUPS:
        items = document.get(group)
        if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
            raise DatasetControlError(f"Invalid scenario collection: {group}")
        rows.extend(items)
    identifiers = [_text(row.get("id")) for row in rows]
    if not rows or len(set(identifiers)) != len(identifiers):
        raise DatasetControlError("Datasets require scenarios with unique identifiers.")
    return rows


def input_digest(row: dict[str, Any], window: object) -> str:
    # Ignore identifiers and expected answers: renaming them must not hide exact reuse.
    content = {
        key: value
        for key, value in row.items()
        if key not in {"id", "family", "expect", "customer_lineage", "story_lineage"}
    }
    if "transactions" in content:
        content["transactions"] = [transaction[1:] for transaction in content["transactions"]]
    return digest({"window": window, "input": content})


def record_for(
    name: str,
    document: dict[str, Any],
    split: str,
    lineages: dict[str, dict[str, str]],
    reason: str,
    *,
    contaminated: bool = False,
) -> dict[str, Any]:
    """Prepare a registration record; callers must review lineage before committing it."""
    return {
        "dataset": name,
        "version": document["version"],
        "split": split,
        "content_sha256": digest(document),
        "lineages": lineages,
        "input_sha256": sorted({input_digest(row, document.get("window")) for row in scenarios(document)}),
        "contaminated": contaminated,
        "reason": reason,
    }


def validate_catalog(
    documents: dict[str, dict[str, Any]],
    catalog: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    if catalog.get("schema_version") != 1:
        raise DatasetControlError("Unsupported dataset catalog schema.")
    entries = catalog.get("datasets")
    history = catalog.get("history")
    if not isinstance(entries, dict) or not isinstance(history, list) or not history:
        raise DatasetControlError("Dataset registrations and history are required.")
    if set(entries) != set(documents):
        raise DatasetControlError("Catalog registrations must match all dataset JSON files.")
    owners: dict[tuple[str, str], str] = {}
    registered_versions: dict[tuple[str, str], str] = {}
    locked: dict[str, tuple[str, str]] = {}
    for record in history:
        if not isinstance(record, dict):
            raise DatasetControlError("Invalid dataset history record.")
        name = _text(record.get("dataset"))
        version = _text(record.get("version"))
        split = record.get("split")
        if split not in SPLITS:
            raise DatasetControlError("Invalid dataset split in history.")
        _text(record.get("reason"))
        content_hash = _text(record.get("content_sha256"))
        if not isinstance(record.get("contaminated"), bool):
            raise DatasetControlError("History must explicitly record contamination status.")
        pair = (name, version)
        if pair in registered_versions and registered_versions[pair] != content_hash:
            raise DatasetControlError("Changed dataset content requires a new version.")
        registered_versions[pair] = content_hash
        if split == "locked_release":
            if record["contaminated"]:
                raise DatasetControlError("Contaminated locked release data cannot be used.")
            seal = (version, content_hash)
            if name in locked and locked[name] != seal:
                raise DatasetControlError("Locked datasets are immutable; register a new dataset name.")
            locked[name] = seal
        lineages = record.get("lineages")
        fingerprints = record.get("input_sha256")
        if (
            not isinstance(lineages, dict)
            or not lineages
            or not isinstance(fingerprints, list)
            or not fingerprints
        ):
            raise DatasetControlError("History requires lineage and input fingerprints.")
        tokens = [("input", _text(item)) for item in fingerprints]
        for lineage in lineages.values():
            if not isinstance(lineage, dict):
                raise DatasetControlError("Invalid lineage declaration.")
            for dimension in ("family", "customer", "story"):
                tokens.append((dimension, _text(lineage.get(dimension)).casefold()))
        for token in tokens:
            if token in owners and owners[token] != split:
                raise DatasetControlError(f"Cross-split {token[0]} leakage detected.")
            owners[token] = split
    controls: dict[str, dict[str, Any]] = {}
    present = {entry.get("split") for entry in entries.values() if isinstance(entry, dict)}
    for name, document in documents.items():
        entry = entries[name]
        if not isinstance(entry, dict) or entry.get("split") not in SPLITS:
            raise DatasetControlError("Invalid current dataset registration.")
        if document.get("dataset") != name or "_dataset_control" in document:
            raise DatasetControlError("Dataset identity is invalid or uses reserved metadata.")
        version = _text(document.get("version"))
        lineages = entry.get("lineages")
        rows = scenarios(document)
        if not isinstance(lineages, dict) or set(lineages) != {row["id"] for row in rows}:
            raise DatasetControlError("Every scenario requires exactly one lineage declaration.")
        for row in rows:
            lineage = lineages[row["id"]]
            declared_family = row.get("family", row.get("alert_type"))
            if declared_family is not None and lineage.get("family") != declared_family:
                raise DatasetControlError("Registered family differs from the scenario family.")
        matching = [r for r in history if r["dataset"] == name and r["version"] == version]
        if not matching or entry != matching[-1]:
            raise DatasetControlError("Current registration must match the latest version history record.")
        if any(r.get("contaminated") for r in matching):
            raise DatasetControlError("Dataset contamination is recorded; retire this version.")
        expected = record_for(name, document, entry["split"], lineages, entry["reason"])
        if entry != expected:
            raise DatasetControlError("Dataset content or provenance differs from its registered version.")
        if name in locked and locked[name] != (version, entry["content_sha256"]):
            raise DatasetControlError("Locked dataset identity cannot be reassigned.")
        controls[name] = {
            "catalog_sha256": digest(catalog),
            "content_sha256": entry["content_sha256"],
            "split": entry["split"],
            "lineages": lineages,
            "contaminated": False,
            "all_splits_present": present == SPLITS,
            "lineage_review": "DECLARED_NOT_INDEPENDENTLY_VERIFIED",
        }
    return controls


def read_dataset(
    folder: Path,
    name: str,
    *,
    purpose: str = "development",
) -> tuple[dict[str, Any], dict[str, Any]]:
    if purpose not in PURPOSE_SPLIT:
        raise DatasetControlError("Unknown evaluation purpose.")
    try:
        catalog = json.loads((folder.parent / "dataset-catalog.json").read_text(encoding="utf-8-sig"))
        documents = {
            path.stem: json.loads(path.read_text(encoding="utf-8-sig")) for path in folder.glob("*.json")
        }
        if not isinstance(catalog, dict) or any(not isinstance(value, dict) for value in documents.values()):
            raise DatasetControlError("Catalog and datasets must be JSON objects.")
        controls = validate_catalog(documents, catalog)
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise DatasetControlError("Dataset catalog is missing or malformed.") from exc
    if name not in documents:
        raise DatasetControlError("Unknown dataset.")
    control = controls[name]
    if control["split"] != PURPOSE_SPLIT[purpose]:
        raise DatasetControlError(
            "Dataset split does not match the evaluation purpose; locked release data is not for tuning."
        )
    if purpose == "release" and not control["all_splits_present"]:
        raise DatasetControlError(
            "Release evaluation requires separate development, validation and locked release sets."
        )
    return documents[name], control
