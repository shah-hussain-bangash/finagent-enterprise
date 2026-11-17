"""Disabled-by-default, snapshot-bound candidate for extractive assistance.

No API exposes this candidate. It returns only validated excerpts, not a generated narrative.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, cast

from finagent.agents.assistant_grounding import ExtractiveDraft, validate_extractive_draft
from finagent.agents.assistant_retrieval import retrieve_snapshot_excerpts
from finagent.agents.gateway import CallContext
from finagent.agents.prep import CaseFacts
from finagent.agents.registry import require_enabled
from finagent.agents.runner import RunScope, _input
from finagent.database.session import transaction
from finagent.database.models import EvidenceSnapshot
from finagent.services.case_service.queries import load_scoped_case
from finagent.services.evidence_service.queries import item_view
from finagent.services.evidence_service.snapshots import snapshot_items


def run_assistant_candidate(facts: CaseFacts, scope: RunScope, question: str) -> dict[str, Any]:
    """Experimental internal path. The caller must prepare facts from the authorized snapshot."""
    capability = require_enabled("AG-ASSISTANT")  # FUTURE in the normal registry; fail closed.
    query = question.strip()
    if not 3 <= len(query) <= 80:
        raise ValueError("Question must contain between 3 and 80 characters")
    with transaction(scope.factory) as session:
        case = load_scoped_case(session, scope.principal, str(facts.case.id))
        if case is None or scope.principal.tenant_id != facts.tenant_id:
            raise PermissionError("Case access is unavailable")
        if facts.snapshot.case_id != facts.case.id or facts.snapshot.tenant_id != facts.tenant_id:
            raise PermissionError("Snapshot does not belong to this case")
        snapshot = session.get(EvidenceSnapshot, facts.snapshot.id)
        if snapshot is None or snapshot.case_id != case.id or snapshot.tenant_id != scope.principal.tenant_id:
            raise PermissionError("Snapshot does not belong to this case")
        authoritative_items = snapshot_items(session, scope.principal.tenant_id, snapshot.id)
        session.expunge_all()

    # Never send caller-supplied evidence or gap contents to a model; use the persisted snapshot.
    facts = replace(facts, case=case, snapshot=snapshot, items=authoritative_items)

    evidence = {"items": [item_view(item) for item in facts.items if item.source_type in capability.permitted_source_types]}
    search = retrieve_snapshot_excerpts(evidence, query)
    retrieval_context = {
        "terms_used": search["terms_used"], "matching_records": search["matching_records"],
        "top_matched_terms": search["top_matched_terms"], "top_tie_count": search["top_tie_count"],
        "search_limited": search["search_limited"], "lexicon_version": search["lexicon_version"],
        "bilingual_aliases_used": search["bilingual_aliases_used"],
    }
    if search["terms_used"] < 2:
        return {
            "status": "NEEDS_CLARIFICATION", "quotes": [], "retrieval": retrieval_context,
            "notice": "Ask a more specific question with at least two meaningful words. No model was called.",
        }
    retrieved: dict[str, list[str]] = {}
    source_context: dict[str, dict[str, Any]] = {}
    for match in search["matches"]:
        retrieved.setdefault(match["evidence_ref"], []).append(match["excerpt"])
        source_context[match["evidence_ref"]] = {
            "source_type": match["source_type"], "source_ref": match["source_ref"],
            "version_no": match["version_no"], "freshness": match["freshness"],
            "completeness": match["completeness"], "as_of": match["as_of"],
        }
    if not retrieved:
        return {"status": "INSUFFICIENT_EVIDENCE", "quotes": [], "retrieval": retrieval_context}

    candidate_input = _input(
        "AG-ASSISTANT", facts, scope,
        {"task_type": "AG-ASSISTANT", "objective": capability.purpose},
        {"question": query, "retrieved_excerpts": search["matches"]},
    )
    draft, usage = scope.gateway.invoke(
        "AG-ASSISTANT", candidate_input, ExtractiveDraft,
        CallContext(facts.tenant_id, facts.case.id, scope.run_id, scope.step_id),
    )
    checked = validate_extractive_draft(draft, retrieved)
    if not checked["accepted"]:
        return {"status": "REJECTED", "reason": checked["reason"], "quotes": [], "usage": usage.as_dict(), "retrieval": retrieval_context}
    verified_quotes = cast(list[dict[str, str]], checked["quotes"])
    quotes = [{**quote, "source": source_context[quote["evidence_ref"]]} for quote in verified_quotes]
    return {
        "status": checked["status"], "quotes": quotes, "usage": usage.as_dict(),
        "snapshot_id": str(facts.snapshot.id), "retrieval": retrieval_context,
        "notice": "Lexical retrieval uses a small synthetic bilingual keyword list; it is not semantic search or general translation. Tied matches need human review. Quoted source text is untrusted. Exact matching does not establish relevance, truth or completeness.",
    }
