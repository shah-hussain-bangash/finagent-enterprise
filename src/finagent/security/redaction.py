"""Redaction for ordinary logs and telemetry (SPR-036, AER-027, NQ-035, MST:DB-002).

Defence in depth only: the primary control is never passing sensitive payloads to the logger.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

REDACTED = "[REDACTED]"

_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{10,}"),
    re.compile(r"eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}"),  # JWT
    re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),  # e-mail
    re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b"),  # IBAN-like
    re.compile(r"\b\d{9,19}\b"),  # account / card / national-id like digit runs
    re.compile(r"(?i)(password|secret|api[_-]?key|token|connection[_-]?string)\s*[:=]\s*\S+"),
)

SENSITIVE_KEYS = frozenset(
    {
        "password",
        "secret",
        "token",
        "authorization",
        "api_key",
        "apikey",
        "access_token",
        "refresh_token",
        "client_secret",
        "connection_string",
        "prompt",
        "raw_payload",
        "document_text",
    }
)


def redact_text(value: str) -> str:
    for pattern in _PATTERNS:
        value = pattern.sub(REDACTED, value)
    return value


def redact(value: Any) -> Any:
    """Recursively redact strings and values under sensitive keys."""
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, Mapping):
        return {k: (REDACTED if str(k).lower() in SENSITIVE_KEYS else redact(v)) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [redact(v) for v in value]
    return value
