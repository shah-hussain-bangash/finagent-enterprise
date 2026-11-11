"""SYNTHETIC test documents: clean, malicious, encrypted, oversized and malformed (prompt §26)."""

from __future__ import annotations

import io
import zipfile

from mockbank.app import _minimal_pdf

from finagent.documents.scanner import EICAR

_CT = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" '
    'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    "</Types>"
)
_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def pdf(*lines: str) -> bytes:
    return _minimal_pdf(list(lines))


def passport_pdf(name: str = "SYNTHETIC Person 001", dob: str = "1985-04-12") -> bytes:
    return pdf(
        "SYNTHETIC PASSPORT - not a real document",
        f"Full name: {name}",
        "Document number: P12345678",
        f"Date of birth: {dob}",
        "Nationality: AE",
        "Expiry date: 2031-01-01",
    )


def blank_pdf(pages: int = 1) -> bytes:
    from pypdf import PdfWriter

    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=200, height=200)
    out = io.BytesIO()
    w.write(out)
    return out.getvalue()


def encrypted_pdf() -> bytes:
    from pypdf import PdfReader, PdfWriter

    w = PdfWriter(clone_from=PdfReader(io.BytesIO(passport_pdf())))
    w.encrypt(user_password="synthetic-user", owner_password="synthetic-owner", algorithm="RC4-128")
    out = io.BytesIO()
    w.write(out)
    return out.getvalue()


def javascript_pdf() -> bytes:
    data = passport_pdf()
    return data.replace(
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Catalog /Pages 2 0 R /OpenAction << /S /JavaScript /JS (app.alert(1)) >> >>",
    )


def _body(paragraphs: list[tuple[str, str | None]], table: list[list[str]] | None = None) -> str:
    parts = []
    for text, style in paragraphs:
        ppr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
        parts.append(f"<w:p>{ppr}<w:r><w:t>{text}</w:t></w:r></w:p>")
    if table:
        rows = "".join(
            "<w:tr>" + "".join(f"<w:tc><w:p><w:r><w:t>{c}</w:t></w:r></w:p></w:tc>" for c in row) + "</w:tr>"
            for row in table
        )
        parts.append(f"<w:tbl>{rows}</w:tbl>")
    head = f'<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="{_W}">'
    return f"{head}<w:body>{''.join(parts)}</w:body></w:document>"


def docx(
    paragraphs: list[tuple[str, str | None]] | None = None,
    table: list[list[str]] | None = None,
    *,
    document_xml: str | None = None,
    extra: dict[str, bytes] | None = None,
    content_types: str = _CT,
) -> bytes:
    paragraphs = paragraphs or [
        ("Customer details", "Heading1"),
        ("Full name: SYNTHETIC Person 002", None),
        ("Address: 2 Synthetic Street, Dubai", None),
        ("Issue date: 2026-09-01", None),
    ]
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("word/document.xml", document_xml or _body(paragraphs, table))
        for name, data in (extra or {}).items():
            zf.writestr(name, data)
    return out.getvalue()


def macro_docx() -> bytes:
    return docx(extra={"word/vbaProject.bin": b"\x00SYNTHETIC-VBA"})


def bomb_docx() -> bytes:
    """~60 MB of whitespace in document.xml: tiny compressed, huge ratio (decompression bomb)."""
    xml = _body([("bomb", None)]).replace("<w:body>", "<w:body>" + " " * (60 * 1024 * 1024))
    return docx(document_xml=xml)


def xxe_docx() -> bytes:
    xml = (
        '<?xml version="1.0"?><!DOCTYPE d [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
        f'<w:document xmlns:w="{_W}"><w:body><w:p><w:r><w:t>&x;</w:t></w:r></w:p></w:body></w:document>'
    )
    return docx(document_xml=xml)


def statement_csv() -> bytes:
    return (
        b"booking_time,amount,currency,direction,status\n"
        b"2026-09-20T09:00:00+04:00,100.00,AED,CREDIT,POSTED\n"
        b"2026-09-21T09:00:00+04:00,250.50,AED,CREDIT,POSTED\n"
        b"2026-09-22T09:00:00,40.00,,CREDIT,PENDING\n"
    )


def eicar_csv() -> bytes:
    return b"a,b\n" + EICAR + b",1\n"
