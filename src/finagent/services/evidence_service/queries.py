"""Permission-scoped evidence reads (FR-002, FR-015, FR-016, SPR-011).

Every read goes through the case scope first, so concealed and absent cases give the same 404.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from finagent.core.errors import not_found
from finagent.database.models import Alert, Case, EvidenceItem, EvidenceSnapshot
from finagent.security.authorization.policy import Permission, PrincipalContext, require
from finagent.services.case_service.queries import load_scoped_case, scope_filter
from finagent.services.evidence_service import snapshots as snaps
from finagent.services.evidence_service.conflicts import conflict_view
from finagent.services.evidence_service.items import current_items


def item_view(i: EvidenceItem, *, include_content: bool = True) -> dict[str, Any]:
    out: dict[str, Any] = {
        "evidence_item_id": str(i.id),
        "source_type": i.source_type,
        "source_ref": i.source_ref,
        "version_no": i.version_no,
        "status": i.status,
        "content_hash": i.content_hash,
        "source_system": i.source_system,
        "retrieved_at": i.retrieved_at.isoformat(),
        "as_of": i.as_of.isoformat() if i.as_of else None,
        "freshness": i.freshness,
        "completeness": i.completeness,
        "classification": i.classification,
        "change_reason": i.change_reason,
        "supersedes_id": str(i.supersedes_id) if i.supersedes_id else None,
    }
    if include_content:
        out["content"] = i.content
    return out


def snapshot_view(s: EvidenceSnapshot) -> dict[str, Any]:
    return {
        "snapshot_id": str(s.id),
        "case_id": str(s.case_id),
        "version_no": s.version_no,
        "content_hash": s.content_hash,
        "completeness_status": s.completeness_status,
        "requirements_version": s.requirements_version,
        "gaps": s.gaps,
        "open_conflicts": s.open_conflicts,
        "run_id": str(s.run_id) if s.run_id else None,
        "created_by": s.created_by,
        "created_at": s.created_at.isoformat(),
    }


def case_evidence(
    session: Session, ctx: PrincipalContext, raw_case_id: str, max_age: timedelta
) -> dict[str, Any]:
    require(ctx, Permission.CASE_READ)
    case = load_scoped_case(session, ctx, raw_case_id)
    if case is None:
        raise not_found()
    alert = session.scalar(select(Alert).where(Alert.tenant_id == ctx.tenant_id, Alert.id == case.alert_id))
    items = current_items(session, ctx.tenant_id, case.id)
    gaps = snaps.compute_gaps(alert, items, datetime.now(UTC), max_age) if alert else []
    latest = snaps.latest_snapshot(session, ctx.tenant_id, case.id)
    return {
        "case_id": str(case.id),
        "requirements_version": snaps.requirement_for(alert.alert_type).version if alert else None,
        "items": [item_view(i) for i in items],
        "gaps": gaps,
        "conflicts": [conflict_view(c) for c in snaps.conflicts_for(session, ctx.tenant_id, case.id)],
        "latest_snapshot": snapshot_view(latest) if latest else None,
    }


def list_snapshots(session: Session, ctx: PrincipalContext, raw_case_id: str) -> dict[str, Any]:
    require(ctx, Permission.CASE_READ)
    case = load_scoped_case(session, ctx, raw_case_id)
    if case is None:
        raise not_found()
    rows = session.scalars(
        select(EvidenceSnapshot)
        .where(EvidenceSnapshot.tenant_id == ctx.tenant_id, EvidenceSnapshot.case_id == case.id)
        .order_by(EvidenceSnapshot.version_no.desc())
    ).all()
    return {"items": [snapshot_view(s) for s in rows]}


def get_snapshot(
    session: Session, ctx: PrincipalContext, raw_case_id: str, raw_snapshot_id: str
) -> dict[str, Any]:
    require(ctx, Permission.CASE_READ)
    case = load_scoped_case(session, ctx, raw_case_id)
    if case is None:
        raise not_found()
    try:
        snapshot_id = uuid.UUID(raw_snapshot_id)
    except ValueError as exc:
        raise not_found() from exc
    snap = session.scalar(
        select(EvidenceSnapshot).where(
            EvidenceSnapshot.tenant_id == ctx.tenant_id,
            EvidenceSnapshot.case_id == case.id,
            EvidenceSnapshot.id == snapshot_id,
        )
    )
    if snap is None:
        raise not_found()
    out = snapshot_view(snap)
    out["items"] = [item_view(i) for i in snaps.snapshot_items(session, ctx.tenant_id, snap.id)]
    return out


def evidence_item(session: Session, ctx: PrincipalContext, raw_item_id: str) -> EvidenceItem | None:
    """Scoped lookup used by citation/claim resolution (SPR-011: re-checked at open time)."""
    try:
        item_id = uuid.UUID(raw_item_id)
    except ValueError:
        return None
    return session.scalar(
        select(EvidenceItem)
        .join(Case, and_(Case.tenant_id == EvidenceItem.tenant_id, Case.id == EvidenceItem.case_id))
        .where(EvidenceItem.tenant_id == ctx.tenant_id, EvidenceItem.id == item_id, scope_filter(ctx))
    )
