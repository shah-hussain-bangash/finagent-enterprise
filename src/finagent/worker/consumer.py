"""Queue consumer with inbox dedup and a dead-letter store (prompt §21; NQ-020, NQ-023).

For each message: validate (envelope shape, producer signature, known type, active tenant) ->
in ONE transaction insert the inbox row and run the handler -> ack. A duplicate delivery hits the
unique inbox key and is acknowledged without running the handler again. Permanent problems go
straight to the dead-letter store; handler failures are released with backoff and dead-lettered
after `max_deliveries`. Nothing is retried forever.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, sessionmaker

from finagent.database.models import DeadLetter, InboxEvent, Tenant
from finagent.database.session import transaction
from finagent.services.audit_service import writer as audit
from finagent.worker.events import EVENT_TYPES, EventEnvelope
from finagent.worker.queue import QueuePort, ReceivedMessage

log = logging.getLogger("finagent.consumer")
Handler = Callable[[Session, EventEnvelope], None]


class PermanentHandlerError(Exception):
    """The event can never be processed (e.g. references a missing resource): dead-letter now."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass
class ConsumeResult:
    processed: int = 0
    duplicates: int = 0
    released: int = 0
    dead_lettered: int = 0
    dispatched: list[EventEnvelope] = field(default_factory=list)


class Consumer:
    def __init__(
        self,
        name: str,
        factory: sessionmaker[Session],
        queue: QueuePort,
        handlers: dict[str, Handler],
        signing_key: bytes,
        *,
        max_deliveries: int = 5,
        retry_delay_s: int = 5,
    ) -> None:
        # Known events without a handler are acknowledged and recorded in the inbox (no work).
        self.name, self._factory, self._queue = name, factory, queue
        self._handlers, self._key = handlers, signing_key
        self._max, self._delay = max_deliveries, retry_delay_s

    def _invalid_reason(self, msg: ReceivedMessage) -> str | None:
        env = msg.envelope
        if env is None:
            return "MALFORMED_ENVELOPE"
        if not env.signature_valid(self._key):
            return "SIGNATURE_INVALID"
        if env.event_type not in EVENT_TYPES:
            return "UNKNOWN_EVENT_TYPE"
        with transaction(self._factory) as s:
            status = s.scalar(select(Tenant.status).where(Tenant.id == env.tenant_ref))
        if status != "ACTIVE":
            return "UNKNOWN_OR_INACTIVE_TENANT"
        return None

    def _dead_letter(self, msg: ReceivedMessage, reason: str, tenant_known: bool) -> None:
        env = msg.envelope
        tenant_id = env.tenant_ref if (env is not None and tenant_known) else None
        safe_envelope: dict[str, Any] = (
            env.model_dump(mode="json", exclude={"signature"}) if env is not None else {"unparseable": True}
        )
        with transaction(self._factory) as s:
            dl = DeadLetter(
                tenant_id=tenant_id,
                consumer=self.name,
                event_id=env.event_id if env is not None else None,
                event_type=env.event_type[:100] if env is not None else "UNKNOWN",
                reason_code=reason[:64],
                delivery_count=msg.delivery_count,
                envelope=safe_envelope if tenant_known else {"withheld": "tenant not established"},
            )
            s.add(dl)
            s.flush()
            if tenant_id is not None:
                audit.record(
                    s,
                    tenant_id=tenant_id,
                    action="EVENT_DEAD_LETTERED",
                    object_type="dead_letter",
                    object_id=str(dl.id),
                    outcome="FAILED",
                    actor_principal_id=None,
                    actor_kind="SYSTEM",
                    details={"consumer": self.name, "reason": reason, "event_type": dl.event_type},
                )
        log.warning("event_dead_lettered", extra={"consumer": self.name, "reason": reason})
        self._queue.dead_letter(msg, reason)

    def poll(self, max_messages: int = 10, visibility_timeout_s: int = 60) -> ConsumeResult:
        result = ConsumeResult()
        for msg in self._queue.receive(max_messages, visibility_timeout_s):
            reason = self._invalid_reason(msg)
            if reason is not None:
                tenant_known = reason not in {
                    "MALFORMED_ENVELOPE",
                    "SIGNATURE_INVALID",
                    "UNKNOWN_OR_INACTIVE_TENANT",
                }
                self._dead_letter(msg, reason, tenant_known)
                result.dead_lettered += 1
                continue
            env = msg.envelope
            if env is None:  # narrowed above; keeps the type checker honest
                continue
            handler = self._handlers.get(env.event_type)
            try:
                with transaction(self._factory) as s:
                    inserted = s.execute(
                        pg_insert(InboxEvent)
                        .values(
                            id=uuid.uuid4(),
                            tenant_id=env.tenant_ref,
                            consumer=self.name,
                            event_id=env.event_id,
                            event_type=env.event_type,
                        )
                        .on_conflict_do_nothing(constraint="uq_inbox_event")
                        .returning(InboxEvent.id)
                    ).scalar_one_or_none()
                    if inserted is not None and handler is not None:
                        handler(s, env)  # same transaction as the inbox row: processed exactly once
            except PermanentHandlerError as exc:
                self._dead_letter(msg, exc.code, tenant_known=True)
                result.dead_lettered += 1
                continue
            except Exception as exc:
                if msg.delivery_count >= self._max:
                    self._dead_letter(msg, f"MAX_DELIVERIES:{type(exc).__name__}", tenant_known=True)
                    result.dead_lettered += 1
                else:
                    self._queue.release(msg, self._delay * msg.delivery_count)
                    result.released += 1
                continue
            self._queue.ack(msg)
            if inserted is None:
                result.duplicates += 1
            else:
                result.processed += 1
                if handler is not None:  # post-commit work (e.g. execute a run) for handled events
                    result.dispatched.append(env)
        return result
