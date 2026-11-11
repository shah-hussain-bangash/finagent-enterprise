"""Completeness, gaps, conflicts and immutable evidence snapshots (FR-015, FR-016, AER-007, AER-010).

Required evidence per alert family is versioned configuration marked SYNTHETIC (the approved
checklist is OPEN, AI-D02). Gaps carry a severity:

* BLOCKING - analysis cannot be presented as complete (missing source, incomplete transaction
  window). The workflow waits for evidence (case BLOCKED, run WAITING_EVIDENCE).
* REVIEW   - disclosed as a limitation (stale source, unreadable or expired document, missing
  required field, pending screening review). Never silently ignored.

Conflicts keep BOTH values with their source references (AER-007). A snapshot is created only when
the evidence set differs from the latest snapshot; snapshot rows are immutable (DB trigger).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from finagent.core.correlation import get_correlation_id
from finagent.database.models import (
    Alert,
    Case,
    EvidenceConflict,
    EvidenceItem,
    EvidenceSnapshot,
    EvidenceSnapshotItem,
    OutboxEvent,
)
from finagent.domain.fingerprint import fingerprint
from finagent.services.audit_service import writer as audit
from finagent.services.evidence_service.items import (
    current_items,
    evidence_content,
    hydrate_evidence_payloads,
)

BLOCKING, REVIEW = "BLOCKING", "REVIEW"


@dataclass(frozen=True)
class EvidenceRequirement:
    version: str
    sources: tuple[str, ...]
    documents: tuple[str, ...]


_DEFAULT = EvidenceRequirement(
    version="evidence-req-v1-SYNTHETIC",
    sources=("CUSTOMER_PROFILE", "TRANSACTIONS", "KYC_DOCUMENT_LIST", "SANCTIONS_SUMMARY"),
    documents=("PASSPORT", "PROOF_OF_ADDRESS"),
)
REQUIREMENTS: dict[str, EvidenceRequirement] = {"TM_STRUCTURING": _DEFAULT}
COMPARED_FIELDS = ("full_name", "date_of_birth")


def requirement_for(alert_type: str) -> EvidenceRequirement:
    return REQUIREMENTS.get(alert_type, _DEFAULT)


def _gap(code: str, severity: str, source_type: str, source_ref: str | None, detail: str) -> dict[str, Any]:
    return {
        "code": code,
        "severity": severity,
        "source_type": source_type,
        "source_ref": source_ref,
        "detail": detail,
    }


def compute_gaps(
    alert: Alert, items: list[EvidenceItem], now: datetime, max_age: timedelta
) -> list[dict[str, Any]]:
    req = requirement_for(alert.alert_type)
    gaps: list[dict[str, Any]] = []
    by_key = {(i.source_type, i.source_ref): i for i in items}
    for subject in alert.subject_refs:
        for source in req.sources:
            item = by_key.get((source, subject))
            if item is None:
                gaps.append(
                    _gap("SOURCE_MISSING", BLOCKING, source, subject, "required source not retrieved")
                )
                continue
            if item.completeness == "INCOMPLETE":
                reasons = (
                    ",".join(evidence_content(item).get("completeness", {}).get("reasons", []))
                    or "unspecified"
                )
                gaps.append(
                    _gap("SOURCE_INCOMPLETE", BLOCKING, source, subject, f"incomplete retrieval: {reasons}")
                )
            if item.freshness == "STALE" or (item.as_of is not None and now - item.as_of > max_age):
                gaps.append(_gap("SOURCE_STALE", REVIEW, source, subject, "source data older than threshold"))
        kyc = by_key.get(("KYC_DOCUMENT_LIST", subject))
        for doc in evidence_content(kyc).get("documents", []) if kyc else []:
            if doc.get("status") not in ("VERIFIED", "CURRENT"):
                detail = f"{doc.get('document_type')} status {doc.get('status')}"
                gaps.append(_gap("KYC_DOCUMENT_NOT_CURRENT", REVIEW, "KYC_DOCUMENT_LIST", subject, detail))
        sanctions = by_key.get(("SANCTIONS_SUMMARY", subject))
        status = (evidence_content(sanctions).get("summary", {}) if sanctions else {}).get("screening_status")
        if status in ("POTENTIAL_MATCH_PENDING_REVIEW", "NOT_SCREENED"):
            gaps.append(
                _gap(
                    "SCREENING_NOT_RESOLVED",
                    REVIEW,
                    "SANCTIONS_SUMMARY",
                    subject,
                    f"screening status {status}; resolution is outside platform authority",
                )
            )
    docs = [i for i in items if i.source_type == "DOCUMENT_EXTRACTION"]
    for doc_type in req.documents:
        typed = [d for d in docs if evidence_content(d).get("document_type") == doc_type]
        if not typed:
            gaps.append(
                _gap("DOCUMENT_MISSING", REVIEW, "DOCUMENT_EXTRACTION", None, f"no {doc_type} document")
            )
    for d in docs:
        if evidence_content(d).get("status") != "PARSED":
            gaps.append(
                _gap(
                    "DOCUMENT_NOT_READABLE",
                    REVIEW,
                    "DOCUMENT_EXTRACTION",
                    d.source_ref,
                    f"{evidence_content(d).get('document_type')} "
                    f"processing status {evidence_content(d).get('status')}",
                )
            )
        for f in evidence_content(d).get("fields", []):
            if f.get("value_state") in ("MISSING", "EXTRACTION_FAILED"):
                gaps.append(
                    _gap(
                        "REQUIRED_FIELD_MISSING",
                        REVIEW,
                        "DOCUMENT_EXTRACTION",
                        d.source_ref,
                        f"{evidence_content(d).get('document_type')}.{f.get('field_key')} "
                        f"{f.get('value_state')}",
                    )
                )
    return gaps


def _norm(value: Any) -> str:
    return " ".join(str(value or "").split()).casefold()


def detect_conflicts(
    session: Session, tenant_id: uuid.UUID, case_id: uuid.UUID, items: list[EvidenceItem]
) -> None:
    """Create OPEN conflicts for differing values; retire conflicts whose items were superseded.
    A resolution carries over when the same values reappear in re-versioned items."""
    current_ids = {i.id for i in items}
    for old in session.scalars(
        select(EvidenceConflict).where(
            EvidenceConflict.tenant_id == tenant_id,
            EvidenceConflict.case_id == case_id,
            EvidenceConflict.status != "SUPERSEDED",
        )
    ):
        if old.item_a_id not in current_ids or old.item_b_id not in current_ids:
            old.status = "SUPERSEDED"
    session.flush()
    profiles = {i.source_ref: i for i in items if i.source_type == "CUSTOMER_PROFILE"}
    for doc in (i for i in items if i.source_type == "DOCUMENT_EXTRACTION"):
        owner = evidence_content(doc).get("customer_ref")
        profile = (
            profiles.get(owner) if owner else (next(iter(profiles.values())) if len(profiles) == 1 else None)
        )
        if profile is None:
            continue
        values = evidence_content(profile).get("profile", {})
        for f in evidence_content(doc).get("fields", []):
            key = f.get("field_key")
            if key not in COMPARED_FIELDS or f.get("value_state") != "PRESENT" or values.get(key) is None:
                continue
            if _norm(values.get(key)) == _norm(f.get("value")):
                continue
            exists = session.scalar(
                select(EvidenceConflict.id).where(
                    EvidenceConflict.tenant_id == tenant_id,
                    EvidenceConflict.case_id == case_id,
                    EvidenceConflict.field_key == key,
                    EvidenceConflict.item_a_id == profile.id,
                    EvidenceConflict.item_b_id == doc.id,
                )
            )
            if exists is not None:
                continue
            value_a, value_b = str(values.get(key))[:300], str(f.get("value"))[:300]
            prior = session.scalar(
                select(EvidenceConflict)
                .where(
                    EvidenceConflict.tenant_id == tenant_id,
                    EvidenceConflict.case_id == case_id,
                    EvidenceConflict.field_key == key,
                    EvidenceConflict.value_a == value_a,
                    EvidenceConflict.value_b == value_b,
                    EvidenceConflict.resolution.is_not(None),
                )
                .order_by(EvidenceConflict.created_at.desc())
                .limit(1)
            )
            conflict = EvidenceConflict(
                tenant_id=tenant_id,
                case_id=case_id,
                field_key=key,
                item_a_id=profile.id,
                item_b_id=doc.id,
                value_a=value_a,
                value_b=value_b,
                status="OPEN",
            )
            if prior is not None:  # same values already resolved: keep the analyst's recorded reason
                conflict.status, conflict.resolution = "RESOLVED", prior.resolution
                conflict.rationale = f"carried over from conflict {prior.id}: {prior.rationale}"[:1000]
                conflict.resolved_by, conflict.resolved_at = prior.resolved_by, prior.resolved_at
            session.add(conflict)
    session.flush()


def latest_snapshot(session: Session, tenant_id: uuid.UUID, case_id: uuid.UUID) -> EvidenceSnapshot | None:
    return session.scalar(
        select(EvidenceSnapshot)
        .where(EvidenceSnapshot.tenant_id == tenant_id, EvidenceSnapshot.case_id == case_id)
        .order_by(EvidenceSnapshot.version_no.desc())
        .limit(1)
    )


def conflicts_for(session: Session, tenant_id: uuid.UUID, case_id: uuid.UUID) -> list[EvidenceConflict]:
    return list(
        session.scalars(
            select(EvidenceConflict)
            .where(
                EvidenceConflict.tenant_id == tenant_id,
                EvidenceConflict.case_id == case_id,
                EvidenceConflict.status != "SUPERSEDED",
            )
            .order_by(EvidenceConflict.created_at)
        )
    )


def create_snapshot(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    case_id: uuid.UUID,
    created_by: str,
    max_age: timedelta,
    run_id: uuid.UUID | None = None,
    actor_principal_id: uuid.UUID | None = None,
    actor_kind: str = "SYSTEM",
) -> tuple[EvidenceSnapshot, bool]:
    """Freeze the current evidence set. Returns (snapshot, created)."""
    row = session.execute(
        select(Case, Alert)
        .join(Alert, and_(Alert.tenant_id == Case.tenant_id, Alert.id == Case.alert_id))
        .where(Case.tenant_id == tenant_id, Case.id == case_id)
        .with_for_update(of=Case)
    ).one()
    _case, alert = row
    items = current_items(session, tenant_id, case_id)
    detect_conflicts(session, tenant_id, case_id, items)
    conflicts = conflicts_for(session, tenant_id, case_id)
    open_conflicts = sum(1 for c in conflicts if c.status == "OPEN")
    gaps = compute_gaps(alert, items, datetime.now(UTC), max_age)
    req = requirement_for(alert.alert_type)
    if open_conflicts:
        status = "CONFLICTED"
    elif any(g["severity"] == BLOCKING for g in gaps):
        status = "INCOMPLETE"
    else:
        status = "COMPLETE"
    digest = fingerprint(
        {
            "items": sorted([str(i.id), i.content_hash] for i in items),
            "conflicts": sorted([str(c.id), c.status, c.resolution or ""] for c in conflicts),
            "gaps": sorted(g["code"] + "|" + str(g["source_ref"]) + "|" + g["detail"] for g in gaps),
            "requirements": req.version,
        }
    )
    latest = latest_snapshot(session, tenant_id, case_id)
    if latest is not None and latest.content_hash == digest:
        return latest, False
    snapshot = EvidenceSnapshot(
        tenant_id=tenant_id,
        case_id=case_id,
        version_no=(latest.version_no + 1) if latest else 1,
        content_hash=digest,
        completeness_status=status,
        requirements_version=req.version,
        gaps=gaps,
        open_conflicts=open_conflicts,
        run_id=run_id,
        created_by=created_by,
    )
    session.add(snapshot)
    session.flush()
    for item in items:
        session.add(
            EvidenceSnapshotItem(tenant_id=tenant_id, snapshot_id=snapshot.id, evidence_item_id=item.id)
        )
    audit.record(
        session,
        tenant_id=tenant_id,
        action="EVIDENCE_SNAPSHOT_CREATED",
        object_type="evidence_snapshot",
        object_id=str(snapshot.id),
        outcome="SUCCESS",
        actor_principal_id=actor_principal_id,
        actor_kind=actor_kind,
        case_id=case_id,
        details={
            "version_no": snapshot.version_no,
            "items": len(items),
            "completeness": status,
            "gaps": len(gaps),
            "open_conflicts": open_conflicts,
            "content_hash": digest,
        },
    )
    session.add(
        OutboxEvent(
            tenant_id=tenant_id,
            event_type="evidence.snapshot_created.v1",
            schema_version="1",
            resource_id=str(snapshot.id),
            correlation_id=get_correlation_id(),
            payload={"case_id": str(case_id), "snapshot_id": str(snapshot.id), "completeness": status},
        )
    )
    session.flush()
    return snapshot, True


def snapshot_items(session: Session, tenant_id: uuid.UUID, snapshot_id: uuid.UUID) -> list[EvidenceItem]:
    items = list(
        session.scalars(
            select(EvidenceItem)
            .join(
                EvidenceSnapshotItem,
                and_(
                    EvidenceSnapshotItem.tenant_id == EvidenceItem.tenant_id,
                    EvidenceSnapshotItem.evidence_item_id == EvidenceItem.id,
                ),
            )
            .where(
                EvidenceSnapshotItem.tenant_id == tenant_id, EvidenceSnapshotItem.snapshot_id == snapshot_id
            )
            .order_by(EvidenceItem.source_type, EvidenceItem.source_ref)
        )
    )
    return hydrate_evidence_payloads(session, items)


def snapshot_count(session: Session, tenant_id: uuid.UUID, case_id: uuid.UUID) -> int:
    return int(
        session.scalar(
            select(func.count()).where(
                EvidenceSnapshot.tenant_id == tenant_id, EvidenceSnapshot.case_id == case_id
            )
        )
        or 0
    )
