"""Material-change hook (FR-006, AER-016, DIR-018).

Every service that changes reviewed facts - a new evidence version with different content, a
corrected document field, a resolved evidence conflict, a report edit - calls `material_change` in
the SAME transaction. The change is audited and published; approvals bound to the case are then
invalidated by `review_service.approvals.invalidate_for_case` (wired in P10).
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from finagent.core.correlation import get_correlation_id
from finagent.database.models import OutboxEvent
from finagent.services.audit_service import writer as audit


def material_change(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    case_id: uuid.UUID,
    kind: str,
    actor_principal_id: uuid.UUID | None,
    actor_kind: str,
    refs: dict[str, Any] | None = None,
) -> int:
    """Record the change and invalidate affected approvals. Returns the number invalidated."""
    from finagent.services.review_service import approvals  # lazy: avoids an import cycle

    invalidated = approvals.invalidate_for_case(
        session,
        tenant_id=tenant_id,
        case_id=case_id,
        reason=kind,
        actor_principal_id=actor_principal_id,
        actor_kind=actor_kind,
    )
    audit.record(
        session,
        tenant_id=tenant_id,
        action="MATERIAL_CHANGE_RECORDED",
        object_type="case",
        object_id=str(case_id),
        outcome="SUCCESS",
        actor_principal_id=actor_principal_id,
        actor_kind=actor_kind,
        case_id=case_id,
        details={"kind": kind, "approvals_invalidated": invalidated, **(refs or {})},
    )
    session.add(
        OutboxEvent(
            tenant_id=tenant_id,
            event_type="evidence.material_change.v1",
            schema_version="1",
            resource_id=str(case_id),
            correlation_id=get_correlation_id(),
            payload={"case_id": str(case_id), "kind": kind, "approvals_invalidated": invalidated},
        )
    )
    return invalidated
