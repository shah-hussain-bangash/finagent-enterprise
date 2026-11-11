"""Connector resilience primitives (prompt §13; DIR-021, DIR-028, AER-022, NQ-023).

* `ConnectorError` carries a classified `kind`; only TRANSIENT kinds are ever retried. Authorization,
  validation, schema, revocation and not-found failures are permanent.
* `RetryPolicy` - bounded attempts, exponential backoff with full jitter, honours Retry-After.
* `CircuitBreaker` - closed -> open after N consecutive transient failures -> half-open after a
  cool-down (one trial call) -> closed on success. An open circuit fails fast without calling.
"""

from __future__ import annotations

import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TypeVar

T = TypeVar("T")
_RNG = secrets.SystemRandom()


class ErrorKind(StrEnum):
    TIMEOUT = "TIMEOUT"
    UNAVAILABLE = "UNAVAILABLE"
    RATE_LIMITED = "RATE_LIMITED"
    CIRCUIT_OPEN = "CIRCUIT_OPEN"
    UNAUTHORIZED = "UNAUTHORIZED"
    FORBIDDEN = "FORBIDDEN"
    REVOKED = "REVOKED"
    NOT_FOUND = "NOT_FOUND"
    INVALID_REQUEST = "INVALID_REQUEST"
    CONFLICT = "CONFLICT"
    SCHEMA_UNSUPPORTED = "SCHEMA_UNSUPPORTED"
    INVALID_RESPONSE = "INVALID_RESPONSE"


TRANSIENT = frozenset({ErrorKind.TIMEOUT, ErrorKind.UNAVAILABLE, ErrorKind.RATE_LIMITED})


class ConnectorError(Exception):
    """Safe, classified connector failure. Never carries provider payloads or credentials."""

    def __init__(self, kind: ErrorKind, source: str, retry_after_s: float | None = None) -> None:
        super().__init__(f"{source}: {kind.value}")
        self.kind, self.source, self.retry_after_s = kind, source, retry_after_s

    @property
    def transient(self) -> bool:
        return self.kind in TRANSIENT


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    base_delay_s: float = 0.2
    max_delay_s: float = 5.0

    def delay(self, attempt: int, retry_after_s: float | None) -> float:
        if retry_after_s is not None:
            return min(self.max_delay_s, retry_after_s)
        ceiling = min(self.max_delay_s, self.base_delay_s * (2 ** (attempt - 1)))
        return _RNG.uniform(0, ceiling)


@dataclass
class CircuitBreaker:
    failure_threshold: int = 5
    reset_after_s: float = 30.0
    clock: Callable[[], float] = time.monotonic
    _failures: int = 0
    _opened_at: float | None = None
    _trial_in_flight: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def state(self) -> str:
        with self._lock:
            return self._state()

    def _state(self) -> str:
        if self._opened_at is None:
            return "CLOSED"
        if self.clock() - self._opened_at >= self.reset_after_s:
            return "HALF_OPEN"
        return "OPEN"

    def before_call(self, source: str) -> None:
        with self._lock:
            state = self._state()
            if state == "OPEN" or (state == "HALF_OPEN" and self._trial_in_flight):
                raise ConnectorError(ErrorKind.CIRCUIT_OPEN, source)
            if state == "HALF_OPEN":
                self._trial_in_flight = True

    def on_success(self) -> None:
        with self._lock:
            self._failures, self._opened_at, self._trial_in_flight = 0, None, False

    def on_failure(self, error: ConnectorError) -> None:
        with self._lock:
            self._trial_in_flight = False
            if not error.transient:
                return  # a 403 or schema error says nothing about the source's health
            self._failures += 1
            if self._failures >= self.failure_threshold or self._opened_at is not None:
                self._opened_at = self.clock()


def call_with_resilience[T](
    source: str,
    fn: Callable[[], T],
    policy: RetryPolicy,
    breaker: CircuitBreaker,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    attempt = 0
    while True:
        attempt += 1
        breaker.before_call(source)
        try:
            result = fn()
        except ConnectorError as exc:
            breaker.on_failure(exc)
            if not exc.transient or attempt >= policy.max_attempts:
                raise
            sleep(policy.delay(attempt, exc.retry_after_s))
            continue
        breaker.on_success()
        return result
