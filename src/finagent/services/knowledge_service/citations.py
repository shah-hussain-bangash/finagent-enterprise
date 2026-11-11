"""Structured citation validation and exact-revision opening (FR-019, AER-013, SPR-011, DIR-017).

A citation is valid only if ALL hold, each reported separately so a reviewer sees why it failed:
  exists          the chunk exists in the caller's tenant
  version         same policy version, version label and text hash (content identity, not truth)
  current_access  version still APPROVED, chunk active, caller's roles still allowed (re-checked now)
  location        cited section/page match the chunk
  provenance      (optional) the chunk was returned by a retrieval run of this case/run
  support         lexical support of the claim by the chunk text

`support` is a deterministic LEXICAL proxy (method `lexical-support-v1`): it can show that a claim
is NOT supported; it cannot prove semantic entailment. The UI labels it accordingly.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from finagent.core.errors import not_found
from finagent.database.models import KnowledgeChunk, PolicyDocument, PolicyVersion, RetrievalRun
from finagent.security.authorization.policy import Permission, PrincipalContext, require
from finagent.services.audit_service import writer as audit
from finagent.services.knowledge_service.retrieval import tokens

SUPPORT_METHOD = "lexical-support-v1"
SUPPORT_RATIO = 0.5


@dataclass(frozen=True)
class CitationCheck:
    valid: bool
    checks: dict[str, bool]
    reasons: list[str] = field(default_factory=list)
    support_ratio: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "checks": self.checks,
            "reasons": self.reasons,
            "support_ratio": self.support_ratio,
            "support_method": SUPPORT_METHOD,
        }


def lexical_support(claim: str, passage: str) -> float:
    claim_terms = set(tokens(claim))
    if not claim_terms:
        return 0.0
    return len(claim_terms & set(tokens(passage))) / len(claim_terms)


def _load(
    session: Session, tenant_id: uuid.UUID, raw_chunk_id: str
) -> tuple[KnowledgeChunk, PolicyVersion, PolicyDocument] | None:
    try:
        chunk_id = uuid.UUID(raw_chunk_id)
    except ValueError:
        return None
    row = session.execute(
        select(KnowledgeChunk, PolicyVersion, PolicyDocument)
        .join(
            PolicyVersion,
            and_(
                PolicyVersion.tenant_id == KnowledgeChunk.tenant_id,
                PolicyVersion.id == KnowledgeChunk.policy_version_id,
            ),
        )
        .join(
            PolicyDocument,
            and_(
                PolicyDocument.tenant_id == PolicyVersion.tenant_id,
                PolicyDocument.id == PolicyVersion.policy_document_id,
            ),
        )
        .where(KnowledgeChunk.tenant_id == tenant_id, KnowledgeChunk.id == chunk_id)
    ).first()
    return (row[0], row[1], row[2]) if row else None


def check_citation(
    session: Session,
    ctx: PrincipalContext,
    citation: dict[str, Any],
    claim_text: str | None = None,
    *,
    retrieval_ids: set[uuid.UUID] | None = None,
) -> CitationCheck:
    reasons: list[str] = []
    loaded = _load(session, ctx.tenant_id, str(citation.get("chunk_id", "")))
    checks = {"exists": loaded is not None}
    if loaded is None:
        return CitationCheck(False, checks, ["CITATION_NOT_FOUND"])
    chunk, version, _ = loaded
    checks["version"] = (
        str(version.id) == str(citation.get("policy_version_id"))
        and citation.get("version_label") in (None, version.version_label)
        and citation.get("text_hash") in (None, chunk.text_hash)
    )
    checks["current_access"] = (
        version.approval_status == "APPROVED"
        and chunk.active
        and bool(set(version.allowed_roles) & {r.value for r in ctx.roles})
    )
    checks["location"] = str(citation.get("section")) == chunk.section and citation.get("page") in (
        None,
        chunk.page,
    )
    if retrieval_ids is not None:
        returned = set()
        for run in session.scalars(
            select(RetrievalRun).where(
                RetrievalRun.tenant_id == ctx.tenant_id, RetrievalRun.id.in_(retrieval_ids)
            )
        ):
            returned |= {r["chunk_id"] for r in run.results}
        checks["provenance"] = str(chunk.id) in returned
    ratio = None
    if claim_text is not None:
        ratio = round(lexical_support(claim_text, f"{chunk.heading} {chunk.text}"), 3)
        checks["support"] = ratio >= SUPPORT_RATIO
    names = {
        "version": "CITATION_VERSION_MISMATCH",
        "current_access": "CITATION_ACCESS_OR_STATUS_CHANGED",
        "location": "CITATION_LOCATION_MISMATCH",
        "provenance": "CITATION_NOT_RETRIEVED_FOR_THIS_CASE",
        "support": "CLAIM_NOT_SUPPORTED_BY_CITATION",
    }
    reasons = [names[k] for k, ok in checks.items() if not ok and k in names]
    return CitationCheck(all(checks.values()), checks, reasons, ratio)


class CitationValidationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    citation: dict[str, Any]
    claim_text: str | None = Field(default=None, max_length=4000)


def validate_endpoint(
    session: Session, ctx: PrincipalContext, body: CitationValidationRequest
) -> dict[str, Any]:
    require(ctx, Permission.KNOWLEDGE_SEARCH)
    return check_citation(session, ctx, body.citation, body.claim_text).as_dict()


def open_chunk(session: Session, ctx: PrincipalContext, raw_chunk_id: str) -> dict[str, Any]:
    """Open the EXACT cited revision. Access is re-checked now; revoked text is withheld."""
    require(ctx, Permission.KNOWLEDGE_SEARCH)
    loaded = _load(session, ctx.tenant_id, raw_chunk_id)
    if loaded is None:
        raise not_found()
    chunk, version, doc = loaded
    if not set(version.allowed_roles) & {r.value for r in ctx.roles}:
        raise not_found()  # not permitted looks exactly like absent
    viewable = version.approval_status in ("APPROVED", "RETIRED")
    audit.record(
        session,
        tenant_id=ctx.tenant_id,
        action="CITATION_OPENED",
        object_type="knowledge_chunk",
        object_id=str(chunk.id),
        outcome="SUCCESS" if viewable else "WITHHELD",
        actor_principal_id=ctx.principal_id,
        actor_kind=ctx.kind.value,
        details={"policy_version_id": str(version.id), "approval_status": version.approval_status},
    )
    return {
        "chunk_id": str(chunk.id),
        "chunk_key": chunk.chunk_key,
        "policy_key": doc.policy_key,
        "policy_title": doc.title,
        "policy_version_id": str(version.id),
        "version_label": version.version_label,
        "approval_status": version.approval_status,
        "effective_from": version.effective_from.isoformat(),
        "effective_to": version.effective_to.isoformat() if version.effective_to else None,
        "section": chunk.section,
        "heading": chunk.heading,
        "page": chunk.page,
        "text_hash": chunk.text_hash,
        "active": chunk.active,
        "text": chunk.text if viewable else None,
        "notice": None if viewable else "This policy version was revoked; its text is withheld.",
    }
