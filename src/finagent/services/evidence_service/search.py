"""Bounded, literal search over already authorized current case evidence.

This is retrieval only: document text is never treated as an instruction and no model is invoked.
"""

from __future__ import annotations

from typing import Any

MAX_ITEMS = 100
MAX_VALUES_PER_ITEM = 500
MAX_CHARS_PER_VALUE = 10000
MAX_MATCHES = 20


def search_current_evidence(evidence: dict[str, Any], query: str) -> dict[str, Any]:
    needle = query.casefold()
    matches: list[dict[str, Any]] = []
    limited = False
    items = evidence.get("items", [])
    if len(items) > MAX_ITEMS:
        limited = True
    for item in items[:MAX_ITEMS]:
        stack: list[tuple[Any, str]] = [(item.get("content"), "content")]
        visited = 0
        while stack:
            if visited >= MAX_VALUES_PER_ITEM or len(matches) >= MAX_MATCHES:
                limited = True
                break
            value, path = stack.pop()
            visited += 1
            if isinstance(value, dict):
                stack.extend((child, f"{path}.{key}") for key, child in list(value.items())[:MAX_VALUES_PER_ITEM])
                if len(value) > MAX_VALUES_PER_ITEM:
                    limited = True
            elif isinstance(value, list):
                stack.extend((child, f"{path}[{index}]") for index, child in enumerate(value[:MAX_VALUES_PER_ITEM]))
                if len(value) > MAX_VALUES_PER_ITEM:
                    limited = True
            elif isinstance(value, str):
                if len(value) > MAX_CHARS_PER_VALUE:
                    limited = True
                text = value[:MAX_CHARS_PER_VALUE]
                # Do not use the folded string's offset: Unicode case-folding can change length.
                if needle in text.casefold():
                    matches.append({
                        "evidence_ref": f"ev:{item['evidence_item_id']}",
                        "source_type": item["source_type"],
                        "source_ref": item["source_ref"],
                        "version_no": item["version_no"],
                        "freshness": item["freshness"],
                        "completeness": item["completeness"],
                        "as_of": item["as_of"],
                        "field_path": path,
                        "excerpt": text[:200],
                        "excerpt_truncated": len(value) > 200,
                    })
    return {
        "matches": matches,
        "search_limited": limited,
        "scope": "CURRENT_EVIDENCE",
        "notice": "Literal text matches only; no AI answer or risk determination. Sources may be stale or incomplete.",
    }
