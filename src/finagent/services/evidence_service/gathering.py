"""Authorized evidence gathering for a case (FR-012, BR-004, prompt §6 steps 15-20).

Deterministic orchestration code - not a model - calls the tool gateway, which re-derives tenant,
subjects and window from the database and audits every decision. Each result becomes a versioned
evidence item; KYC documents go through the same scan/store/parse pipeline as uploads.

Failure classification: a transient tool failure is raised (the workflow retries); a denial or a
permanent failure is recorded and surfaces later as an explicit SOURCE_MISSING gap - never as
silently complete evidence.
"""

from __future__ import annotations

import base64
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from finagent.database.models import Document, DocumentVersion
from finagent.database.session import transaction
from finagent.integrations.tool_gateway.gateway import (
    ExecutionContext,
    ToolDeniedError,
    ToolFailedError,
    ToolGateway,
)
from finagent.services.case_service.transitions import Actor
from finagent.services.document_service.ingest import DocumentPorts, ingest_connector_document
from finagent.services.document_service.processing import ProcessingConfig, process_one
from finagent.services.evidence_service.items import EvidenceInput, record_item

SOURCE_TOOLS = (
    ("get_customer_profile", "CUSTOMER_PROFILE"),
    ("list_kyc_documents", "KYC_DOCUMENT_LIST"),
    ("get_transactions", "TRANSACTIONS"),
    ("get_sanctions_summary", "SANCTIONS_SUMMARY"),
)


@dataclass
class GatherSummary:
    recorded: int = 0
    unchanged: int = 0
    material_changes: int = 0
    documents: int = 0
    failures: list[dict[str, str]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "recorded": self.recorded,
            "unchanged": self.unchanged,
            "material_changes": self.material_changes,
            "documents": self.documents,
            "failures": self.failures,
        }


def _evidence(source_type: str, ref: str, output: dict[str, Any], invocation: uuid.UUID) -> EvidenceInput:
    meta = output.get("meta") or {}
    if source_type == "CUSTOMER_PROFILE":
        content: dict[str, Any] = {"profile": output["profile"]}
        classification, completeness = (
            output["profile"].get("classification", "CONFIDENTIAL"),
            "NOT_APPLICABLE",
        )
    elif source_type == "KYC_DOCUMENT_LIST":
        content, classification, completeness = {"documents": output["documents"]}, "CONFIDENTIAL", "COMPLETE"
    elif source_type == "TRANSACTIONS":
        # The as-of time is evidence METADATA (kept in `as_of`); inside the content it would make
        # every re-fetch look like a material change.
        proof = {k: v for k, v in output["completeness"].items() if k != "source_as_of"}
        content = {k: v for k, v in output.items() if k not in ("meta", "completeness")}
        content["completeness"] = proof
        classification = "CONFIDENTIAL"
        completeness = "COMPLETE" if output["completeness"]["status"] == "COMPLETE" else "INCOMPLETE"
    else:
        content, classification, completeness = {"summary": output["summary"]}, "RESTRICTED", "NOT_APPLICABLE"
    return EvidenceInput(
        source_type=source_type,
        source_ref=ref,
        content=content,
        source_system=str(meta.get("source_system", "unknown")),
        retrieved_at=datetime.fromisoformat(str(meta.get("retrieved_at")))
        if meta.get("retrieved_at")
        else datetime.now(UTC),
        as_of=datetime.fromisoformat(str(meta["as_of"])) if meta.get("as_of") else None,
        freshness=str(meta.get("freshness", "UNKNOWN")),
        completeness=completeness,
        classification=classification,
        tool_invocation_id=invocation,
    )


def gather_case_evidence(
    factory: sessionmaker[Session],
    gateway: ToolGateway,
    ctx: ExecutionContext,
    subjects: list[str],
    ports: DocumentPorts,
) -> GatherSummary:
    summary = GatherSummary()
    actor = Actor.system(f"workflow:{ctx.run_id}")
    for ref in subjects:
        doc_list: list[dict[str, Any]] = []
        for tool, source_type in SOURCE_TOOLS:
            try:
                result = gateway.invoke(ctx, tool, {"customer_ref": ref})
            except ToolDeniedError as exc:
                summary.failures.append({"tool": tool, "subject": ref, "reason": exc.reason})
                continue
            except ToolFailedError as exc:
                if exc.transient:
                    raise  # classified transient: the run retries with backoff
                summary.failures.append({"tool": tool, "subject": ref, "reason": exc.kind})
                continue
            if source_type == "KYC_DOCUMENT_LIST":
                doc_list = list(result.output.get("documents", []))
            with transaction(factory) as s:
                rec = record_item(
                    s,
                    tenant_id=ctx.tenant_id,
                    case_id=ctx.case_id,
                    data=_evidence(source_type, ref, result.output, result.invocation_id),
                    created_by=actor.label,
                )
            summary.recorded += int(rec.created)
            summary.unchanged += int(not rec.created)
            summary.material_changes += int(rec.material)
        for doc in doc_list:
            args = {"customer_ref": ref, "document_id": str(doc.get("document_id"))}
            try:
                fetched = gateway.invoke(ctx, "fetch_kyc_document", args)
            except ToolDeniedError as exc:
                summary.failures.append({"tool": "fetch_kyc_document", "subject": ref, "reason": exc.reason})
                continue
            except ToolFailedError as exc:
                if exc.transient:
                    raise
                summary.failures.append({"tool": "fetch_kyc_document", "subject": ref, "reason": exc.kind})
                continue
            meta = fetched.output["document"]
            with transaction(factory) as s:
                ingest_connector_document(
                    s,
                    tenant_id=ctx.tenant_id,
                    case_id=ctx.case_id,
                    source_ref=str(meta["document_id"]),
                    document_type=str(meta["document_type"]),
                    classification=str(meta.get("classification", "CONFIDENTIAL")),
                    customer_ref=ref,
                    media_type=str(fetched.output["media_type"]),
                    data=base64.b64decode(fetched.output["content_b64"]),
                    actor=actor,
                    ports=ports,
                )
            summary.documents += 1
    return summary


def process_case_documents(
    factory: sessionmaker[Session],
    store: Any,
    cfg: ProcessingConfig,
    tenant_id: uuid.UUID,
    case_id: uuid.UUID,
    wait_s: float = 30.0,
) -> dict[str, int]:
    """Parse this case's STORED versions now (SKIP LOCKED; safe alongside the background loop) and
    wait briefly for versions another worker is parsing. Anything still unfinished is reported and
    becomes a DOCUMENT_NOT_READABLE gap rather than blocking forever."""
    deadline = time.monotonic() + wait_s
    while True:
        with transaction(factory) as s:
            rows = s.execute(
                select(DocumentVersion.id, DocumentVersion.status, DocumentVersion.attempts)
                .join(
                    Document,
                    (Document.tenant_id == DocumentVersion.tenant_id)
                    & (Document.id == DocumentVersion.document_id),
                )
                .where(Document.tenant_id == tenant_id, Document.case_id == case_id)
                .where(DocumentVersion.status.in_(("STORED", "PARSING")))
                .with_for_update(of=DocumentVersion, skip_locked=True)
            ).all()
            claim = []
            for vid, status, attempts in rows:
                if status == "STORED":
                    v = s.get(DocumentVersion, vid)
                    if v is not None:
                        v.status, v.attempts, v.updated_at = "PARSING", attempts + 1, datetime.now(UTC)
                        claim.append((vid, attempts + 1))
            pending_elsewhere = sum(1 for _, status, _ in rows if status == "PARSING")
        for vid, attempt in claim:
            process_one(factory, store, cfg, vid, attempt)
        if not claim and (pending_elsewhere == 0 or time.monotonic() > deadline):
            break
        if not claim:
            time.sleep(0.5)
    with transaction(factory) as s:
        statuses = s.execute(
            select(DocumentVersion.status)
            .join(
                Document,
                (Document.tenant_id == DocumentVersion.tenant_id)
                & (Document.id == DocumentVersion.document_id),
            )
            .where(Document.tenant_id == tenant_id, Document.case_id == case_id)
        ).scalars()
        counts: dict[str, int] = {}
        for st in statuses:
            counts[st] = counts.get(st, 0) + 1
    return counts
