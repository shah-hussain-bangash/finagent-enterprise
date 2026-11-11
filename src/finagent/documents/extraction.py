"""Typed field extraction with provenance (FR-014; DIR-009; AER-017; prompt §15).

Deterministic rules - no model. Every field records value, type, page/row/section, validation
status and a confidence note. Distinct states are never merged:

* PRESENT + VALID           the text matched the field's format rule (authenticity NOT verified)
* PRESENT + UNVERIFIED      free text without a format rule
* PRESENT + INVALID_FORMAT  kept verbatim, not interpreted (e.g. ambiguous date, money w/o currency)
* PRESENT + CONFLICTING     the same field appears with different values; all are kept
* MISSING                   the document was readable but the required field is absent
* EXTRACTION_FAILED         the document could not be read (OCR unavailable, corrupt, ...)

The extraction profile is versioned and marked SYNTHETIC: the approved field list per document type
is an OPEN decision (AI-D02 / DATA-D04).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

PROFILE_VERSION = "kyc-fields-v1-SYNTHETIC"

TEXT, DATE, DATETIME, MONEY, IDENTIFIER, ENUM = "TEXT", "DATE", "DATETIME", "MONEY", "IDENTIFIER", "ENUM"

_ALIASES = {
    "full name": "full_name",
    "name": "full_name",
    "customer name": "full_name",
    "document number": "document_number",
    "passport number": "document_number",
    "passport no": "document_number",
    "date of birth": "date_of_birth",
    "dob": "date_of_birth",
    "nationality": "nationality",
    "expiry date": "expiry_date",
    "date of expiry": "expiry_date",
    "issue date": "issue_date",
    "date of issue": "issue_date",
    "address": "address",
    "residential address": "address",
    "statement date": "statement_date",
    "customer reference": "customer_ref",
}
FIELD_TYPES = {
    "full_name": TEXT,
    "document_number": IDENTIFIER,
    "date_of_birth": DATE,
    "nationality": TEXT,
    "expiry_date": DATE,
    "issue_date": DATE,
    "address": TEXT,
    "statement_date": DATE,
    "customer_ref": IDENTIFIER,
}
REQUIRED = {
    "PASSPORT": ("full_name", "document_number", "date_of_birth", "expiry_date"),
    "PROOF_OF_ADDRESS": ("full_name", "address", "issue_date"),
}
STATEMENT_COLUMNS = ("booking_time", "amount", "currency", "direction", "status")
_KV = re.compile(r"^\s*([A-Za-z][A-Za-z .'/-]{0,40}?)\s*:\s*(.+?)\s*$")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TEXT_DATE = re.compile(r"^\d{1,2} [A-Za-z]{3} \d{4}$")
_NUMERIC_DATE = re.compile(r"^\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}$")
_IDENT = re.compile(r"^[A-Z0-9][A-Z0-9-]{3,29}$")
_MONEY = re.compile(r"^(?:([A-Z]{3})\s*)?(-?[0-9][0-9,]*(?:\.[0-9]{1,4})?)(?:\s*([A-Z]{3}))?$")
_CCY = re.compile(r"^[A-Z]{3}$")


@dataclass(frozen=True)
class ExtractedField:
    field_key: str
    value_text: str | None
    value_type: str
    value_state: str
    validation_status: str
    confidence_note: str
    currency: str | None = None
    page: int | None = None
    row: int | None = None
    section: str | None = None


def typed_field(key: str, raw: str, prov: dict[str, Any]) -> ExtractedField:
    kind = FIELD_TYPES.get(key, TEXT)
    raw = raw.strip()[:1000]
    base = {"page": prov.get("page"), "row": prov.get("row"), "section": prov.get("section")}
    if kind == DATE:
        return _date_field(key, raw, base)
    if kind == IDENTIFIER:
        if _IDENT.fullmatch(raw):
            return ExtractedField(
                key, raw, kind, "PRESENT", "VALID", "format validated; authenticity not verified", **base
            )
        return ExtractedField(
            key, raw, kind, "PRESENT", "INVALID_FORMAT", "identifier format not recognised", **base
        )
    return ExtractedField(
        key, raw, kind, "PRESENT", "UNVERIFIED", "extracted text; not verified against a source", **base
    )


def _date_field(key: str, raw: str, base: dict[str, Any]) -> ExtractedField:
    if _ISO_DATE.fullmatch(raw):
        try:
            value = date.fromisoformat(raw).isoformat()
        except ValueError:
            return ExtractedField(key, raw, DATE, "PRESENT", "INVALID_FORMAT", "not a calendar date", **base)
        return ExtractedField(
            key, value, DATE, "PRESENT", "VALID", "ISO date; authenticity not verified", **base
        )
    if _TEXT_DATE.fullmatch(raw):
        try:
            value = datetime.strptime(raw, "%d %b %Y").date().isoformat()
        except ValueError:
            return ExtractedField(key, raw, DATE, "PRESENT", "INVALID_FORMAT", "not a calendar date", **base)
        return ExtractedField(
            key, value, DATE, "PRESENT", "VALID", "unambiguous date; authenticity not verified", **base
        )
    if _NUMERIC_DATE.fullmatch(raw):
        note = "ambiguous day/month order; kept verbatim and not interpreted"
        return ExtractedField(key, raw, DATE, "PRESENT", "INVALID_FORMAT", note, **base)
    return ExtractedField(key, raw, DATE, "PRESENT", "INVALID_FORMAT", "date format not recognised", **base)


def parse_money(raw: str) -> tuple[Decimal, str] | None:
    """Decimal + explicit ISO currency, or None. A number without a currency is never money."""
    m = _MONEY.fullmatch(raw.strip())
    if m is None:
        return None
    ccy = m.group(1) or m.group(3)
    if not ccy or (m.group(1) and m.group(3)):
        return None
    try:
        return Decimal(m.group(2).replace(",", "")), ccy
    except InvalidOperation:
        return None


def _key_values(segments: list[dict[str, Any]]) -> list[tuple[str, str, dict[str, Any]]]:
    out: list[tuple[str, str, dict[str, Any]]] = []
    for seg in segments:
        for line in str(seg.get("text", "")).splitlines():
            m = _KV.match(line)
            if not m:
                continue
            key = _ALIASES.get(re.sub(r"\s+", " ", m.group(1).strip().lower()))
            if key:
                out.append((key, m.group(2), seg))
    return out


def _statement_rows(parse: dict[str, Any]) -> list[ExtractedField]:
    columns = [c.strip().lower() for c in (parse.get("columns") or [])]
    fields: list[ExtractedField] = []
    missing = [c for c in STATEMENT_COLUMNS if c not in columns]
    for col in missing:
        fields.append(
            ExtractedField(f"statement.{col}", None, TEXT, "MISSING", "MISSING", "required column absent")
        )
    if missing:
        return fields
    idx = {c: columns.index(c) for c in STATEMENT_COLUMNS}
    for seg in parse.get("segments", []):
        cells = [str(c) for c in (seg.get("cells") or [])]
        row = seg.get("row")
        values = {c: (cells[i].strip() if i < len(cells) else "") for c, i in idx.items()}
        when, amount, ccy = values["booking_time"], values["amount"], values["currency"]
        try:
            ts = datetime.fromisoformat(when)
            ok_ts = ts.tzinfo is not None
        except ValueError:
            ok_ts = False
        fields.append(
            ExtractedField(
                "transaction.booking_time",
                when or None,
                DATETIME,
                "PRESENT" if when else "MISSING",
                "VALID" if ok_ts else ("MISSING" if not when else "INVALID_FORMAT"),
                "timezone-aware timestamp" if ok_ts else "timestamp missing or without offset",
                row=row,
            )
        )
        money_ok = bool(_CCY.fullmatch(ccy))
        try:
            Decimal(amount)
        except InvalidOperation:
            money_ok = False
        fields.append(
            ExtractedField(
                "transaction.amount",
                amount or None,
                MONEY,
                "PRESENT" if amount else "MISSING",
                "VALID" if money_ok else "INVALID_FORMAT",
                "decimal amount with explicit currency" if money_ok else "amount or currency invalid",
                currency=ccy if _CCY.fullmatch(ccy) else None,
                row=row,
            )
        )
        for col in ("direction", "status"):
            value = values[col]
            fields.append(
                ExtractedField(
                    f"transaction.{col}",
                    value or None,
                    ENUM,
                    "PRESENT" if value else "MISSING",
                    "UNVERIFIED" if value else "MISSING",
                    "statement value; not reconciled to the ledger",
                    row=row,
                )
            )
    return fields


def extract(document_type: str, parse: dict[str, Any]) -> list[ExtractedField]:
    status = parse.get("status")
    required = REQUIRED.get(document_type, ())
    if status != "PARSED":
        note = f"document not readable ({status}); value not extracted"
        return [
            ExtractedField(k, None, FIELD_TYPES.get(k, TEXT), "EXTRACTION_FAILED", "MISSING", note)
            for k in required
        ]
    if document_type == "BANK_STATEMENT" or parse.get("columns") is not None:
        return _statement_rows(parse)
    fields = [typed_field(k, v, seg) for k, v, seg in _key_values(parse.get("segments", []))]
    by_key: dict[str, set[str]] = {}
    for f in fields:
        by_key.setdefault(f.field_key, set()).add((f.value_text or "").casefold())
    conflicted = {k for k, values in by_key.items() if len(values) > 1}
    fields = [
        ExtractedField(
            f.field_key,
            f.value_text,
            f.value_type,
            f.value_state,
            "CONFLICTING",
            "different values for the same field in this document; all kept",
            f.currency,
            f.page,
            f.row,
            f.section,
        )
        if f.field_key in conflicted
        else f
        for f in fields
    ]
    present = {f.field_key for f in fields}
    for key in required:
        if key not in present:
            fields.append(
                ExtractedField(
                    key, None, FIELD_TYPES.get(key, TEXT), "MISSING", "MISSING", "required field not found"
                )
            )
    return fields
