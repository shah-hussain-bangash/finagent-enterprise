"""Document intelligence units: signatures, scanning, bounded parsing, sandbox, typed extraction,
storage isolation (FR-013, FR-014, DIR-009, DIR-014, AER-017, SPR-017)."""

from __future__ import annotations

import socket
import subprocess
import sys
import threading
import uuid
from pathlib import Path

import pytest

from finagent.core.crypto import DecryptionError, Encryptor
from finagent.documents import formats, parsers
from finagent.documents.extraction import extract, parse_money
from finagent.documents.parsers import ParseLimits, parse
from finagent.documents.sandbox import SandboxConfig, run_parser
from finagent.documents.scanner import ClamdScanner, LocalTestSignatureScanner, ScanVerdict
from finagent.documents.storage import LocalBlobStore, new_key
from tests import doc_fixtures as fx

LIMITS = ParseLimits()


@pytest.mark.req("FR-013", "DIR-014", "SPR-017")
def test_declared_type_must_match_signature() -> None:
    assert formats.check("application/pdf", fx.passport_pdf()).ok
    assert formats.check("text/csv; charset=utf-8", fx.statement_csv()).ok
    assert formats.check(formats.DOCX, fx.docx()).ok
    assert formats.check("application/pdf", fx.statement_csv()).reason == "SIGNATURE_MISMATCH"
    assert formats.check("text/csv", fx.passport_pdf()).reason == "SIGNATURE_MISMATCH"
    assert formats.check(formats.DOCX, fx.macro_docx()).reason == "ACTIVE_CONTENT_MACROS"
    ole = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 512
    assert formats.check(formats.DOCX, ole).reason == "ENCRYPTED_OR_LEGACY_CONTAINER"
    assert formats.check("image/png", b"\x89PNG\r\n").reason == "UNSUPPORTED_MEDIA_TYPE"
    assert formats.check("application/pdf", b"MZ\x90\x00 executable").reason == "SIGNATURE_MISMATCH"


@pytest.mark.req("FR-013", "DIR-014")
def test_local_test_scanner_is_labelled_and_detects_test_signatures() -> None:
    scanner = LocalTestSignatureScanner()
    assert "NOT-AV" in scanner.engine  # never mistaken for antivirus
    assert scanner.scan(fx.eicar_csv()).verdict is ScanVerdict.INFECTED
    assert scanner.scan(fx.statement_csv()).verdict is ScanVerdict.CLEAN


def _fake_clamd(reply: bytes) -> tuple[int, threading.Thread]:
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)

    def serve() -> None:
        conn, _ = srv.accept()
        with conn:
            data = b""
            while not data.endswith(b"\x00\x00\x00\x00"):
                chunk = conn.recv(65536)
                if not chunk:
                    break
                data += chunk
            assert data.startswith(b"zINSTREAM\0")
            conn.sendall(reply)
        srv.close()

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    return srv.getsockname()[1], t


@pytest.mark.req("FR-013")
def test_clamd_adapter_speaks_instream_and_fails_closed() -> None:
    port, t = _fake_clamd(b"stream: OK\0")
    assert ClamdScanner("127.0.0.1", port).scan(b"hello").verdict is ScanVerdict.CLEAN
    t.join(5)
    port, t = _fake_clamd(b"stream: Eicar-Test-Signature FOUND\0")
    result = ClamdScanner("127.0.0.1", port).scan(fx.eicar_csv())
    assert result.verdict is ScanVerdict.INFECTED and result.signature == "Eicar-Test-Signature"
    t.join(5)
    with socket.socket() as s:  # nothing listening -> ERROR, never CLEAN
        s.bind(("127.0.0.1", 0))
        free = s.getsockname()[1]
    assert ClamdScanner("127.0.0.1", free, timeout_s=1).scan(b"x").verdict is ScanVerdict.ERROR


@pytest.mark.req("FR-014", "AER-017")
def test_pdf_text_is_extracted_with_page_provenance() -> None:
    out = parse("application/pdf", fx.passport_pdf(), LIMITS)
    assert out["status"] == parsers.PARSED and out["page_count"] == 1
    assert out["segments"][0]["page"] == 1 and "Full name: SYNTHETIC Person 001" in out["segments"][0]["text"]


@pytest.mark.req("FR-013", "AER-017", "SPR-017")
@pytest.mark.release_blocker
def test_hostile_or_unreadable_pdfs_get_explicit_states() -> None:
    assert parse("application/pdf", fx.encrypted_pdf(), LIMITS)["status"] == parsers.ENCRYPTED
    assert parse("application/pdf", fx.javascript_pdf(), LIMITS)["status"] == parsers.ACTIVE_CONTENT
    assert parse("application/pdf", fx.blank_pdf(), LIMITS)["status"] == parsers.OCR_UNAVAILABLE
    assert parse("application/pdf", b"%PDF-1.4 garbage", LIMITS)["status"] == parsers.CORRUPT
    tight = ParseLimits(max_pages=2)
    over = parse("application/pdf", fx.blank_pdf(pages=3), tight)
    assert over["status"] == parsers.LIMIT_EXCEEDED and over["reason"] == "PAGE_LIMIT"


@pytest.mark.req("FR-014", "AER-017")
def test_docx_paragraphs_and_tables_keep_section_and_row() -> None:
    out = parse(formats.DOCX, fx.docx(table=[["Item", "Value"], ["Turnover", "AED 1,000.00"]]), LIMITS)
    assert out["status"] == parsers.PARSED
    para = next(s for s in out["segments"] if "Full name" in s["text"])
    assert para["section"] == "Customer details" and para["kind"] == "paragraph"
    rows = [s for s in out["segments"] if s["kind"] == "table_row"]
    assert rows[1]["row"] == 2 and rows[1]["cells"] == ["Turnover", "AED 1,000.00"]


@pytest.mark.req("SPR-017", "FR-013")
@pytest.mark.release_blocker
def test_docx_bombs_macros_and_entities_are_refused() -> None:
    bomb = parse(formats.DOCX, fx.bomb_docx(), LIMITS)
    assert bomb["status"] == parsers.LIMIT_EXCEEDED and bomb["reason"] == "DECOMPRESSION_RATIO"
    assert parse(formats.DOCX, fx.macro_docx(), LIMITS)["status"] == parsers.ACTIVE_CONTENT
    xxe = parse(formats.DOCX, fx.xxe_docx(), LIMITS)
    assert xxe["status"] == parsers.UNSUPPORTED_CONTENT  # DTD/entity rejected, never resolved
    assert parse(formats.DOCX, b"PK\x03\x04 not really a zip", LIMITS)["status"] == parsers.CORRUPT


@pytest.mark.req("FR-014", "SPR-017")
def test_csv_rows_limits_and_formula_values() -> None:
    out = parse("text/csv", b"a,b\n1,2\n=HYPERLINK(1),3\n-5,4\n", LIMITS)
    assert out["status"] == parsers.PARSED and out["row_count"] == 3
    assert "ROW_3_FORMULA_LIKE_VALUE" in out["warnings"] and "ROW_4_FORMULA_LIKE_VALUE" not in out["warnings"]
    many = b"a\n" + b"1\n" * 20
    assert parse("text/csv", many, ParseLimits(max_rows=10))["status"] == parsers.LIMIT_EXCEEDED
    assert parse("text/csv", b"\xff\xfe\x00bad", LIMITS)["status"] == parsers.UNSUPPORTED_CONTENT


@pytest.mark.req("SPR-017", "FR-013")
@pytest.mark.release_blocker
def test_sandbox_isolates_parsing_with_time_and_memory_limits() -> None:
    ok = run_parser("application/pdf", fx.passport_pdf(), SandboxConfig())
    assert ok["status"] == parsers.PARSED and ok["memory_limit_enforced"] is True
    assert (
        run_parser("application/pdf", fx.passport_pdf(), SandboxConfig(timeout_s=0.001))["status"]
        == parsers.TIMEOUT
    )
    code = (
        "from finagent.documents.parse_worker import apply_memory_limit as a;"
        "assert a(150*1024*1024); x = bytearray(600*1024*1024); print('NOT LIMITED')"
    )
    proc = subprocess.run(  # noqa: S603 - fixed argv, test-authored code
        [sys.executable, "-I", "-c", code], capture_output=True, text=True, check=False
    )
    assert proc.returncode != 0 and "NOT LIMITED" not in proc.stdout


@pytest.mark.req("FR-014", "DIR-009", "AER-017")
def test_typed_extraction_keeps_states_distinct() -> None:
    parsed = parse("application/pdf", fx.passport_pdf(), LIMITS)
    fields = {f.field_key: f for f in extract("PASSPORT", parsed)}
    assert (
        fields["date_of_birth"].value_text == "1985-04-12"
        and fields["date_of_birth"].validation_status == "VALID"
    )
    assert fields["date_of_birth"].page == 1
    assert fields["document_number"].validation_status == "VALID"
    assert fields["full_name"].validation_status == "UNVERIFIED"  # free text is never "verified"
    ambiguous = parse("application/pdf", fx.pdf("Full name: X", "Date of birth: 03/04/1985"), LIMITS)
    dob = next(f for f in extract("PASSPORT", ambiguous) if f.field_key == "date_of_birth")
    assert dob.validation_status == "INVALID_FORMAT" and dob.value_text == "03/04/1985"
    missing = {f.field_key: f for f in extract("PASSPORT", ambiguous)}
    assert missing["expiry_date"].value_state == "MISSING"
    dup = parse("application/pdf", fx.pdf("Full name: A", "Full name: B"), LIMITS)
    assert {f.validation_status for f in extract("PASSPORT", dup) if f.field_key == "full_name"} == {
        "CONFLICTING"
    }
    failed = extract("PASSPORT", {"status": "OCR_UNAVAILABLE"})
    assert {f.value_state for f in failed} == {"EXTRACTION_FAILED"}  # not MISSING: unreadable


@pytest.mark.req("FR-014", "DIR-013", "DIR-008")
def test_statement_rows_are_typed_with_row_provenance_and_explicit_currency() -> None:
    fields = extract("BANK_STATEMENT", parse("text/csv", fx.statement_csv(), LIMITS))
    amounts = [f for f in fields if f.field_key == "transaction.amount"]
    assert [(f.row, f.currency, f.validation_status) for f in amounts] == [
        (2, "AED", "VALID"),
        (3, "AED", "VALID"),
        (4, None, "INVALID_FORMAT"),  # no currency: never assumed
    ]
    times = [f for f in fields if f.field_key == "transaction.booking_time"]
    assert times[2].validation_status == "INVALID_FORMAT"  # naive timestamp rejected
    assert parse_money("AED 1,250.50") is not None and parse_money("1250.50") is None


@pytest.mark.req("MST:SEC-001", "SPR-017")
@pytest.mark.release_blocker
def test_blob_store_encrypts_and_binds_objects_to_their_key(tmp_path: Path) -> None:
    store = LocalBlobStore(tmp_path, Encryptor(b"k" * 32))
    a, b = uuid.uuid4(), uuid.uuid4()
    key_a, key_b = new_key(a, "documents"), new_key(b, "documents")
    store.put(key_a, b"SYNTHETIC secret")
    raw = (tmp_path / f"{key_a}.bin").read_bytes()
    assert b"SYNTHETIC secret" not in raw  # encrypted at rest
    assert store.get(key_a) == b"SYNTHETIC secret"
    (tmp_path / f"{key_b}.bin").parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / f"{key_b}.bin").write_bytes(raw)  # copy tenant A's object under tenant B's key
    with pytest.raises(DecryptionError):
        store.get(key_b)
    for bad in ("../../etc/passwd", f"{a}/documents/../../x", f"{a}/Documents/{b}"):
        with pytest.raises(ValueError):
            store.get(bad)
