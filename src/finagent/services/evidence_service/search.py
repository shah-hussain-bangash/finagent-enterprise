"""Bounded, literal search over already authorized current case evidence.

This is retrieval only: document text is never treated as an instruction and no model is invoked.
"""

from __future__ import annotations

from typing import Any

MAX_ITEMS = 100
MAX_VALUES_PER_ITEM = 500
MAX_CHARS_PER_VALUE = 10000
MAX_MATCHES = 20


def _match_excerpt(text: str, needle: str) -> tuple[str, bool]:
    """Map a case-folded match back to original characters, including Unicode expansions."""
    folded_offset = text.casefold().find(needle)
    folded_end = folded_offset + len(needle)
    current = 0
    match_start = 0
    match_end = 0
    found_start = False
    for index, character in enumerate(text):
        after = current + len(character.casefold())
        if not found_start and after > folded_offset:
            match_start = index
            found_start = True
        if after >= folded_end:
            match_end = index + 1
            break
        current = after
    start = max(0, match_start - 60)
    end = min(len(text), max(match_end, start + 200))
    return text[start:end], start > 0 or end < len(text)


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
                if needle in text.casefold():
                    excerpt, truncated = _match_excerpt(text, needle)
                    matches.append({
                        "evidence_ref": f"ev:{item['evidence_item_id']}",
                        "source_type": item["source_type"],
                        "source_ref": item["source_ref"],
                        "version_no": item["version_no"],
                        "freshness": item["freshness"],
                        "completeness": item["completeness"],
                        "as_of": item["as_of"],
                        "field_path": path,
                        "excerpt": excerpt,
                        "excerpt_truncated": truncated or len(value) > MAX_CHARS_PER_VALUE,
                    })
    return {
        "matches": matches,
        "search_limited": limited,
        "scope": "CURRENT_EVIDENCE",
        "notice": "Literal text matches only; no AI answer or risk determination. Sources may be stale or incomplete.",
    }
