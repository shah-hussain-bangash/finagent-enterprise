"""Fail-closed validation for a future extractive investigation assistant.

This module does not call a model or grant access. Callers must provide only evidence
already authorized for the case and retrieved for the current request. Exact quotation
validates provenance, not relevance, truth, completeness or resistance to prompt injection.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

EvidenceReference = Annotated[
    str, StringConstraints(pattern=r"^ev:[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
]
QuoteText = Annotated[str, StringConstraints(min_length=1, max_length=500)]


class ExtractiveQuote(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    evidence_ref: EvidenceReference
    quote: QuoteText


class ExtractiveDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    status: Literal["SUPPORTED_EXCERPTS", "INSUFFICIENT_EVIDENCE"]
    quotes: list[ExtractiveQuote] = Field(max_length=5)


def validate_extractive_draft(draft: ExtractiveDraft, retrieved: dict[str, list[str]]) -> dict[str, object]:
    """Return exact excerpts only if every quote matches a source retrieved for this request.

    The caller must not persist or display a rejected draft. Error codes intentionally omit its text.
    """
    if draft.status == "INSUFFICIENT_EVIDENCE":
        if draft.quotes:
            return {"accepted": False, "reason": "ABSTENTION_CONTAINS_QUOTES"}
        return {"accepted": True, "status": "INSUFFICIENT_EVIDENCE", "quotes": []}
    if not draft.quotes:
        return {"accepted": False, "reason": "NO_CITED_EXCERPTS"}
    seen: set[tuple[str, str]] = set()
    output: list[dict[str, str]] = []
    for item in draft.quotes:
        if not item.quote.strip():
            return {"accepted": False, "reason": "EMPTY_EXCERPT"}
        if item.evidence_ref not in retrieved:
            return {"accepted": False, "reason": "EVIDENCE_NOT_RETRIEVED"}
        if not any(item.quote in text for text in retrieved[item.evidence_ref]):
            return {"accepted": False, "reason": "EXCERPT_NOT_IN_SOURCE"}
        key = (item.evidence_ref, item.quote)
        if key in seen:
            return {"accepted": False, "reason": "DUPLICATE_EXCERPT"}
        seen.add(key)
        output.append({"evidence_ref": item.evidence_ref, "quote": item.quote})
    return {"accepted": True, "status": "SUPPORTED_EXCERPTS", "quotes": output}
