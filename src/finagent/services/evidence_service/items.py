"""Versioned evidence items (FR-016, DIR-017).

`record_item` is idempotent on (content, as-of): re-fetching identical content at the same as-of
returns the current version. Different content - or the same content at a newer as-of - creates
a new version and marks the previous one SUPERSEDED; nothing is overwritten. Only a CONTENT change
is material (FR-006): it is reported to the review service, which invalidates affected approvals.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import set_committed_value

from finagent.database.models import ApprovalRequest, EvidenceItem, EvidenceItemPayload
from finagent.domain.fingerprint import fingerprint
from finagent.services.audit_service import writer as audit
from finagent.services.review_service.invalidation import material_change


@dataclass(frozen=True)
class EvidenceInput:
    source_type: str
    source_ref: str
    content: dict[str, Any]
    source_system: str
    retrieved_at: datetime
    as_of: datetime | None
    freshness: str  # FRESH | STALE | UNKNOWN
    completeness: str  # COMPLETE | INCOMPLETE | NOT_APPLICABLE
    classification: str
    tool_invocation_id: uuid.UUID | None = None
    document_version_id: uuid.UUID | None = None
    change_reason: str | None = None


@dataclass(frozen=True)
class Recorded:
    item: EvidenceItem
    created: bool
    material: bool


def evidence_content(item: EvidenceItem) -> dict[str, Any]:
    """Read loaded content without changing a retention-purged parent row."""
    content = item.content
    return {} if content is None else content


def hydrate_evidence_payloads(
    session: Session,
    items: list[EvidenceItem],
) -> list[EvidenceItem]:
    """Prefer purgeable payload content while retaining legacy fallback.

    set_committed_value changes the in-memory loaded value without marking the
    immutable EvidenceItem row dirty, so later flushes cannot accidentally
    attempt an UPDATE of the immutable parent record.
    """
    if not items:
        return items

    item_ids = [item.id for item in items]
    payloads = {
        payload.evidence_item_id: payload
        for payload in session.scalars(
            select(EvidenceItemPayload).where(EvidenceItemPayload.evidence_item_id.in_(item_ids))
        )
    }

    for item in items:
        payload = payloads.get(item.id)

        if payload is not None:
            set_committed_value(item, "content", payload.content)
        elif item.content is None:
            # Retention may legitimately purge the payload while preserving
            # the evidence identity, content hash, provenance, and links.
            # Keep the database parent NULL, but expose an empty mapping
            # in memory so downstream readers can safely use .get(...).
            set_committed_value(item, "content", {})

    return items


def current_items(session: Session, tenant_id: uuid.UUID, case_id: uuid.UUID) -> list[EvidenceItem]:
    items = list(
        session.scalars(
            select(EvidenceItem)
            .where(
                EvidenceItem.tenant_id == tenant_id,
                EvidenceItem.case_id == case_id,
                EvidenceItem.status == "CURRENT",
            )
            .order_by(EvidenceItem.source_type, EvidenceItem.source_ref)
        )
    )
    return hydrate_evidence_payloads(session, items)


def record_item(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    case_id: uuid.UUID,
    data: EvidenceInput,
    created_by: str,
    actor_principal_id: uuid.UUID | None = None,
    actor_kind: str = "SYSTEM",
) -> Recorded:
    content_hash = fingerprint(data.content)
    current = session.scalar(
        select(EvidenceItem)
        .where(
            EvidenceItem.tenant_id == tenant_id,
            EvidenceItem.case_id == case_id,
            EvidenceItem.source_type == data.source_type,
            EvidenceItem.source_ref == data.source_ref,
            EvidenceItem.status == "CURRENT",
        )
        .with_for_update()
    )
    if current is not None and current.content_hash == content_hash and current.as_of == data.as_of:
        return Recorded(current, created=False, material=False)
    material = current is not None and current.content_hash != content_hash
    reason = data.change_reason or (
        None if current is None else ("CONTENT_CHANGED" if material else "REFRESHED_SAME_CONTENT")
    )
    if current is not None:
        current.status = "SUPERSEDED"
        session.flush()  # release the partial unique index before inserting the new version
    item = EvidenceItem(
        tenant_id=tenant_id,
        case_id=case_id,
        source_type=data.source_type,
        source_ref=data.source_ref,
        version_no=(current.version_no + 1) if current else 1,
        status="CURRENT",
        content=None,
        content_hash=content_hash,
        source_system=data.source_system,
        retrieved_at=data.retrieved_at,
        as_of=data.as_of,
        freshness=data.freshness,
        completeness=data.completeness,
        classification=data.classification,
        supersedes_id=current.id if current else None,
        change_reason=reason,
        tool_invocation_id=data.tool_invocation_id,
        document_version_id=data.document_version_id,
        created_by=created_by,
    )
    session.add(item)
    session.flush()

    session.add(
        EvidenceItemPayload(
            tenant_id=tenant_id,
            evidence_item_id=item.id,
            content=data.content,
        )
    )
    session.flush()

    audit.record(
        session,
        tenant_id=tenant_id,
        action="EVIDENCE_VERSIONED" if current else "EVIDENCE_RECORDED",
        object_type="evidence_item",
        object_id=str(item.id),
        outcome="SUCCESS",
        actor_principal_id=actor_principal_id,
        actor_kind=actor_kind,
        case_id=case_id,
        details={
            "source_type": data.source_type,
            "version_no": item.version_no,
            "content_hash": content_hash,
            "material": material,
            "reason": reason,
        },
    )
    if current is None and session.scalar(
        select(ApprovalRequest.id)
        .where(
            ApprovalRequest.tenant_id == tenant_id,
            ApprovalRequest.case_id == case_id,
            ApprovalRequest.status.in_(("PENDING", "APPROVED")),
        )
        .limit(1)
    ):
        # A NEW source after submission changes the reviewed evidence set (FR-006).
        material_change(
            session,
            tenant_id=tenant_id,
            case_id=case_id,
            kind="NEW_EVIDENCE_AFTER_SUBMISSION",
            actor_principal_id=actor_principal_id,
            actor_kind=actor_kind,
            refs={"new_item_id": str(item.id), "source_type": data.source_type},
        )
    if material and current is not None:
        material_change(
            session,
            tenant_id=tenant_id,
            case_id=case_id,
            kind="EVIDENCE_CONTENT_CHANGED",
            actor_principal_id=actor_principal_id,
            actor_kind=actor_kind,
            refs={"superseded_item_id": str(current.id), "new_item_id": str(item.id)},
        )
    return Recorded(item, created=True, material=material)
