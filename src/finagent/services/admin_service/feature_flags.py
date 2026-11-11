"""Tenant-scoped, audited feature flags (prompt §44). Unknown or absent flags are OFF.

The case-status write (DATA-D06) needs BOTH this tenant flag and the deployment-level setting.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, StringConstraints
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from finagent.core.errors import AppError, ErrorCode
from finagent.database.models import FeatureFlag
from finagent.security.authorization.policy import Permission, PrincipalContext, require
from finagent.services.audit_service import writer as audit

CASE_STATUS_WRITE = "case_status_write"
KNOWN_FLAGS = frozenset({CASE_STATUS_WRITE})


class FlagUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=10, max_length=300)]


def is_enabled(session: Session, tenant_id: uuid.UUID, flag_key: str) -> bool:
    if flag_key not in KNOWN_FLAGS:
        return False
    return bool(
        session.scalar(
            select(FeatureFlag.enabled).where(
                FeatureFlag.tenant_id == tenant_id, FeatureFlag.flag_key == flag_key
            )
        )
    )


def set_flag(session: Session, ctx: PrincipalContext, flag_key: str, body: FlagUpdate) -> dict[str, Any]:
    require(ctx, Permission.TENANT_ADMIN)  # MFA step-up
    if flag_key not in KNOWN_FLAGS:
        raise AppError(ErrorCode.NOT_FOUND)
    now = datetime.now(UTC)
    stmt = pg_insert(FeatureFlag).values(
        id=uuid.uuid4(),
        tenant_id=ctx.tenant_id,
        flag_key=flag_key,
        enabled=body.enabled,
        reason=body.reason,
        updated_by=ctx.principal_id,
        updated_at=now,
    )
    session.execute(
        stmt.on_conflict_do_update(
            constraint="uq_feature_flag",
            set_={
                "enabled": body.enabled,
                "reason": body.reason,
                "updated_by": ctx.principal_id,
                "updated_at": now,
            },
        )
    )
    audit.record(
        session,
        tenant_id=ctx.tenant_id,
        action="FEATURE_FLAG_CHANGED",
        object_type="feature_flag",
        object_id=flag_key,
        outcome="SUCCESS",
        actor_principal_id=ctx.principal_id,
        actor_kind=ctx.kind.value,
        details={"enabled": body.enabled, "reason": body.reason},
    )
    return {"flag_key": flag_key, "enabled": body.enabled, "updated_at": now.isoformat()}
