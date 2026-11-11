"""Retrieval units: chunking, deterministic BM25, Azure adapter filtering/re-check (FR-018, AER-011)."""

from __future__ import annotations

import json
import uuid
from datetime import date
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from finagent.core.errors import AppError
from finagent.services.knowledge_service.citations import lexical_support
from finagent.services.knowledge_service.ingestion import (
    CHUNK_OVERLAP,
    CHUNK_WORDS,
    SectionIn,
    chunk_sections,
)
from finagent.services.knowledge_service.retrieval import (
    AzureAISearchRetriever,
    Candidate,
    LocalBM25Retriever,
    tokens,
)

TENANT = uuid.uuid4()


def cand(text: str, key: str, version: uuid.UUID | None = None) -> Candidate:
    chunk = SimpleNamespace(id=uuid.uuid4(), tenant_id=TENANT, chunk_key=key, heading="H", text=text)
    v = SimpleNamespace(id=version or uuid.uuid4(), effective_from=date(2026, 1, 1), effective_to=None)
    return Candidate(chunk, v, SimpleNamespace(id=uuid.uuid4(), policy_key=key))  # type: ignore[arg-type]


@pytest.mark.req("FR-018")
def test_chunks_respect_sections_and_overlap() -> None:
    long_text = " ".join(f"w{i}" for i in range(CHUNK_WORDS * 2))
    chunks = chunk_sections(
        [
            SectionIn(section="1", heading="A", text="short text"),
            SectionIn(section="2", heading="B", text=long_text),
        ]
    )
    assert [c["chunk_key"] for c in chunks] == ["s1-c1", "s2-c1", "s2-c2", "s2-c3"]
    assert all(c["section"] in ("1", "2") for c in chunks) and chunks[0]["text"] == "short text"
    first, second = chunks[1]["text"].split(), chunks[2]["text"].split()
    assert first[-CHUNK_OVERLAP:] == second[:CHUNK_OVERLAP]


@pytest.mark.req("FR-018", "AER-011")
def test_bm25_is_deterministic_and_ignores_unrelated_text() -> None:
    cands = [
        cand("Proof of address must be dated within 90 days.", "kyc"),
        cand("Repeated cash deposits below the reporting threshold are an indicator.", "aml"),
        cand("Lunch menu for the canteen.", "noise"),
    ]
    r = LocalBM25Retriever()
    first = r.rank("cash deposits threshold", cands, 5)
    assert [c.chunk.chunk_key for c, _ in first] == ["aml"]
    assert first == r.rank("cash deposits threshold", list(reversed(cands)), 5)
    assert r.rank("", cands, 5) == [] and r.rank("cash", [], 5) == []
    assert tokens("The Deposits were REPORTED") == ["deposit", "reported"]


@pytest.mark.req("FR-019", "AER-013")
def test_lexical_support_proxy() -> None:
    passage = "Repeated cash deposits just below the reporting threshold are an indicator requiring review."
    assert lexical_support("cash deposits below the reporting threshold", passage) >= 0.8
    assert lexical_support("the customer committed fraud", passage) == 0.0


@pytest.mark.req("FR-018", "SPR-011", "MST:SEC-001")
@pytest.mark.release_blocker
def test_azure_adapter_filters_by_tenant_and_rechecks_every_hit() -> None:
    allowed = cand("cash deposits", "allowed")
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        seen["key"] = request.headers.get("api-key")
        hits = [
            {"chunk_id": str(uuid.uuid4()), "@search.score": 9.0},
            {"chunk_id": str(allowed.chunk.id), "@search.score": 1.0},
        ]
        return httpx.Response(200, json={"value": hits})

    r = AzureAISearchRetriever(
        "https://s.search.windows.net",
        "idx",
        "secret",
        http=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    out = r.rank("cash", [allowed], 5)
    assert [c.chunk.chunk_key for c, _ in out] == ["allowed"]  # the foreign hit was dropped
    assert (
        f"tenant_id eq '{TENANT}'" in seen["body"]["filter"]
        and str(allowed.version.id) in seen["body"]["filter"]
    )
    assert seen["key"] == "secret"
    failing = AzureAISearchRetriever(
        "https://s.search.windows.net",
        "idx",
        "k",
        http=httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(503))),
    )
    with pytest.raises(AppError):
        failing.rank("cash", [allowed], 5)
    with pytest.raises(ValueError):
        AzureAISearchRetriever("http://insecure", "idx", "k")
