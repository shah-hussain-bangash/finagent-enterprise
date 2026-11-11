"""Correlation-Id propagation (prompt §25). Client-supplied values are used for tracing only."""

from __future__ import annotations

import re
import uuid
from contextvars import ContextVar

_VALID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")
_current: ContextVar[str] = ContextVar("correlation_id", default="")


def accept_or_generate(raw: str | None) -> str:
    return raw if raw and _VALID.fullmatch(raw) else str(uuid.uuid4())


def set_correlation_id(value: str) -> None:
    _current.set(value)


def get_correlation_id() -> str:
    return _current.get() or str(uuid.uuid4())
