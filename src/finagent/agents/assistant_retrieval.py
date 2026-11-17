"""Bounded lexical retrieval for the disabled assistant candidate; not semantic search."""

from __future__ import annotations

import re
from typing import Any

from finagent.services.evidence_service.search import MAX_MATCHES, search_current_evidence

STOPWORDS = frozenset({"what", "which", "when", "where", "from", "that", "does", "have", "with", "about", "the", "and", "for", "are", "was"})
MAX_TERMS = 5


def retrieve_snapshot_excerpts(evidence: dict[str, Any], question: str) -> dict[str, Any]:
    """Match up to five distinct query words within each evidence record.

    This lexical fallback has no semantic or translation capability. Require at least two
    query words when two or more are available; all omissions and limits remain explicit.
    """
    words = list(dict.fromkeys(word for word in re.findall(r"[^\W_]+", question.casefold()) if len(word) >= 3 and word not in STOPWORDS))
    terms = words[:MAX_TERMS]
    if not terms:
        return {"matches": [], "search_limited": False, "terms_used": 0}
    grouped: dict[str, dict[str, Any]] = {}
    limited = len(words) > MAX_TERMS
    for term in terms:
        result = search_current_evidence(evidence, term)
        limited = limited or result["search_limited"]
        for match in result["matches"]:
            key = match["evidence_ref"]
            group = grouped.setdefault(key, {"terms": set(), "excerpts": []})
            group["terms"].add(term)
            if not any(entry["field_path"] == match["field_path"] and entry["excerpt"] == match["excerpt"] for entry in group["excerpts"]):
                group["excerpts"].append(match)
    minimum = min(2, len(terms))
    ranked = sorted((value for value in grouped.values() if len(value["terms"]) >= minimum), key=lambda value: -len(value["terms"]))
    matches = [entry for group in ranked for entry in group["excerpts"]]
    if len(matches) > MAX_MATCHES:
        limited = True
    return {"matches": matches[:MAX_MATCHES], "search_limited": limited, "terms_used": len(terms)}
