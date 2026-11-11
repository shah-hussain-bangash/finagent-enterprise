"""Bounded parsers for PDF, DOCX and CSV (FR-013/014; AER-017; SPR-017).

These functions run INSIDE the isolated child process (`documents.sandbox`). Each returns a plain
dict (JSON-serialisable) with a status, text segments carrying page/section/row provenance and
warnings. They never execute embedded content: PDF JavaScript/launch actions and DOCX macros are
detected and reported as ACTIVE_CONTENT, encrypted files as ENCRYPTED, and resource limits
(pages, rows, decompressed size, ratio, characters) as LIMIT_EXCEEDED. An image-only PDF is
reported as OCR_UNAVAILABLE when no OCR provider is configured - never as an empty success.
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
from dataclasses import asdict, dataclass
from typing import Any

PARSED = "PARSED"
OCR_UNAVAILABLE = "OCR_UNAVAILABLE"
ENCRYPTED = "ENCRYPTED"
ACTIVE_CONTENT = "ACTIVE_CONTENT"
LIMIT_EXCEEDED = "LIMIT_EXCEEDED"
CORRUPT = "CORRUPT"
UNSUPPORTED_CONTENT = "UNSUPPORTED_CONTENT"
TIMEOUT = "TIMEOUT"  # set by the sandbox runner, never by a parser
CRASHED = "CRASHED"  # set by the sandbox runner (memory limit, interpreter failure)


@dataclass(frozen=True)
class ParseLimits:
    """PROPOSED limits (SEC-D07 / DATA-D04 are OPEN); configured through Settings."""

    max_pages: int = 200
    max_rows: int = 50_000
    max_columns: int = 64
    max_chars: int = 2_000_000
    max_cell_chars: int = 4_000
    max_zip_entries: int = 2_000
    max_uncompressed_bytes: int = 100 * 1024 * 1024
    max_compression_ratio: int = 100
    max_stream_bytes: int = 50 * 1024 * 1024

    def as_dict(self) -> dict[str, int]:
        return asdict(self)


def _result(status: str, parser: str, **kw: Any) -> dict[str, Any]:
    out: dict[str, Any] = {
        "status": status,
        "parser": parser,
        "reason": kw.pop("reason", None),
        "page_count": kw.pop("page_count", None),
        "row_count": kw.pop("row_count", None),
        "segments": kw.pop("segments", []),
        "columns": kw.pop("columns", None),
        "warnings": kw.pop("warnings", []),
    }
    out.update(kw)
    return out


# ------------------------------------------------------------------------------------------- PDF
_PDF_ACTIVE = re.compile(rb"/(?:JavaScript|JS|Launch|EmbeddedFiles?|RichMedia|XFA)(?![A-Za-z0-9])")


def parse_pdf(data: bytes, limits: ParseLimits) -> dict[str, Any]:
    import pypdf
    from pypdf.errors import PdfReadError

    parser = f"pypdf-{pypdf.__version__}"
    if _PDF_ACTIVE.search(data):
        return _result(ACTIVE_CONTENT, parser, reason="PDF_ACTIVE_CONTENT")
    cap = limits.max_stream_bytes
    caps = pypdf.Configuration(
        zlib_maximum_output_length=cap,
        lzw_maximum_output_length=cap,
        run_length_maximum_output_length=cap,
        array_based_stream_maximum_output_length=cap,
        maximum_declared_stream_length=cap,
        brotli_maximum_output_length=cap,
        image_maximum_buffer_size=cap,
        page_tree_maximum_entries=limits.max_pages + 1,
    )
    try:
        with pypdf.apply_configuration(caps):
            reader = pypdf.PdfReader(io.BytesIO(data), strict=False)
            if reader.is_encrypted:
                return _result(ENCRYPTED, parser, reason="PDF_ENCRYPTED")
            root = reader.trailer.get("/Root")
            catalog = root.get_object() if root is not None else {}
            names = catalog.get("/Names")
            names = names.get_object() if names is not None else {}
            if "/JavaScript" in names or "/EmbeddedFiles" in names or "/AA" in catalog:
                return _result(ACTIVE_CONTENT, parser, reason="PDF_ACTIVE_CONTENT")
            pages = len(reader.pages)
            if pages > limits.max_pages:
                return _result(LIMIT_EXCEEDED, parser, reason="PAGE_LIMIT", page_count=pages)
            segments: list[dict[str, Any]] = []
            total = 0
            for number, page in enumerate(reader.pages, start=1):
                text = page.extract_text() or ""
                total += len(text)
                if total > limits.max_chars:
                    return _result(LIMIT_EXCEEDED, parser, reason="TEXT_LIMIT", page_count=pages)
                segments.append({"kind": "page", "page": number, "section": None, "row": None, "text": text})
    except pypdf.errors.LimitReachedError:
        return _result(LIMIT_EXCEEDED, parser, reason="DECOMPRESSION_LIMIT")
    except (
        pypdf.errors.PyPdfError,
        PdfReadError,
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
        IndexError,
    ):
        return _result(CORRUPT, parser, reason="PDF_UNREADABLE")
    except RecursionError:
        return _result(LIMIT_EXCEEDED, parser, reason="NESTING_LIMIT")
    if not any(s["text"].strip() for s in segments):
        # Scanned/image-only PDF: needs OCR. Without an OCR provider this is explicit (AER-017).
        return _result(OCR_UNAVAILABLE, parser, reason="NO_TEXT_LAYER", page_count=pages)
    return _result(PARSED, parser, page_count=pages, segments=segments)


# ------------------------------------------------------------------------------------------ DOCX
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"


def _zip_guard(zf: zipfile.ZipFile, limits: ParseLimits) -> str | None:
    infos = zf.infolist()
    if len(infos) > limits.max_zip_entries:
        return "ZIP_ENTRY_LIMIT"
    total = 0
    for info in infos:
        if info.flag_bits & 0x1:
            return "ZIP_ENCRYPTED_ENTRY"
        total += info.file_size
        if info.compress_size and info.file_size / info.compress_size > limits.max_compression_ratio:
            return "DECOMPRESSION_RATIO"
    if total > limits.max_uncompressed_bytes:
        return "DECOMPRESSED_SIZE"
    return None


def _read_member(zf: zipfile.ZipFile, name: str, cap: int) -> bytes:
    with zf.open(name) as fh:
        data = fh.read(cap + 1)  # bounded read: never trust the declared size
    if len(data) > cap:
        raise OverflowError(name)
    return data


def _text_of(el: Any) -> str:
    return "".join(t.text or "" for t in el.iter(f"{_W}t"))


def parse_docx(data: bytes, limits: ParseLimits) -> dict[str, Any]:
    from defusedxml import ElementTree
    from defusedxml.common import DefusedXmlException

    parser = "docx-defusedxml-1"
    warnings: list[str] = []
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            guard = _zip_guard(zf, limits)
            if guard == "ZIP_ENCRYPTED_ENTRY":
                return _result(ENCRYPTED, parser, reason=guard)
            if guard:
                return _result(LIMIT_EXCEEDED, parser, reason=guard)
            names = set(zf.namelist())
            if any(n.lower().endswith("vbaproject.bin") for n in names):
                return _result(ACTIVE_CONTENT, parser, reason="DOCX_MACROS")
            ctypes_xml = _read_member(zf, "[Content_Types].xml", 1_000_000)
            if b"macroEnabled" in ctypes_xml or b"activeX" in ctypes_xml.lower():
                return _result(ACTIVE_CONTENT, parser, reason="DOCX_ACTIVE_CONTENT")
            if "word/_rels/document.xml.rels" in names:
                rels = ElementTree.fromstring(_read_member(zf, "word/_rels/document.xml.rels", 1_000_000))
                if any(r.get("TargetMode") == "External" for r in rels.iter(f"{_REL}Relationship")):
                    warnings.append("EXTERNAL_REFERENCES_IGNORED")  # never fetched
            body_xml = _read_member(zf, "word/document.xml", limits.max_uncompressed_bytes)
    except (zipfile.BadZipFile, KeyError, OSError, EOFError):
        return _result(CORRUPT, parser, reason="DOCX_UNREADABLE")
    except OverflowError:
        return _result(LIMIT_EXCEEDED, parser, reason="DECOMPRESSED_SIZE")
    except (DefusedXmlException, ElementTree.ParseError):
        return _result(UNSUPPORTED_CONTENT, parser, reason="DOCX_XML_REJECTED")
    try:
        root = ElementTree.fromstring(body_xml)
    except (DefusedXmlException, ElementTree.ParseError):
        return _result(UNSUPPORTED_CONTENT, parser, reason="DOCX_XML_REJECTED")
    body = root.find(f"{_W}body")
    if body is None:
        return _result(CORRUPT, parser, reason="DOCX_NO_BODY")
    segments: list[dict[str, Any]] = []
    section, total, para_no, table_no = "body", 0, 0, 0
    for block in body:
        if block.tag == f"{_W}p":
            para_no += 1
            text = _text_of(block)
            style = block.find(f"{_W}pPr/{_W}pStyle")
            if style is not None and str(style.get(f"{_W}val", "")).lower().startswith("heading"):
                section = text.strip()[:120] or section
            if text.strip():
                segments.append(
                    {"kind": "paragraph", "page": None, "section": section, "row": para_no, "text": text}
                )
                total += len(text)
        elif block.tag == f"{_W}tbl":
            table_no += 1
            for row_no, tr in enumerate(block.iter(f"{_W}tr"), start=1):
                cells = [_text_of(tc)[: limits.max_cell_chars] for tc in tr.iter(f"{_W}tc")]
                text = " | ".join(cells)
                total += len(text)
                segments.append(
                    {
                        "kind": "table_row",
                        "page": None,
                        "section": f"{section} / table {table_no}",
                        "row": row_no,
                        "text": text,
                        "cells": cells,
                    }
                )
        if total > limits.max_chars:
            return _result(LIMIT_EXCEEDED, parser, reason="TEXT_LIMIT")
    if not segments:
        return _result(UNSUPPORTED_CONTENT, parser, reason="DOCX_NO_TEXT", warnings=warnings)
    return _result(PARSED, parser, segments=segments, warnings=warnings)


# ------------------------------------------------------------------------------------------- CSV
_FORMULA = ("=", "+", "-", "@", "\t", "\r")


def parse_csv(data: bytes, limits: ParseLimits) -> dict[str, Any]:
    parser = "csv-stdlib-1"
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return _result(UNSUPPORTED_CONTENT, parser, reason="CSV_NOT_UTF8")
    if len(text) > limits.max_chars * 4:
        return _result(LIMIT_EXCEEDED, parser, reason="TEXT_LIMIT")
    csv.field_size_limit(limits.max_cell_chars)
    reader = csv.reader(io.StringIO(text, newline=""))
    warnings: list[str] = []
    segments: list[dict[str, Any]] = []
    try:
        header = next(reader, None)
        if not header:
            return _result(UNSUPPORTED_CONTENT, parser, reason="CSV_EMPTY")
        columns = [h.strip()[:64] for h in header]
        if len(columns) > limits.max_columns:
            return _result(LIMIT_EXCEEDED, parser, reason="COLUMN_LIMIT")
        for row_no, row in enumerate(reader, start=2):  # row numbers are file lines (header = 1)
            if row_no - 1 > limits.max_rows:
                return _result(LIMIT_EXCEEDED, parser, reason="ROW_LIMIT")
            if not any(c.strip() for c in row):
                continue
            if len(row) != len(columns):
                warnings.append(f"ROW_{row_no}_COLUMN_COUNT")
            if any(c.startswith(_FORMULA) and not _is_number(c) for c in row):
                warnings.append(f"ROW_{row_no}_FORMULA_LIKE_VALUE")  # data only; never evaluated
            segments.append(
                {
                    "kind": "row",
                    "page": None,
                    "section": None,
                    "row": row_no,
                    "text": ",".join(row),
                    "cells": row[: limits.max_columns],
                }
            )
    except csv.Error:
        return _result(CORRUPT, parser, reason="CSV_MALFORMED")
    return _result(
        PARSED, parser, row_count=len(segments), segments=segments, columns=columns, warnings=warnings
    )


def _is_number(value: str) -> bool:
    try:
        float(value)
    except ValueError:
        return False
    return True


PARSERS = {"application/pdf": parse_pdf, "text/csv": parse_csv}
PARSERS["application/vnd.openxmlformats-officedocument.wordprocessingml.document"] = parse_docx


def parse(media_type: str, data: bytes, limits: ParseLimits) -> dict[str, Any]:
    fn = PARSERS.get(media_type)
    if fn is None:
        return _result(UNSUPPORTED_CONTENT, "none", reason="UNSUPPORTED_MEDIA_TYPE")
    return fn(data, limits)
