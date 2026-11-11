"""Notification port (prompt §13 "notifications"; FR-011 delivery visibility; SPR-037; DIR-029).

Notifications carry references only - never case content, customer data or findings - so a
mis-routed message cannot tip off a customer or leak investigation content. The channel (email,
Teams) is OPEN (prompt §63). Providers must dedupe on `idempotency_key` and answer `status(key)`,
which the external-operation ledger uses to decide whether an ambiguous send happened.

Adapters: in-memory (tests), file (local dev; durable across worker processes, used by the slice-2
demo). A real channel adapter is added when the channel decision is made.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class Notification:
    tenant_id: uuid.UUID
    idempotency_key: str  # provider-side dedup: retries never produce a second message
    recipient_role: str
    template: str  # e.g. "case.escalated"
    resource_ref: str  # opaque id the recipient opens inside FinAgent (authorization applies there)


class DeliveryStatus(StrEnum):
    DELIVERED = "DELIVERED"
    NOT_FOUND = "NOT_FOUND"
    UNKNOWN = "UNKNOWN"  # provider cannot say -> the ledger records OUTCOME_UNCERTAIN


class NotificationError(Exception):
    """Delivery failed. `code` is a safe classification, never a provider payload."""

    def __init__(self, code: str, transient: bool = True) -> None:
        super().__init__(code)
        self.code, self.transient = code, transient


class NotificationPort(Protocol):
    def send(self, notification: Notification) -> None: ...

    def status(self, tenant_id: uuid.UUID, idempotency_key: str) -> DeliveryStatus: ...


class LocalNotificationAdapter:
    """In-memory sink: dedupes on idempotency_key exactly like a well-behaved provider."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.delivered: dict[str, Notification] = {}

    def send(self, notification: Notification) -> None:
        with self._lock:
            self.delivered.setdefault(notification.idempotency_key, notification)

    def status(self, tenant_id: uuid.UUID, idempotency_key: str) -> DeliveryStatus:
        with self._lock:
            found = idempotency_key in self.delivered
        return DeliveryStatus.DELIVERED if found else DeliveryStatus.NOT_FOUND


class FileNotificationAdapter:
    """LOCAL ONLY. One file per idempotency key, created with O_EXCL, so concurrent worker
    processes can never record the same notification twice."""

    def __init__(self, directory: str | Path) -> None:
        self._dir = Path(directory)
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        return self._dir / f"{hashlib.sha256(key.encode()).hexdigest()}.json"

    def send(self, notification: Notification) -> None:
        body = json.dumps({**asdict(notification), "tenant_id": str(notification.tenant_id)}).encode()
        try:
            fd = os.open(
                self._path(notification.idempotency_key), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600
            )
        except FileExistsError:
            return  # already delivered: provider-side dedup
        try:
            os.write(fd, body)
        finally:
            os.close(fd)

    def status(self, tenant_id: uuid.UUID, idempotency_key: str) -> DeliveryStatus:
        return DeliveryStatus.DELIVERED if self._path(idempotency_key).exists() else DeliveryStatus.NOT_FOUND

    def count(self) -> int:
        return sum(1 for _ in self._dir.glob("*.json"))


def build_notifier(backend: str, directory: str) -> NotificationPort:
    if backend == "file":
        return FileNotificationAdapter(directory)
    return LocalNotificationAdapter()
