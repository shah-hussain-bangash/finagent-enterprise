"""Permission-filtered, date-applicable policy retrieval (FR-018..020, AER-011..013, prompt §14).

Order (ACL in the data layer, never in a prompt):
  principal -> trusted tenant -> APPROVED + INDEXED versions -> effective on the as-of date ->
  role ACL overlap -> applicability (alert type) -> rank (BM25 locally / Azure AI Search) ->
  re-check every hit against the eligible set -> citations -> outcome.

Outcomes: ANSWERED, INSUFFICIENT_EVIDENCE (nothing applicable: abstain and ask the policy owner;
general model knowledge is never substituted - AER-012), CONFLICTING_GUIDANCE (two approved
versions of one policy effective on the same date: surfaced and escalated, never silently picked).
Versions not effective TODAY are labelled historical.
"""

from __future__ import annotations

import math
import re
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Annotated, Any, Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy import and_, or_, select, text
from sqlalchemy.orm import Session

from finagent.core.config import Settings
from finagent.core.errors import AppError, ErrorCode, not_found
from finagent.database.models import Alert, KnowledgeChunk, PolicyDocument, PolicyVersion, RetrievalRun
from finagent.domain.fingerprint import fingerprint
from finagent.security.authorization.policy import Permission, PrincipalContext, require
from finagent.services.audit_service import writer as audit
from finagent.services.case_service.queries import load_scoped_case

_TOKEN = re.compile(r"[a-z0-9]+")
STOPWORDS = frozenset(
    {
        *("a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has", "have", "in", "is"),
        *("it", "its", "of", "on", "or", "that", "the", "this", "to", "was", "were", "will", "with"),
        *("which", "who", "what", "when", "where", "how", "should", "shall", "must", "may", "can"),
        *("not", "no", "any", "all"),
    }
)
INSUFFICIENT_MESSAGE = (
    "No approved policy applicable to this question and date was found for your access. "
    "Request policy-owner review; general model knowledge is not substituted (AER-012)."
)


def tokens(value: str) -> list[str]:
    out = []
    for t in _TOKEN.findall(value.lower()):
        if t in STOPWORDS or len(t) < 2:
            continue
        if len(t) > 4 and t.endswith("s") and not t.endswith("ss"):
            t = t[:-1]  # naive plural folding: deposits -> deposit
        out.append(t)
    return out


@dataclass(frozen=True)
class Candidate:
    chunk: KnowledgeChunk
    version: PolicyVersion
    doc: PolicyDocument


class Retriever(Protocol):
    name: str

    def rank(self, query: str, candidates: list[Candidate], top_k: int) -> list[tuple[Candidate, float]]: ...


class LocalBM25Retriever:
    """Deterministic BM25 over the already-authorized candidate set (ADR-0007 local adapter)."""

    name = "local-bm25-v1"

    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1, self.b = k1, b

    def rank(self, query: str, candidates: list[Candidate], top_k: int) -> list[tuple[Candidate, float]]:
        q = tokens(query)
        if not q or not candidates:
            return []
        docs = [tokens(f"{c.chunk.heading} {c.chunk.text}") for c in candidates]
        avgdl = sum(len(d) for d in docs) / len(docs) or 1.0
        df: Counter[str] = Counter()
        for d in docs:
            df.update(set(d))
        n = len(docs)
        scored = []
        for cand, d in zip(candidates, docs, strict=True):
            tf = Counter(d)
            score = 0.0
            for term in set(q):
                if term not in tf:
                    continue
                idf = math.log(1 + (n - df[term] + 0.5) / (df[term] + 0.5))
                freq = tf[term]
                score += (
                    idf * freq * (self.k1 + 1) / (freq + self.k1 * (1 - self.b + self.b * len(d) / avgdl))
                )
            if score > 0:
                scored.append((cand, round(score, 6)))
        scored.sort(key=lambda x: (-x[1], x[0].chunk.chunk_key, str(x[0].chunk.id)))  # deterministic
        return scored[:top_k]


class AzureAISearchRetriever:
    """Azure AI Search (BM25 + optional semantic ranking configured on the index). LIVE NOT VERIFIED.

    The query carries a filter restricted to the already-authorized version ids, AND every hit is
    re-checked against the candidate set afterwards, so a stale or mis-filtered index entry (e.g. a
    revoked chunk not yet deleted remotely) can never reach model context.
    """

    name = "azure-ai-search"

    def __init__(
        self,
        endpoint: str,
        index: str,
        api_key: str,
        api_version: str = "2024-07-01",
        http: httpx.Client | None = None,
    ) -> None:
        if not endpoint.startswith("https://"):
            raise ValueError("azure search endpoint must use https")
        self._url = f"{endpoint.rstrip('/')}/indexes/{index}/docs"
        self._params = {"api-version": api_version}
        self._headers = {"api-key": api_key, "Content-Type": "application/json"}
        self._http = http or httpx.Client(timeout=10.0)

    def rank(self, query: str, candidates: list[Candidate], top_k: int) -> list[tuple[Candidate, float]]:
        if not candidates:
            return []
        by_id = {str(c.chunk.id): c for c in candidates}
        versions = sorted({str(c.version.id) for c in candidates})
        tenants = {str(c.chunk.tenant_id) for c in candidates}
        if len(tenants) != 1:
            raise ValueError("candidates must belong to one tenant")
        body = {
            "search": query,
            "top": top_k * 3,
            "filter": (
                f"tenant_id eq '{tenants.pop()}' and "
                f"search.in(policy_version_id, '{','.join(versions)}', ',')"
            ),
            "select": "chunk_id",
        }
        r = self._http.post(f"{self._url}/search", params=self._params, headers=self._headers, json=body)
        if r.status_code >= 400:
            raise AppError(ErrorCode.DEPENDENCY_UNAVAILABLE, "The policy search service is unavailable.")
        hits = []
        for doc in r.json().get("value", []):
            cand = by_id.get(str(doc.get("chunk_id")))
            if cand is not None:  # re-check: anything outside the authorized set is dropped
                hits.append((cand, float(doc.get("@search.score", 0.0))))
        return hits[:top_k]

    def publish(self, chunks: list[KnowledgeChunk]) -> None:
        actions = [
            {
                "@search.action": "mergeOrUpload",
                "chunk_id": str(c.id),
                "tenant_id": str(c.tenant_id),
                "policy_version_id": str(c.policy_version_id),
                "heading": c.heading,
                "text": c.text,
            }
            for c in chunks
        ]
        self._post_index(actions)

    def retract(self, chunk_ids: list[uuid.UUID]) -> None:
        self._post_index([{"@search.action": "delete", "chunk_id": str(i)} for i in chunk_ids])

    def _post_index(self, actions: list[dict[str, Any]]) -> None:
        if not actions:
            return
        r = self._http.post(
            f"{self._url}/index", params=self._params, headers=self._headers, json={"value": actions}
        )
        if r.status_code >= 400:
            raise AppError(ErrorCode.DEPENDENCY_UNAVAILABLE, "The policy search index could not be updated.")


def build_retriever(settings: Settings) -> LocalBM25Retriever | AzureAISearchRetriever:
    if (
        settings.retrieval_backend == "azure"
        and settings.azure_search_endpoint
        and settings.azure_search_api_key
    ):
        return AzureAISearchRetriever(
            settings.azure_search_endpoint,
            settings.azure_search_index,
            settings.azure_search_api_key.get_secret_value(),
        )
    return LocalBM25Retriever()


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    query: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=500)]
    as_of: date | None = None
    case_id: uuid.UUID | None = None
    alert_type: Annotated[str, StringConstraints(pattern=r"^[A-Z0-9_]{2,64}$")] | None = None
    top_k: int = Field(default=5, ge=1, le=10)


def eligible_candidates(
    session: Session, ctx: PrincipalContext, as_of: date, alert_type: str | None
) -> list[Candidate]:
    roles = sorted(r.value for r in ctx.roles)
    stmt = (
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
        .where(
            KnowledgeChunk.tenant_id == ctx.tenant_id,  # trusted tenant, unconditional
            KnowledgeChunk.active.is_(True),
            PolicyVersion.approval_status == "APPROVED",
            PolicyVersion.ingestion_status == "INDEXED",
            PolicyVersion.effective_from <= as_of,
            or_(PolicyVersion.effective_to.is_(None), PolicyVersion.effective_to >= as_of),
            PolicyVersion.allowed_roles.overlap(roles),
        )
    )
    if alert_type:
        stmt = stmt.where(
            or_(
                text(
                    "jsonb_array_length(coalesce(policy_documents.applicability->'alert_types', "
                    "'[]'::jsonb)) = 0"
                ),
                PolicyDocument.applicability["alert_types"].contains([alert_type]),
            )
        )
    return [Candidate(c, v, d) for c, v, d in session.execute(stmt).all()]


def citation_of(cand: Candidate, today: date) -> dict[str, Any]:
    v = cand.version
    current = v.effective_from <= today and (v.effective_to is None or v.effective_to >= today)
    return {
        "chunk_id": str(cand.chunk.id),
        "chunk_key": cand.chunk.chunk_key,
        "policy_key": cand.doc.policy_key,
        "policy_title": cand.doc.title,
        "policy_version_id": str(v.id),
        "version_label": v.version_label,
        "section": cand.chunk.section,
        "heading": cand.chunk.heading,
        "page": cand.chunk.page,
        "effective_from": v.effective_from.isoformat(),
        "effective_to": v.effective_to.isoformat() if v.effective_to else None,
        "historical": not current,
        "text_hash": cand.chunk.text_hash,
        "excerpt": cand.chunk.text,
    }


def search(
    session: Session,
    ctx: PrincipalContext,
    req: SearchRequest,
    retriever: Retriever,
    *,
    run_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    require(ctx, Permission.KNOWLEDGE_SEARCH)
    alert_type, as_of, case_id = req.alert_type, req.as_of, None
    if req.case_id is not None:
        case = load_scoped_case(session, ctx, str(req.case_id))
        if case is None:
            raise not_found()
        case_id = case.id
        alert = session.scalar(
            select(Alert).where(Alert.tenant_id == ctx.tenant_id, Alert.id == case.alert_id)
        )
        if alert is not None:
            alert_type = alert_type or alert.alert_type
            as_of = as_of or alert.occurred_at.date()  # guidance applicable when the activity occurred
    today = datetime.now(UTC).date()
    as_of = as_of or today
    candidates = eligible_candidates(session, ctx, as_of, alert_type)
    ranked = retriever.rank(" ".join(req.query.split()), candidates, req.top_k)
    by_policy: dict[uuid.UUID, set[uuid.UUID]] = {}
    for cand in candidates:
        by_policy.setdefault(cand.doc.id, set()).add(cand.version.id)
    conflicted = {cand.doc.policy_key for cand, _ in ranked if len(by_policy[cand.doc.id]) > 1}
    if not ranked:
        outcome = "INSUFFICIENT_EVIDENCE"
    elif conflicted:
        outcome = "CONFLICTING_GUIDANCE"
    else:
        outcome = "ANSWERED"
    citations = [{**citation_of(c, today), "score": score} for c, score in ranked]
    gaps = []
    if outcome == "INSUFFICIENT_EVIDENCE":
        gaps.append(
            {"code": "NO_APPLICABLE_POLICY", "detail": INSUFFICIENT_MESSAGE, "owner_role": "POLICY_ADMIN"}
        )
    if outcome == "CONFLICTING_GUIDANCE":
        gaps.append(
            {
                "code": "CONFLICTING_POLICY_VERSIONS",
                "detail": (
                    f"More than one approved version is effective on {as_of}: "
                    + ", ".join(sorted(conflicted))
                ),
                "owner_role": "POLICY_ADMIN",
            }
        )
    run = RetrievalRun(
        tenant_id=ctx.tenant_id,
        case_id=case_id,
        run_id=run_id,
        principal_id=ctx.principal_id,
        query_hash=fingerprint(req.query),
        as_of=as_of,
        retriever=retriever.name,
        filters={
            "alert_type": alert_type,
            "roles": sorted(r.value for r in ctx.roles),
            "candidates": len(candidates),
        },
        results=[
            {"chunk_id": c["chunk_id"], "policy_version_id": c["policy_version_id"], "score": c["score"]}
            for c in citations
        ],
        outcome=outcome,
    )
    session.add(run)
    session.flush()
    audit.record(
        session,
        tenant_id=ctx.tenant_id,
        action="KNOWLEDGE_SEARCHED",
        object_type="retrieval_run",
        object_id=str(run.id),
        outcome="SUCCESS",
        actor_principal_id=ctx.principal_id,
        actor_kind=ctx.kind.value,
        case_id=case_id,
        details={  # query hash only: the query text may contain case details
            "retrieval_outcome": outcome,
            "query_hash": run.query_hash,
            "as_of": as_of.isoformat(),
            "results": len(citations),
            "policy_versions": sorted({c["policy_version_id"] for c in citations}),
        },
    )
    return {
        "retrieval_id": str(run.id),
        "outcome": outcome,
        "abstained": outcome != "ANSWERED",
        "as_of": as_of.isoformat(),
        "retriever": retriever.name,
        "citations": citations,
        "evidence_gaps": gaps,
        "policy_versions": sorted(
            {
                (c["policy_key"], c["version_label"], c["policy_version_id"], c["historical"])
                for c in citations
            }
        ),
        "message": INSUFFICIENT_MESSAGE if outcome == "INSUFFICIENT_EVIDENCE" else None,
    }
