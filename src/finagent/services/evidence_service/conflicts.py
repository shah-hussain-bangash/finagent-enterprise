"""Analyst resolution of evidence conflicts (FR-015, AER-007).

A resolution records the choice and a reason; both source records stay untouched. Resolving a
conflict changes the reviewed fact base, so it is a material change (FR-006).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints
from sqlalchemy import select
from sqlalchemy.orm import Session

from finagent.core.errors import AppError, ErrorCode, not_found
from finagent.database.models import EvidenceConflict
from finagent.domain.states import CASE_TERMINAL, CaseStatus
from finagent.security.authorization.policy import Permission, PrincipalContext, require
from finagent.services.audit_service import writer as audit
from finagent.services.case_service.queries import load_scoped_case
from finagent.services.review_service.invalidation import material_change

Rationale = Annotated[str, StringConstraints(strip_whitespace=True, min_length=10, max_length=1000)]


class ConflictResolution(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    resolution: Literal["USE_A", "USE_B", "BOTH_RETAINED_EXPLAINED", "SOURCE_CORRECTION_REQUESTED"]
    rationale: Rationale


def conflict_view(c: EvidenceConflict) -> dict[str, Any]:
    return {
        "conflict_id": str(c.id),
        "field_key": c.field_key,
        "item_a_id": str(c.item_a_id),
        "item_b_id": str(c.item_b_id),
        "value_a": c.value_a,
        "value_b": c.value_b,
        "status": c.status,
        "resolution": c.resolution,
        "rationale": c.rationale,
        "resolved_by": str(c.resolved_by) if c.resolved_by else None,
        "resolved_at": c.resolved_at.isoformat() if c.resolved_at else None,
        "created_at": c.created_at.isoformat(),
    }


def resolve(
    session: Session, ctx: PrincipalContext, raw_case_id: str, raw_conflict_id: str, body: ConflictResolution
) -> dict[str, Any]:
    require(ctx, Permission.CASE_UPDATE)
    case = load_scoped_case(session, ctx, raw_case_id, for_update=True)
    if case is None:
        raise not_found()
    try:
        conflict_id = uuid.UUID(raw_conflict_id)
    except ValueError as exc:
        raise not_found() from exc
    conflict = session.scalar(
        select(EvidenceConflict)
        .where(
            EvidenceConflict.tenant_id == ctx.tenant_id,
            EvidenceConflict.case_id == case.id,
            EvidenceConflict.id == conflict_id,
        )
        .with_for_update()
    )
    if conflict is None:
        raise not_found()
    if CaseStatus(case.status) in CASE_TERMINAL:
        raise AppError(ErrorCode.CASE_INVALID_STATE, "Completed or cancelled cases cannot be changed.")
    if conflict.status != "OPEN":
        raise AppError(
            ErrorCode.CASE_INVALID_STATE,
            "This conflict is not open (already resolved or superseded by newer evidence).",
            {"current_status": conflict.status},
        )
    conflict.status, conflict.resolution, conflict.rationale = "RESOLVED", body.resolution, body.rationale
    conflict.resolved_by, conflict.resolved_at = ctx.principal_id, datetime.now(UTC)
    audit.record(
        session,
        tenant_id=ctx.tenant_id,
        action="EVIDENCE_CONFLICT_RESOLVED",
        object_type="evidence_conflict",
        object_id=str(conflict.id),
        outcome="SUCCESS",
        actor_principal_id=ctx.principal_id,
        actor_kind=ctx.kind.value,
        case_id=case.id,
        details={"field_key": conflict.field_key, "resolution": body.resolution},
    )
    material_change(
        session,
        tenant_id=ctx.tenant_id,
        case_id=case.id,
        kind="EVIDENCE_CONFLICT_RESOLVED",
        actor_principal_id=ctx.principal_id,
        actor_kind=ctx.kind.value,
        refs={"conflict_id": str(conflict.id)},
    )
    session.flush()
    return conflict_view(conflict)
