"""Policy knowledge endpoints (SRS §12 `POST /knowledge/search`; prompt §14, §22; FR-017..020)."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Request

from finagent.api.deps import Factory, Principal, charge
from finagent.database.session import transaction
from finagent.documents.scanner import MalwareScanner
from finagent.integrations.factory import ConnectorFactory
from finagent.services.knowledge_service import citations, ingestion, retrieval
from finagent.services.knowledge_service.citations import CitationValidationRequest
from finagent.services.knowledge_service.ingestion import LifecycleRequest, PolicyVersionIn
from finagent.services.knowledge_service.retrieval import Retriever, SearchRequest

knowledge = APIRouter(prefix="/api/v1/knowledge", tags=["knowledge"])
SEARCH_COST = 5  # quota units: search is an expensive operation (NQ-028)


def _retriever(request: Request) -> Retriever:
    r: Retriever = request.app.state.retriever
    return r


def _scanner(request: Request) -> MalwareScanner:
    scanner: MalwareScanner = request.app.state.document_ports.scanner
    return scanner


@knowledge.post("/search")
def search(body: SearchRequest, request: Request, ctx: Principal, factory: Factory) -> dict[str, Any]:
    """Permission-filtered, date-applicable search. Abstains with INSUFFICIENT_EVIDENCE."""
    charge(request, ctx, SEARCH_COST)
    with transaction(factory) as s:
        return retrieval.search(s, ctx, body, _retriever(request))


@knowledge.get("/policies")
def list_policies(ctx: Principal, factory: Factory) -> dict[str, Any]:
    with transaction(factory) as s:
        return ingestion.list_versions(s, ctx)


@knowledge.post("/policies", status_code=201)
def create_policy_version(
    body: PolicyVersionIn, request: Request, ctx: Principal, factory: Factory
) -> dict[str, Any]:
    """New DRAFT version (scanned, parsed, indexed). It is NOT retrievable until approved."""
    with transaction(factory) as s:
        return ingestion.create_version(s, ctx, body, _scanner(request))


@knowledge.post("/sync")
def sync_policies(request: Request, ctx: Principal, factory: Factory) -> dict[str, Any]:
    """Pull the tenant's policy repository (SYNTHETIC mock bank locally) as DRAFT versions."""
    settings = request.app.state.settings
    return ingestion.sync_from_source(factory, ctx, ConnectorFactory(settings, factory), _scanner(request))


@knowledge.get("/policy-versions/{version_id}")
def get_policy_version(version_id: str, ctx: Principal, factory: Factory) -> dict[str, Any]:
    with transaction(factory) as s:
        return ingestion.get_version(s, ctx, version_id)


@knowledge.post("/policy-versions/{version_id}/reindex")
def reindex_policy_version(version_id: str, ctx: Principal, factory: Factory) -> dict[str, Any]:
    with transaction(factory) as s:
        return ingestion.reindex(s, ctx, version_id)


@knowledge.post("/policy-versions/{version_id}/{action}")
def policy_lifecycle(
    version_id: str,
    action: Literal["approve", "retire", "revoke"],
    body: LifecycleRequest,
    ctx: Principal,
    factory: Factory,
) -> dict[str, Any]:
    with transaction(factory) as s:
        return ingestion.change_lifecycle(s, ctx, version_id, action.upper(), body)


@knowledge.get("/chunks/{chunk_id}")
def open_citation(chunk_id: str, ctx: Principal, factory: Factory) -> dict[str, Any]:
    """Open the exact cited revision (access re-checked now)."""
    with transaction(factory) as s:
        return citations.open_chunk(s, ctx, chunk_id)


@knowledge.post("/citations/validate")
def validate_citation(body: CitationValidationRequest, ctx: Principal, factory: Factory) -> dict[str, Any]:
    with transaction(factory) as s:
        return citations.validate_endpoint(s, ctx, body)
