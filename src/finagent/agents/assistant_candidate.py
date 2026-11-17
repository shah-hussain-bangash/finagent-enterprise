"""Disabled-by-default, snapshot-bound candidate for extractive assistance.

No API exposes this candidate. It returns only validated excerpts, not a generated narrative.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from finagent.agents.assistant_grounding import ExtractiveDraft, validate_extractive_draft
from finagent.agents.gateway import CallContext
from finagent.agents.prep import CaseFacts
from finagent.agents.registry import require_enabled
from finagent.agents.runner import RunScope, _input
from finagent.database.session import transaction
from finagent.database.models import EvidenceSnapshot
from finagent.services.case_service.queries import load_scoped_case
from finagent.services.evidence_service.queries import item_view
from finagent.services.evidence_service.search import search_current_evidence
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
    search = search_current_evidence(evidence, query)
    retrieved: dict[str, list[str]] = {}
    for match in search["matches"]:
        retrieved.setdefault(match["evidence_ref"], []).append(match["excerpt"])
    if not retrieved:
        return {"status": "INSUFFICIENT_EVIDENCE", "quotes": [], "search_limited": search["search_limited"]}

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
        return {"status": "REJECTED", "reason": checked["reason"], "quotes": [], "usage": usage.as_dict()}
    return {
        "status": checked["status"], "quotes": checked["quotes"], "usage": usage.as_dict(),
        "snapshot_id": str(facts.snapshot.id), "search_limited": search["search_limited"],
        "notice": "Quoted source text is untrusted. Exact matching does not establish relevance, truth or completeness.",
    }
