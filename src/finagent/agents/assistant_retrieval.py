"""Bounded lexical retrieval for the disabled assistant candidate; not semantic search."""

from __future__ import annotations

import re
from typing import Any

from finagent.services.evidence_service.search import MAX_MATCHES, search_current_evidence

STOPWORDS = frozenset(
    {
        "what",
        "which",
        "when",
        "where",
        "from",
        "that",
        "does",
        "have",
        "with",
        "about",
        "the",
        "and",
        "for",
        "are",
        "was",
    }
)
MAX_TERMS = 5
BILINGUAL_LEXICON_VERSION = "assistant-keywords-0.2-SYNTHETIC"
# Deliberately small, hand-authored exact aliases for development; not a translation service.
_ALIASES = {
    "monthly": ("الشهري",),
    "الشهري": ("monthly",),
    "turnover": ("الإيراد",),
    "الإيراد": ("turnover",),
    "expected": ("المتوقع",),
    "المتوقع": ("expected",),
}
_CANONICAL = {"الشهري": "monthly", "الإيراد": "turnover", "المتوقع": "expected"}


def retrieve_snapshot_excerpts(evidence: dict[str, Any], question: str) -> dict[str, Any]:
    """Match up to five distinct query words within each evidence record.

    This lexical fallback has no semantic or general translation capability. A small
    synthetic bilingual keyword list expands selected terms. Each term counts once, even
    if its alias also matches. Require two query words when two or more are available.
    """
    words = list(
        dict.fromkeys(
            _CANONICAL.get(word, word)
            for word in re.findall(r"[^\W_]+", question.casefold())
            if len(word) >= 3 and word not in STOPWORDS
        )
    )
    terms = words[:MAX_TERMS]
    if not terms:
        return {
            "matches": [],
            "search_limited": False,
            "terms_used": 0,
            "matching_records": 0,
            "top_matched_terms": 0,
            "top_tie_count": 0,
            "bilingual_aliases_used": False,
            "lexicon_version": BILINGUAL_LEXICON_VERSION,
        }
    grouped: dict[str, dict[str, Any]] = {}
    limited = len(words) > MAX_TERMS
    aliases_used = False
    for term in terms:
        alternatives = (term, *_ALIASES.get(term, ()))
        aliases_used = aliases_used or len(alternatives) > 1
        for alternative in alternatives:
            result = search_current_evidence(evidence, alternative)
            limited = limited or result["search_limited"]
            for match in result["matches"]:
                key = match["evidence_ref"]
                group = grouped.setdefault(key, {"terms": set(), "excerpts": []})
                group["terms"].add(term)
                if not any(
                    entry["field_path"] == match["field_path"] and entry["excerpt"] == match["excerpt"]
                    for entry in group["excerpts"]
                ):
                    group["excerpts"].append(match)
    minimum = min(2, len(terms))
    ranked = sorted(
        (value for value in grouped.values() if len(value["terms"]) >= minimum),
        key=lambda value: -len(value["terms"]),
    )
    top_matched_terms = len(ranked[0]["terms"]) if ranked else 0
    top_tie_count = sum(len(group["terms"]) == top_matched_terms for group in ranked)
    matches = [entry for group in ranked for entry in group["excerpts"]]
    if len(matches) > MAX_MATCHES:
        limited = True
    return {
        "matches": matches[:MAX_MATCHES],
        "search_limited": limited,
        "terms_used": len(terms),
        "matching_records": len(ranked),
        "top_matched_terms": top_matched_terms,
        "top_tie_count": top_tie_count,
        "bilingual_aliases_used": aliases_used,
        "lexicon_version": BILINGUAL_LEXICON_VERSION,
    }
