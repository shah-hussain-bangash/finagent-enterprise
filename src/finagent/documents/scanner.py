"""Malware-scanning port (FR-013; DIR-014; prompt §15).

* `LocalTestSignatureScanner` - LOCAL/TEST ONLY. Detects the industry EICAR test string and a small
  list of synthetic test markers. It is NOT antivirus and Settings refuses it outside local/test.
* `ClamdScanner` - ClamAV daemon over the documented INSTREAM protocol (TCP). LIVE NOT VERIFIED
  on this host (no clamd installed); unit-tested against a fake clamd socket server.
A scanner error is never "clean": the caller quarantines with SCAN_ERROR (fail closed).
"""

from __future__ import annotations

import socket
import struct
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class ScanVerdict(StrEnum):
    CLEAN = "CLEAN"
    INFECTED = "INFECTED"
    ERROR = "ERROR"


@dataclass(frozen=True)
class ScanResult:
    verdict: ScanVerdict
    engine: str
    signature: str | None = None  # safe signature name, never file content


class MalwareScanner(Protocol):
    engine: str

    def scan(self, data: bytes) -> ScanResult: ...


EICAR = rb"X5O!P%@AP[4\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
SYNTHETIC_TEST_MARKER = b"FINAGENT-SYNTHETIC-MALWARE-TEST-MARKER"


class LocalTestSignatureScanner:
    """Clearly identified local test adapter - not a malware engine."""

    engine = "local-test-signatures-NOT-AV"

    def scan(self, data: bytes) -> ScanResult:
        if EICAR in data:
            return ScanResult(ScanVerdict.INFECTED, self.engine, "EICAR-Test-File")
        if SYNTHETIC_TEST_MARKER in data:
            return ScanResult(ScanVerdict.INFECTED, self.engine, "FinAgent-Synthetic-Test")
        return ScanResult(ScanVerdict.CLEAN, self.engine)


class ClamdScanner:
    """ClamAV `zINSTREAM`: 4-byte big-endian chunk length + chunk, terminated by a zero length."""

    engine = "clamav-clamd"
    _CHUNK = 64 * 1024

    def __init__(self, host: str, port: int, timeout_s: float = 30.0) -> None:
        self._addr, self._timeout = (host, port), timeout_s

    def scan(self, data: bytes) -> ScanResult:
        try:
            with socket.create_connection(self._addr, timeout=self._timeout) as sock:
                sock.sendall(b"zINSTREAM\0")
                for i in range(0, len(data), self._CHUNK):
                    chunk = data[i : i + self._CHUNK]
                    sock.sendall(struct.pack(">I", len(chunk)) + chunk)
                sock.sendall(struct.pack(">I", 0))
                reply = b""
                while not reply.endswith(b"\0") and len(reply) < 4096:
                    part = sock.recv(1024)
                    if not part:
                        break
                    reply += part
        except OSError:
            return ScanResult(ScanVerdict.ERROR, self.engine)
        text = reply.rstrip(b"\0").decode("utf-8", "replace")
        if text.endswith("OK"):
            return ScanResult(ScanVerdict.CLEAN, self.engine)
        if text.endswith("FOUND"):
            name = text.split(":", 1)[-1].removesuffix("FOUND").strip()[:100]
            return ScanResult(ScanVerdict.INFECTED, self.engine, name or "unnamed")
        return ScanResult(ScanVerdict.ERROR, self.engine)  # size limit exceeded, daemon error, ...


def build_scanner(backend: str, host: str | None, port: int | None) -> MalwareScanner:
    if backend == "local-test":
        return LocalTestSignatureScanner()
    if backend == "clamd" and host and port:
        return ClamdScanner(host, port)
    raise ValueError("unknown or incomplete malware scanner configuration")
