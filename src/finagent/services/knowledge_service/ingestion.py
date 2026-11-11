"""Policy ingestion and lifecycle (FR-017, FR-018, AER-011, prompt §14, DATA-D07).

Pipeline per version: RECEIVED -> SCANNED (malware scan of the policy text) -> PARSED (sections
validated) -> INDEXED (section-aware chunks). Indexing never makes a policy usable: only a Policy
Administrator's explicit APPROVE (MFA step-up) does. RETIRE and REVOKE deactivate chunks in the
same transaction, so retrieval and any cache keyed on `index_version` stop returning them at once.

Ownership, effective dates, applicability and allowed roles are explicit metadata. Policies
synced from the bank's policy repository are SYNTHETIC in this environment and arrive as DRAFT.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from datetime import UTC, date, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from finagent.core.correlation import get_correlation_id
from finagent.core.errors import AppError, ErrorCode, not_found
from finagent.database.models import KnowledgeChunk, OutboxEvent, PolicyDocument, PolicyVersion
from finagent.documents.scanner import MalwareScanner, ScanVerdict
from finagent.domain.fingerprint import fingerprint
from finagent.integrations.factory import ConnectorFactory
from finagent.security.authorization.policy import Permission, PrincipalContext, Role, require
from finagent.services.audit_service import writer as audit
from finagent.services.case_service.transitions import Actor

CHUNK_WORDS = 120
CHUNK_OVERLAP = 20
SearchRole = Literal["ANALYST", "REVIEWER", "AML_SUPERVISOR", "POLICY_ADMIN"]
SEARCH_ROLES: tuple[SearchRole, ...] = ("AML_SUPERVISOR", "ANALYST", "POLICY_ADMIN", "REVIEWER")
_KEY = r"^[a-z0-9][a-z0-9._-]{1,99}$"
if {r.value for r in (Role.ANALYST, Role.REVIEWER, Role.AML_SUPERVISOR, Role.POLICY_ADMIN)} != set(
    SEARCH_ROLES
):
    raise RuntimeError("SEARCH_ROLES out of step with the role model")


class SectionIn(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    section: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
    heading: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50_000)]
    page: int | None = Field(default=None, ge=1)


class PolicyVersionIn(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_key: str = Field(pattern=_KEY)
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=300)]
    department: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=64)]
    owner_label: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=200)]
    classification: Literal["PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED"]
    alert_types: list[Annotated[str, StringConstraints(pattern=r"^[A-Z0-9_]{2,64}$")]] = Field(
        default_factory=list
    )
    version_label: Annotated[str, StringConstraints(pattern=r"^[0-9A-Za-z.\-]{1,32}$")]
    effective_from: date
    effective_to: date | None = None
    allowed_roles: list[SearchRole] = Field(default_factory=lambda: list(SEARCH_ROLES), min_length=1)
    sections: list[SectionIn] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def _dates(self) -> PolicyVersionIn:
        if self.effective_to is not None and self.effective_to < self.effective_from:
            raise ValueError("effective_to precedes effective_from")
        if len({s.section for s in self.sections}) != len(self.sections):
            raise ValueError("section identifiers must be unique")
        return self


def _words(text: str) -> list[str]:
    return re.findall(r"\S+", text)


def chunk_sections(sections: list[SectionIn]) -> list[dict[str, Any]]:
    """Section-aware chunks: never span two sections; overlapping windows inside long sections."""
    chunks: list[dict[str, Any]] = []
    for sec in sections:
        words = _words(sec.text)
        start, n = 0, 1
        while True:
            window = words[start : start + CHUNK_WORDS]
            body = " ".join(window)
            chunks.append(
                {
                    "chunk_key": f"s{sec.section}-c{n}"[:64],
                    "section": sec.section,
                    "heading": sec.heading,
                    "page": sec.page,
                    "text": body,
                    "token_count": len(window),
                }
            )
            if start + CHUNK_WORDS >= len(words):
                break
            start, n = start + CHUNK_WORDS - CHUNK_OVERLAP, n + 1
    return chunks


def _event(session: Session, tenant_id: uuid.UUID, version: PolicyVersion, change: str) -> None:
    session.add(
        OutboxEvent(
            tenant_id=tenant_id,
            event_type="policy.version_changed.v1",
            schema_version="1",
            resource_id=str(version.id),
            correlation_id=get_correlation_id(),
            payload={
                "policy_version_id": str(version.id),
                "change": change,
                "index_version": version.index_version,
            },
        )
    )


def _index(session: Session, version: PolicyVersion, sections: list[SectionIn]) -> int:
    """Build the new chunk set, then switch: old chunks inactive, new active, in one transaction."""
    for old in session.scalars(
        select(KnowledgeChunk).where(
            KnowledgeChunk.tenant_id == version.tenant_id, KnowledgeChunk.policy_version_id == version.id
        )
    ):
        old.active = False
    session.flush()
    version.index_version += 1
    chunks = chunk_sections(sections)
    for c in chunks:
        session.add(
            KnowledgeChunk(
                tenant_id=version.tenant_id,
                policy_version_id=version.id,
                chunk_key=f"{c['chunk_key']}-i{version.index_version}"[:64],
                section=c["section"],
                heading=c["heading"],
                page=c["page"],
                text=c["text"],
                text_hash=hashlib.sha256(c["text"].encode()).hexdigest(),
                token_count=c["token_count"],
                active=True,
            )
        )
    version.ingestion_status = "INDEXED"
    session.flush()
    return len(chunks)


def ingest_version(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    body: PolicyVersionIn,
    actor: Actor,
    scanner: MalwareScanner,
    source: str = "MANUAL",
    source_ref: str | None = None,
) -> tuple[PolicyVersion, bool]:
    """Create (or return the identical existing) DRAFT version and index it. Returns (version, created)."""
    doc = session.scalar(
        select(PolicyDocument)
        .where(PolicyDocument.tenant_id == tenant_id, PolicyDocument.policy_key == body.policy_key)
        .with_for_update()
    )
    if doc is None:
        doc = PolicyDocument(
            tenant_id=tenant_id,
            policy_key=body.policy_key,
            title=body.title,
            owner_label=body.owner_label,
            department=body.department,
            classification=body.classification,
            applicability={"alert_types": body.alert_types},
            source=source[:16],
        )
        session.add(doc)
        session.flush()
    content_hash = fingerprint([s.model_dump() for s in body.sections])
    existing = session.scalar(
        select(PolicyVersion).where(
            PolicyVersion.tenant_id == tenant_id,
            PolicyVersion.policy_document_id == doc.id,
            PolicyVersion.version_label == body.version_label,
        )
    )
    if existing is not None:
        if existing.source_hash != content_hash:
            raise AppError(
                ErrorCode.VERSION_CONFLICT,
                "This policy version label already exists with different content; use a new label.",
            )
        return existing, False
    version = PolicyVersion(
        tenant_id=tenant_id,
        policy_document_id=doc.id,
        version_label=body.version_label,
        effective_from=body.effective_from,
        effective_to=body.effective_to,
        approval_status="DRAFT",
        ingestion_status="RECEIVED",
        allowed_roles=list(body.allowed_roles),
        sections=[s.model_dump() for s in body.sections],
        source_hash=content_hash,
        source_ref=source_ref,
        submitted_by=actor.label,
    )
    session.add(version)
    session.flush()
    scan = scanner.scan("\n".join(s.text for s in body.sections).encode())
    if scan.verdict is not ScanVerdict.CLEAN:
        version.ingestion_status, version.status_reason = "FAILED", f"SCAN_{scan.verdict.value}"
        chunks = 0
    else:
        version.ingestion_status = "SCANNED"
        version.ingestion_status = "PARSED"  # sections validated by the input contract
        chunks = _index(session, version, list(body.sections))
    audit.record(
        session,
        tenant_id=tenant_id,
        action="POLICY_VERSION_INGESTED",
        object_type="policy_version",
        object_id=str(version.id),
        outcome="SUCCESS" if version.ingestion_status == "INDEXED" else "FAILED",
        actor_principal_id=actor.principal_id,
        actor_kind=actor.kind,
        details={
            "policy_key": body.policy_key,
            "version_label": body.version_label,
            "chunks": chunks,
            "ingestion_status": version.ingestion_status,
            "source": source,
        },
    )
    _event(session, tenant_id, version, "INGESTED")
    return version, True


def create_version(
    session: Session, ctx: PrincipalContext, body: PolicyVersionIn, scanner: MalwareScanner
) -> dict[str, Any]:
    require(ctx, Permission.POLICY_PUBLISH)
    version, _ = ingest_version(
        session, tenant_id=ctx.tenant_id, body=body, actor=Actor.of(ctx), scanner=scanner
    )
    return version_view(session, version)


def sync_from_source(
    session_factory: Any, ctx: PrincipalContext, connectors: ConnectorFactory, scanner: MalwareScanner
) -> dict[str, Any]:
    """Pull the tenant's policy repository through its own connector credential."""
    from finagent.database.session import transaction

    require(ctx, Permission.POLICY_PUBLISH)
    docs, meta = connectors.policies(ctx.tenant_id).policies()
    created, unchanged = 0, 0
    with transaction(session_factory) as s:
        for d in docs:
            restricted = d.classification not in ("PUBLIC", "INTERNAL", "CONFIDENTIAL")
            body = PolicyVersionIn.model_validate(
                {
                    "policy_key": d.policy_id,
                    "title": d.title,
                    "department": d.department,
                    "owner_label": f"{d.department} policy owner (source: {meta.source_system})",
                    # Unknown source classifications are treated as the most restrictive (fail safe).
                    "classification": "RESTRICTED" if restricted else d.classification,
                    # SYNTHETIC applicability mapping until DATA-D07 defines it per policy.
                    "alert_types": ["TM_STRUCTURING"] if d.department == "AML" else [],
                    "version_label": d.version,
                    "effective_from": d.effective_from,
                    "effective_to": d.effective_to,
                    # Per-policy role ACLs are DATA-D07 (OPEN): synced policies default to every
                    # case-working role; the Policy Administrator can publish narrower versions.
                    "allowed_roles": list(SEARCH_ROLES),
                    "sections": [x.model_dump() for x in d.sections],
                }
            )
            _, was_created = ingest_version(
                s,
                tenant_id=ctx.tenant_id,
                body=body,
                actor=Actor.of(ctx),
                scanner=scanner,
                source="CONNECTOR",
                source_ref=f"{meta.source_system}:{d.policy_id}@{d.version}",
            )
            created += int(was_created)
            unchanged += int(not was_created)
    return {"created": created, "unchanged": unchanged, "source_system": meta.source_system}


def _load_version(session: Session, ctx: PrincipalContext, raw_id: str) -> PolicyVersion:
    try:
        vid = uuid.UUID(raw_id)
    except ValueError as exc:
        raise not_found() from exc
    version = session.scalar(
        select(PolicyVersion)
        .where(PolicyVersion.tenant_id == ctx.tenant_id, PolicyVersion.id == vid)
        .with_for_update()
    )
    if version is None:
        raise not_found()
    return version


_TRANSITIONS = {
    "APPROVE": ({"DRAFT"}, "APPROVED"),
    "RETIRE": ({"APPROVED"}, "RETIRED"),
    "REVOKE": ({"DRAFT", "APPROVED", "RETIRED"}, "REVOKED"),
}


class LifecycleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=5, max_length=300)]


def change_lifecycle(
    session: Session, ctx: PrincipalContext, raw_id: str, action: str, body: LifecycleRequest
) -> dict[str, Any]:
    require(ctx, Permission.POLICY_PUBLISH)  # MFA step-up (SPR-005)
    version = _load_version(session, ctx, raw_id)
    allowed_from, target = _TRANSITIONS[action]
    if version.approval_status not in allowed_from:
        raise AppError(
            ErrorCode.CASE_INVALID_STATE,
            f"A {version.approval_status} policy version cannot be {target.lower()}.",
            {"current_status": version.approval_status},
        )
    if action == "APPROVE" and version.ingestion_status != "INDEXED":
        raise AppError(
            ErrorCode.SEMANTIC_VALIDATION_FAILED,
            "Only a successfully indexed version can be approved.",
            {"ingestion_status": version.ingestion_status},
        )
    now = datetime.now(UTC)
    previous = version.approval_status
    version.approval_status, version.status_reason = target, body.reason
    version.status_changed_at, version.status_changed_by = now, ctx.principal_id
    if target == "APPROVED":
        version.approved_by, version.approved_at = ctx.principal_id, now
    else:  # retirement/revocation invalidates retrieval and caches immediately (prompt §14)
        for chunk in session.scalars(
            select(KnowledgeChunk).where(
                KnowledgeChunk.tenant_id == ctx.tenant_id, KnowledgeChunk.policy_version_id == version.id
            )
        ):
            chunk.active = False
        version.index_version += 1
        if target == "REVOKED":
            version.ingestion_status = "DELETED"
    audit.record(
        session,
        tenant_id=ctx.tenant_id,
        action=f"POLICY_VERSION_{target}",
        object_type="policy_version",
        object_id=str(version.id),
        outcome="SUCCESS",
        actor_principal_id=ctx.principal_id,
        actor_kind=ctx.kind.value,
        details={"from": previous, "to": target, "reason": body.reason},
    )
    _event(session, ctx.tenant_id, version, target)
    session.flush()
    return version_view(session, version)


def reindex(session: Session, ctx: PrincipalContext, raw_id: str) -> dict[str, Any]:
    """Safe reindex from the stored, hash-checked sections: the new chunk set is built and swapped
    in one transaction, so a search sees either the old or the new index, never a mix."""
    require(ctx, Permission.POLICY_PUBLISH)
    version = _load_version(session, ctx, raw_id)
    if version.approval_status in ("RETIRED", "REVOKED"):
        raise AppError(ErrorCode.CASE_INVALID_STATE, "Retired or revoked versions are not reindexed.")
    sections = [SectionIn.model_validate(s) for s in version.sections]
    if fingerprint([s.model_dump() for s in sections]) != version.source_hash:
        raise AppError(
            ErrorCode.SEMANTIC_VALIDATION_FAILED, "Stored policy content failed its integrity check."
        )
    chunks = _index(session, version, sections)
    audit.record(
        session,
        tenant_id=ctx.tenant_id,
        action="POLICY_VERSION_REINDEXED",
        object_type="policy_version",
        object_id=str(version.id),
        outcome="SUCCESS",
        actor_principal_id=ctx.principal_id,
        actor_kind=ctx.kind.value,
        details={"chunks": chunks, "index_version": version.index_version},
    )
    _event(session, ctx.tenant_id, version, "REINDEXED")
    return version_view(session, version)


def version_view(session: Session, v: PolicyVersion) -> dict[str, Any]:
    doc = session.scalar(
        select(PolicyDocument).where(
            PolicyDocument.tenant_id == v.tenant_id, PolicyDocument.id == v.policy_document_id
        )
    )
    chunks = session.scalars(
        select(KnowledgeChunk.id).where(
            KnowledgeChunk.tenant_id == v.tenant_id,
            KnowledgeChunk.policy_version_id == v.id,
            KnowledgeChunk.active.is_(True),
        )
    ).all()
    return {
        "policy_version_id": str(v.id),
        "policy_key": doc.policy_key if doc else None,
        "title": doc.title if doc else None,
        "department": doc.department if doc else None,
        "owner_label": doc.owner_label if doc else None,
        "classification": doc.classification if doc else None,
        "applicability": doc.applicability if doc else {},
        "version_label": v.version_label,
        "effective_from": v.effective_from.isoformat(),
        "effective_to": v.effective_to.isoformat() if v.effective_to else None,
        "approval_status": v.approval_status,
        "ingestion_status": v.ingestion_status,
        "allowed_roles": list(v.allowed_roles),
        "source_hash": v.source_hash,
        "source_ref": v.source_ref,
        "submitted_by": v.submitted_by,
        "approved_by": str(v.approved_by) if v.approved_by else None,
        "approved_at": v.approved_at.isoformat() if v.approved_at else None,
        "status_reason": v.status_reason,
        "index_version": v.index_version,
        "active_chunks": len(chunks),
    }


def get_version(session: Session, ctx: PrincipalContext, raw_id: str) -> dict[str, Any]:
    require(ctx, Permission.POLICY_PUBLISH)
    return version_view(session, _load_version(session, ctx, raw_id))


def list_versions(session: Session, ctx: PrincipalContext) -> dict[str, Any]:
    """Policy admins see every version; searchers see approved metadata only (no drafts)."""
    if Permission.POLICY_PUBLISH in ctx.permissions:
        require(ctx, Permission.POLICY_PUBLISH)
        rows = session.scalars(select(PolicyVersion).where(PolicyVersion.tenant_id == ctx.tenant_id)).all()
    else:
        require(ctx, Permission.KNOWLEDGE_SEARCH)
        rows = session.scalars(
            select(PolicyVersion).where(
                PolicyVersion.tenant_id == ctx.tenant_id,
                PolicyVersion.approval_status == "APPROVED",
                PolicyVersion.allowed_roles.overlap([r.value for r in ctx.roles]),
            )
        ).all()
    items = [version_view(session, v) for v in rows]
    items.sort(key=lambda i: (i["policy_key"] or "", i["effective_from"]), reverse=False)
    return {"items": items}
