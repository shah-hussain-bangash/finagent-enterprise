"""Build a fully gathered, snapshotted SYNTHETIC case for capability and workflow tests."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from finagent.agents.gateway import ModelGateway
from finagent.agents.prep import CaseFacts, prepare
from finagent.agents.providers import MockModelProvider
from finagent.agents.runner import RunScope
from finagent.core.config import Settings
from finagent.database.seeds import sid
from finagent.database.session import session_factory, transaction
from finagent.documents.runtime import sandbox_config
from finagent.integrations.factory import ConnectorFactory
from finagent.integrations.tool_gateway.gateway import ExecutionContext, ToolGateway
from finagent.security.authentication.resolver import resolve_principal_by_id
from finagent.services.document_service.processing import ProcessingConfig
from finagent.services.evidence_service.gathering import gather_case_evidence, process_case_documents
from finagent.services.evidence_service.snapshots import create_snapshot
from finagent.services.knowledge_service.retrieval import LocalBM25Retriever
from tests.helpers import assigned_analyst, ingest, principal, subject

PADMIN = subject("a", "policyadmin1")
GATHER_TOOLS = frozenset(
    {
        "get_customer_profile",
        "list_kyc_documents",
        "get_transactions",
        "get_sanctions_summary",
        "fetch_kyc_document",
    }
)


def approve_policies(client: Any, token: Any) -> None:
    assert client.post("/api/v1/knowledge/sync", headers=token(PADMIN)).status_code == 200
    for v in client.get("/api/v1/knowledge/policies", headers=token(PADMIN)).json()["items"]:
        if v["effective_to"] is None and v["approval_status"] == "DRAFT":
            r = client.post(
                f"/api/v1/knowledge/policy-versions/{v['policy_version_id']}/approve",
                json={"reason": "synthetic test approval"},
                headers=token(PADMIN),
            )
            assert r.status_code == 200, r.text


@dataclass
class Prepared:
    client: Any
    settings: Settings
    factory: Any
    case_id: str
    analyst: str
    snapshot_id: uuid.UUID

    def principal(self) -> Any:
        with transaction(self.factory) as s:
            return resolve_principal_by_id(
                s, sid("tenant:a"), principal("a", self.analyst.split(".")[1].split("@")[0])
            )

    def facts(self) -> CaseFacts:
        with transaction(self.factory) as s:
            f = prepare(s, sid("tenant:a"), uuid.UUID(self.case_id), self.snapshot_id, self.settings)
            s.expunge_all()
            return f

    def scope(
        self, provider: MockModelProvider | None = None, **over: Any
    ) -> tuple[RunScope, MockModelProvider]:
        settings = self.settings.model_copy(update={"model_retry_backoff_seconds": 0, **over})
        provider = provider or MockModelProvider()
        gw = ModelGateway(self.factory, provider, settings, sleep=lambda _s: None)
        return RunScope(self.factory, gw, self.principal(), None, "test-step", LocalBM25Retriever()), provider


def prepared_case(client: Any, token: Any, settings: Settings, *, resolve_conflict: bool = True) -> Prepared:
    approve_policies(client, token)
    case_id = ingest(client, token)["case_id"]
    analyst = assigned_analyst(client, token, case_id)
    factory = session_factory(settings.database_url.get_secret_value())
    with transaction(factory) as s:
        p = resolve_principal_by_id(s, sid("tenant:a"), principal("a", analyst.split(".")[1].split("@")[0]))
    ctx = ExecutionContext(
        tenant_id=sid("tenant:a"),
        case_id=uuid.UUID(case_id),
        run_id=None,
        step_id="gather",
        capability_id="SVC-EVIDENCE",
        principal=p,
        allowed_tools=GATHER_TOOLS,
        data_classes=frozenset({"CONFIDENTIAL", "RESTRICTED"}),
    )
    gw = ToolGateway(factory, ConnectorFactory(settings, factory), settings)
    ports = client.app.state.document_ports
    gather_case_evidence(factory, gw, ctx, ["customer-test-001"], ports)
    cfg = ProcessingConfig(sandbox_config(settings), settings.document_max_attempts)
    process_case_documents(factory, ports.store, cfg, sid("tenant:a"), uuid.UUID(case_id))
    if resolve_conflict:
        ev = client.get(f"/api/v1/cases/{case_id}/evidence", headers=token(analyst)).json()
        for c in ev["conflicts"]:
            if c["status"] == "OPEN":
                r = client.post(
                    f"/api/v1/cases/{case_id}/evidence-conflicts/{c['conflict_id']}/resolution",
                    json={"resolution": "USE_A", "rationale": "core banking value verified at onboarding"},
                    headers=token(analyst),
                )
                assert r.status_code == 200, r.text
    with transaction(factory) as s:
        snap, _ = create_snapshot(
            s,
            tenant_id=sid("tenant:a"),
            case_id=uuid.UUID(case_id),
            created_by="test",
            max_age=timedelta(hours=settings.source_max_age_hours),
        )
        snap_id = snap.id
    return Prepared(client, settings, factory, case_id, analyst, snap_id)
