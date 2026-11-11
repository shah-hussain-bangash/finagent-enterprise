"""Run a parser in a separate, resource-limited process (SPR-017; prompt §15 "resource-limited worker").

The parent enforces wall-clock time (the child is killed on timeout); the child enforces its own
memory cap before reading the payload (see `parse_worker`). The child gets an isolated
interpreter (`-I`: no PYTHON* variables, no user site, no current-directory imports) and a
minimal environment with no secrets. Windows hosts use a disposable network-disabled Linux
container instead of executing a document parser natively. Linux children require seccomp.
Any failure maps to an explicit status - never to PARSED.
"""

from __future__ import annotations

import json
import os
import subprocess  # nosec B404 - required to isolate parsing in a resource-limited child process
import sys
from dataclasses import dataclass, field
from typing import Any

from finagent.documents import parsers
from finagent.documents.parser_container import ParserContainerUnavailableError, run_container
from finagent.documents.parsers import ParseLimits

KNOWN_STATUSES = frozenset(
    {
        parsers.PARSED,
        parsers.OCR_UNAVAILABLE,
        parsers.ENCRYPTED,
        parsers.ACTIVE_CONTENT,
        parsers.LIMIT_EXCEEDED,
        parsers.CORRUPT,
        parsers.UNSUPPORTED_CONTENT,
        parsers.CRASHED,
    }
)


@dataclass(frozen=True)
class SandboxConfig:
    timeout_s: float = 60.0
    memory_bytes: int = 768 * 1024 * 1024
    limits: ParseLimits = field(default_factory=ParseLimits)


def _child_env() -> dict[str, str]:
    keep = ("SYSTEMROOT", "WINDIR", "TEMP", "TMP", "LANG", "LC_ALL")
    return {k: os.environ[k] for k in keep if k in os.environ}


def _failure(status: str, reason: str) -> dict[str, Any]:
    return {
        "status": status,
        "reason": reason,
        "parser": "sandbox",
        "page_count": None,
        "row_count": None,
        "segments": [],
        "columns": None,
        "warnings": [],
        "memory_limit_enforced": None,
        "network_limit_enforced": None,
        "process_limit_enforced": None,
        "isolation_method": "unavailable",
    }


def run_parser(media_type: str, data: bytes, config: SandboxConfig) -> dict[str, Any]:
    header = {
        "media_type": media_type,
        "size": len(data),
        "memory_bytes": config.memory_bytes,
        "limits": config.limits.as_dict(),
    }
    payload = json.dumps(header).encode() + b"\n" + data
    try:
        if sys.platform == "win32":
            proc = run_container(payload, config.timeout_s, config.memory_bytes)
        elif sys.platform == "linux":
            proc = subprocess.run(  # nosec B603 - fixed interpreter/module, bytes only on stdin
                [sys.executable, "-I", "-B", "-m", "finagent.documents.parse_worker"],
                input=payload,
                capture_output=True,
                timeout=config.timeout_s,
                env=_child_env(),
                check=False,
                close_fds=True,
            )
        else:
            return _failure(parsers.CRASHED, "PARSER_PLATFORM_UNSUPPORTED")
    except ParserContainerUnavailableError as exc:
        return _failure(parsers.CRASHED, str(exc))
    except subprocess.TimeoutExpired:
        return _failure(parsers.TIMEOUT, "PARSER_TIMEOUT")
    except OSError:
        return _failure(parsers.CRASHED, "PARSER_START_FAILED")
    if proc.returncode != 0:
        # MemoryError / killed by the job object or rlimit / interpreter crash.
        return _failure(parsers.CRASHED, "PARSER_PROCESS_FAILED")
    try:
        result: dict[str, Any] = json.loads(proc.stdout)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return _failure(parsers.CRASHED, "PARSER_OUTPUT_INVALID")
    if not isinstance(result, dict) or result.get("status") not in KNOWN_STATUSES:
        return _failure(parsers.CRASHED, "PARSER_OUTPUT_INVALID")
    if result.get("status") != parsers.CRASHED and (
        result.get("memory_limit_enforced") is not True
        or result.get("network_limit_enforced") is not True
        or result.get("process_limit_enforced") is not True
        or result.get("isolation_method") != "linux-seccomp-v1"
    ):
        return _failure(parsers.CRASHED, "PARSER_ISOLATION_NOT_CONFIRMED")
    return result
