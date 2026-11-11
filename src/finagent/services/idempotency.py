"""Generic command idempotency (FR-032, prompt §41).

`begin` inserts (tenant, operation, key, request_hash) or finds the existing record. Same key +
same request returns the stored response; same key + different request is a 409. Concurrent
duplicates serialise on the unique index. Business unique keys remain the last line of defence.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from finagent.core.errors import AppError, ErrorCode
from finagent.database.models import IdempotencyRecord


@dataclass
class Idempotent:
    record: IdempotencyRecord
    replay: bool  # True -> return record.status_code/record.response unchanged

    def finish(self, status_code: int, body: dict[str, Any], resource_id: str | None) -> None:
        self.record.status_code, self.record.response, self.record.resource_id = (
            status_code,
            body,
            resource_id,
        )


def begin(
    session: Session, tenant_id: uuid.UUID, operation: str, key: str, request_hash: str, retention_days: int
) -> Idempotent:
    inserted = session.execute(
        pg_insert(IdempotencyRecord)
        .values(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            operation=operation,
            key=key,
            request_hash=request_hash,
            expires_at=datetime.now(UTC) + timedelta(days=retention_days),
        )
        .on_conflict_do_nothing(constraint="uq_idempotency")
        .returning(IdempotencyRecord.id)
    ).scalar_one_or_none()
    record = session.scalar(
        select(IdempotencyRecord).where(
            IdempotencyRecord.tenant_id == tenant_id,
            IdempotencyRecord.operation == operation,
            IdempotencyRecord.key == key,
        )
    )
    if record is None:
        raise AppError(ErrorCode.INTERNAL_ERROR)
    if inserted is None:
        if record.request_hash != request_hash:
            raise AppError(
                ErrorCode.IDEMPOTENCY_CONFLICT, "Idempotency-Key was reused with a different request."
            )
        if record.response is not None and record.status_code is not None:
            return Idempotent(record, replay=True)
    return Idempotent(record, replay=False)
