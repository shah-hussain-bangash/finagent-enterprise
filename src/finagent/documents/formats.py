"""Declared-type and file-signature validation (DIR-014; SPR-017).

Only PDF, DOCX and CSV are accepted (PROPOSED MVP set). The declared media type must be supported
AND agree with the bytes' signature. Inspection reads the ZIP central directory only (no
decompression), so a hostile archive cannot cost anything here.
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass
from enum import StrEnum

PDF = "application/pdf"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
CSV = "text/csv"
SUPPORTED_MEDIA_TYPES = frozenset({PDF, DOCX, CSV})
_ALIASES = {"application/csv": CSV, "text/comma-separated-values": CSV}

_OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"  # legacy Office / encrypted OOXML container
_ZIP = b"PK\x03\x04"


class Signature(StrEnum):
    PDF = "PDF"
    DOCX = "DOCX"
    DOCX_MACRO = "DOCX_MACRO"  # OOXML with a VBA project: active content
    OLE_CONTAINER = "OLE_CONTAINER"  # encrypted OOXML or legacy binary Office
    TEXT = "TEXT"
    OTHER_ZIP = "OTHER_ZIP"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class FormatCheck:
    declared: str
    signature: Signature
    ok: bool
    reason: str | None  # safe reason code when not ok


def normalize_media_type(raw: str | None) -> str:
    base = (raw or "").split(";", 1)[0].strip().lower()
    return _ALIASES.get(base, base)


def sniff(data: bytes) -> Signature:
    if data.startswith(b"%PDF-"):
        return Signature.PDF
    if data.startswith(_OLE):
        return Signature.OLE_CONTAINER
    if data.startswith(_ZIP):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                names = set(zf.namelist()[:5000])
        except (zipfile.BadZipFile, ValueError, OSError):
            return Signature.UNKNOWN
        if "[Content_Types].xml" in names and "word/document.xml" in names:
            if any(n.lower().endswith("vbaproject.bin") for n in names):
                return Signature.DOCX_MACRO
            return Signature.DOCX
        return Signature.OTHER_ZIP
    head = data[:65536]
    if b"\x00" in head:
        return Signature.UNKNOWN
    try:
        head.decode("utf-8")
    except UnicodeDecodeError as exc:
        # A multi-byte character may be cut at the 64 KiB boundary; anything earlier is binary.
        if exc.start < len(head) - 4:
            return Signature.UNKNOWN
    return Signature.TEXT


_EXPECTED = {PDF: {Signature.PDF}, DOCX: {Signature.DOCX}, CSV: {Signature.TEXT}}


def check(declared_raw: str | None, data: bytes) -> FormatCheck:
    """`ok=False` with a reason means the upload must not be parsed (quarantine or reject)."""
    declared = normalize_media_type(declared_raw)
    sig = sniff(data)
    if declared not in SUPPORTED_MEDIA_TYPES:
        return FormatCheck(declared, sig, False, "UNSUPPORTED_MEDIA_TYPE")
    if declared == DOCX and sig is Signature.DOCX_MACRO:
        return FormatCheck(declared, sig, False, "ACTIVE_CONTENT_MACROS")
    if declared == DOCX and sig is Signature.OLE_CONTAINER:
        return FormatCheck(declared, sig, False, "ENCRYPTED_OR_LEGACY_CONTAINER")
    if sig not in _EXPECTED[declared]:
        return FormatCheck(declared, sig, False, "SIGNATURE_MISMATCH")
    return FormatCheck(declared, sig, True, None)
