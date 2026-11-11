"""Queue port with a PostgreSQL adapter (local default) and an Azure Service Bus adapter (prompt §2).

Semantics are deliberately the same for both: at-least-once delivery, a visibility lock while a
consumer works, explicit ack/release, and a delivery counter. Exactly-once *processing* is the
consumer's job (inbox dedup) - the queue never promises it.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, sessionmaker

from finagent.database.models import QueueMessage
from finagent.database.session import transaction
from finagent.worker.events import EventEnvelope

log = logging.getLogger("finagent.queue")
DEFAULT_TOPIC = "finagent-events"


@dataclass
class ReceivedMessage:
    envelope: EventEnvelope | None  # None when the body is not a valid envelope (poison)
    raw: dict[str, Any]
    delivery_count: int
    handle: Any  # adapter-specific receipt


class QueuePort(Protocol):
    def publish(self, envelope: EventEnvelope) -> None: ...

    def receive(self, max_messages: int, visibility_timeout_s: int) -> list[ReceivedMessage]: ...

    def ack(self, message: ReceivedMessage) -> None: ...

    def release(self, message: ReceivedMessage, delay_s: int) -> None: ...

    def dead_letter(self, message: ReceivedMessage, reason: str) -> None: ...


def _parse(raw: dict[str, Any]) -> EventEnvelope | None:
    try:
        return EventEnvelope.model_validate(raw)
    except ValidationError:
        return None


@dataclass(frozen=True)
class _PgHandle:
    id: int
    lock_token: uuid.UUID


class PostgresQueue:
    """Each operation runs in its OWN transaction, like a remote broker would: publishing is never
    atomic with the producer's business transaction (that is what the outbox is for)."""

    def __init__(self, factory: sessionmaker[Session], topic: str = DEFAULT_TOPIC) -> None:
        self._factory, self._topic = factory, topic

    def publish(self, envelope: EventEnvelope) -> None:
        with transaction(self._factory) as s:
            s.execute(
                pg_insert(QueueMessage)
                .values(
                    tenant_id=envelope.tenant_ref,
                    topic=self._topic,
                    event_id=envelope.event_id,
                    body=envelope.to_json(),
                )
                .on_conflict_do_nothing(constraint="uq_queue_message_event")  # broker-side dup detection
            )

    def receive(self, max_messages: int, visibility_timeout_s: int) -> list[ReceivedMessage]:
        lock = uuid.uuid4()
        with transaction(self._factory) as s:
            rows = s.execute(
                text(
                    """
                    UPDATE queue_messages
                       SET delivery_count = delivery_count + 1,
                           lock_token = :lock,
                           locked_until = clock_timestamp() + make_interval(secs => :vt)
                     WHERE id IN (
                           SELECT id FROM queue_messages
                            WHERE topic = :topic
                              AND visible_at <= clock_timestamp()
                              AND (locked_until IS NULL OR locked_until < clock_timestamp())
                            ORDER BY id
                            LIMIT :n
                            FOR UPDATE SKIP LOCKED)
                 RETURNING id, body, delivery_count
                    """
                ),
                {"lock": lock, "vt": visibility_timeout_s, "topic": self._topic, "n": max_messages},
            ).all()
        return [
            ReceivedMessage(_parse(body), body, count, _PgHandle(mid, lock))
            for mid, body, count in sorted(rows, key=lambda r: r[0])
        ]

    def ack(self, message: ReceivedMessage) -> None:
        h: _PgHandle = message.handle
        with transaction(self._factory) as s:
            s.execute(
                text("DELETE FROM queue_messages WHERE id = :id AND lock_token = :lock"),
                {"id": h.id, "lock": h.lock_token},
            )

    def release(self, message: ReceivedMessage, delay_s: int) -> None:
        h: _PgHandle = message.handle
        with transaction(self._factory) as s:
            s.execute(
                text(
                    "UPDATE queue_messages SET lock_token = NULL, locked_until = NULL, "
                    "visible_at = clock_timestamp() + make_interval(secs => :d) "
                    "WHERE id = :id AND lock_token = :lock"
                ),
                {"id": h.id, "lock": h.lock_token, "d": delay_s},
            )

    def dead_letter(self, message: ReceivedMessage, reason: str) -> None:
        # The consumer has already persisted the DeadLetter record; remove it from the live queue.
        self.ack(message)

    def depth(self) -> int:
        with transaction(self._factory) as s:
            return int(
                s.execute(
                    text("SELECT count(*) FROM queue_messages WHERE topic = :t"), {"t": self._topic}
                ).scalar()
                or 0
            )


class ServiceBusQueue:
    """Azure Service Bus adapter (managed identity via DefaultAzureCredential; no secrets).

    LIVE INTEGRATION NOT VERIFIED: exercised only against a fake client in tests.
    """

    def __init__(self, namespace: str, queue_name: str, client: Any | None = None) -> None:
        if client is None:
            from azure.identity import DefaultAzureCredential
            from azure.servicebus import ServiceBusClient

            client = ServiceBusClient(namespace, credential=DefaultAzureCredential())
        self._client, self._queue = client, queue_name
        self._receiver: Any | None = None

    def _rx(self) -> Any:
        if self._receiver is None:
            self._receiver = self._client.get_queue_receiver(queue_name=self._queue)
        return self._receiver

    def publish(self, envelope: EventEnvelope) -> None:
        from azure.servicebus import ServiceBusMessage

        message = ServiceBusMessage(
            json.dumps(envelope.to_json()),
            message_id=str(envelope.event_id),  # enables Service Bus duplicate detection
            correlation_id=envelope.correlation_id,
            content_type="application/json",
            application_properties={
                "event_type": envelope.event_type,
                "tenant_ref": str(envelope.tenant_ref),
            },
        )
        with self._client.get_queue_sender(queue_name=self._queue) as sender:
            sender.send_messages(message)

    def receive(self, max_messages: int, visibility_timeout_s: int) -> list[ReceivedMessage]:
        # Lock duration is configured on the Service Bus entity (Terraform), not per receive.
        out = []
        for msg in self._rx().receive_messages(max_message_count=max_messages, max_wait_time=1):
            try:
                raw = json.loads(str(msg))  # str() of a received message is its decoded body
            except ValueError:
                raw = {}
            if not isinstance(raw, dict):
                raw = {}
            out.append(ReceivedMessage(_parse(raw), raw, int(msg.delivery_count or 0) + 1, msg))
        return out

    def ack(self, message: ReceivedMessage) -> None:
        self._rx().complete_message(message.handle)

    def release(self, message: ReceivedMessage, delay_s: int) -> None:
        self._rx().abandon_message(message.handle)  # redelivered after the entity's lock rules

    def dead_letter(self, message: ReceivedMessage, reason: str) -> None:
        self._rx().dead_letter_message(message.handle, reason=reason[:100])


def build_queue(
    backend: str, factory: sessionmaker[Session], namespace: str | None, queue_name: str
) -> QueuePort:
    if backend == "servicebus":
        if not namespace:
            raise RuntimeError("servicebus backend without namespace")
        return ServiceBusQueue(namespace, queue_name)
    return PostgresQueue(factory, queue_name)
