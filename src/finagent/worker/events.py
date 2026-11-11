"""Event envelope v1 (prompt §21; DIR-022; MST:ARCH-003).

`event_id` is the delivery identity (== outbox row id) and is never the business key. Envelopes
carry references, not case content. Producers sign the canonical form with HMAC-SHA256; consumers
reject unsigned, forged or unknown-type events into the dead-letter store without retrying.
JSON Schema: packages/contracts/events/envelope.v1.schema.json (scripts/export_event_schemas.py).
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from finagent.core import crypto
from finagent.database.models import OutboxEvent
from finagent.domain.fingerprint import canonical_json

PRODUCER = "finagent-outbox"

# Every event type the platform emits. Consumers dead-letter anything else.
EVENT_TYPES: frozenset[str] = frozenset(
    {
        "case.created.v1",
        "case.assigned.v1",
        "case.status_changed.v1",
        "case.blocked.v1",
        "case.cancelled.v1",
        "case.awaiting_review.v1",
        "case.completed.v1",
        "case.escalated.v1",
        "workflow.started.v1",
        "workflow.resume_requested.v1",
        "workflow.awaiting_review.v1",
        "workflow.completed.v1",
        "workflow.failed.v1",
        "workflow.cancelled.v1",
        "approval.decided.v1",
        "approval.requested.v1",
        "approval.invalidated.v1",
        "document.received.v1",
        "document.processed.v1",
        "evidence.snapshot_created.v1",
        "evidence.material_change.v1",
        "policy.version_changed.v1",
        "report.version_created.v1",
        "report.submitted.v1",
        "report.withdrawn.v1",
        "export.created.v1",
        "external_operation.uncertain.v1",
        "security.break_glass_activated.v1",
    }
)


class EventEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: uuid.UUID
    event_type: str = Field(max_length=100)
    schema_version: str = Field(max_length=16)
    tenant_ref: uuid.UUID
    resource_id: str = Field(max_length=100)
    occurred_at: AwareDatetime
    correlation_id: str = Field(max_length=64)
    producer: str = PRODUCER
    payload: dict[str, Any]
    signature: str | None = None

    def canonical(self) -> bytes:
        return canonical_json(self.model_dump(mode="json", exclude={"signature"})).encode()

    def signed(self, key: bytes) -> EventEnvelope:
        return self.model_copy(update={"signature": crypto.sign(key, self.canonical())})

    def signature_valid(self, key: bytes) -> bool:
        return self.signature is not None and crypto.verify(key, self.canonical(), self.signature)

    def to_json(self) -> dict[str, Any]:
        data: dict[str, Any] = json.loads(self.model_dump_json())
        return data


def from_outbox(row: OutboxEvent) -> EventEnvelope:
    occurred: datetime = row.occurred_at
    return EventEnvelope(
        event_id=row.id,
        event_type=row.event_type,
        schema_version=row.schema_version,
        tenant_ref=row.tenant_id,
        resource_id=row.resource_id,
        occurred_at=occurred,
        correlation_id=row.correlation_id,
        payload=row.payload,
    )
