"""Transactional-outbox relay (MST:ARCH-003, MST:DB-003; prompt §21, §25).

Picks unpublished rows with `FOR UPDATE SKIP LOCKED` (several relays never publish the same row
concurrently), signs each envelope, publishes it, then marks the row published in the same
transaction that locked it. A crash after publish but before commit re-publishes the row later:
delivery is at-least-once, and consumers dedupe on `event_id` (inbox). A crash before publish loses
nothing - the committed outbox row is the source of truth (NQ-019).
"""

from __future__ import annotations

import logging
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, sessionmaker

from finagent.database.models import OutboxEvent
from finagent.database.session import transaction
from finagent.worker.events import from_outbox
from finagent.worker.queue import QueuePort

log = logging.getLogger("finagent.outbox")
_RNG = secrets.SystemRandom()
FaultHook = Callable[[str, str], None]  # (point, event_type) -> may raise to simulate a crash


@dataclass
class RelayResult:
    published: int = 0
    failed: int = 0


def _backoff(attempts: int, base_s: int) -> timedelta:
    # Exponential backoff with full jitter, capped at 10 minutes (DIR-028 bounded retries).
    ceiling = min(600, base_s * (2 ** min(attempts, 10)))
    return timedelta(seconds=_RNG.uniform(base_s, max(base_s, ceiling)))


def relay_batch(
    factory: sessionmaker[Session],
    queue: QueuePort,
    signing_key: bytes,
    *,
    batch: int = 100,
    max_attempts: int = 10,
    backoff_s: int = 2,
    fault: FaultHook | None = None,
) -> RelayResult:
    result = RelayResult()
    now = datetime.now(UTC)
    with transaction(factory) as session:
        rows = session.scalars(
            select(OutboxEvent)
            .where(
                OutboxEvent.published_at.is_(None),
                OutboxEvent.attempts < max_attempts,
                or_(OutboxEvent.next_attempt_at.is_(None), OutboxEvent.next_attempt_at <= now),
            )
            .order_by(OutboxEvent.created_at, OutboxEvent.id)
            .limit(batch)
            .with_for_update(skip_locked=True)
        ).all()
        for row in rows:
            envelope = from_outbox(row).signed(signing_key)
            try:
                if fault:
                    fault("before_publish", row.event_type)
                queue.publish(envelope)
            except Exception as exc:  # broker unavailable: retry this row later, keep going
                row.attempts += 1
                row.last_error_code = type(exc).__name__[:64]
                row.next_attempt_at = now + _backoff(row.attempts, backoff_s)
                result.failed += 1
                log.warning(
                    "outbox_publish_failed", extra={"event_type": row.event_type, "attempts": row.attempts}
                )
                continue
            if fault:
                fault("after_publish", row.event_type)  # simulated crash: publish done, mark lost
            row.published_at, row.last_error_code = datetime.now(UTC), None
            row.attempts += 1
            result.published += 1
    return result
