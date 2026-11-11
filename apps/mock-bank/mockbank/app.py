"""SYNTHETIC mock-bank API (local/test only). Run: `uvicorn mockbank.app:app --port 8100`.

Every response carries `schema_version` and `as_of`. Data is scoped by API key -> bank. The
`/_mock/*` control endpoints (fault injection, state inspection) exist only for tests and are
disabled unless MOCKBANK_ENABLE_CONTROL=1.
"""

from __future__ import annotations

import asyncio
import base64
import csv
import io
import os
import threading
import zlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from mockbank.data import API_KEYS, Bank, build_all

SCHEMA_VERSION = "1.0"
MAX_PAGE = 100


@dataclass
class Fault:
    bank: str
    path_prefix: str
    kind: str  # timeout | status | revoked | stale | schema_version | duplicate_page
    value: str
    remaining: int


class FaultSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bank: str
    path_prefix: str
    kind: str
    value: str = ""
    times: int = 1


class StatusUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: str
    reason_code: str


class NotificationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    recipient_role: str
    template: str
    resource_ref: str


class State:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        self.banks: dict[str, Bank] = build_all()
        self.faults: list[Fault] = []
        self.case_status_ops: dict[tuple[str, str], dict[str, Any]] = {}
        self.notifications: dict[tuple[str, str], dict[str, Any]] = {}
        self.calls: list[tuple[str, str]] = []  # (bank, path) for test assertions
        self.revoked_customers: set[tuple[str, str]] = set()


STATE = State()
app = FastAPI(title="SYNTHETIC mock bank", version=SCHEMA_VERSION)
CONTROL = os.environ.get("MOCKBANK_ENABLE_CONTROL", "1") == "1"


def _now() -> datetime:
    return datetime.now(UTC)


def _bank(authorization: str | None) -> Bank:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "missing credentials")
    bank = API_KEYS.get(authorization.removeprefix("Bearer ").strip())
    if bank is None:
        raise HTTPException(401, "invalid credentials")
    return STATE.banks[bank]


def _take_fault(bank: str, path: str) -> Fault | None:
    with STATE.lock:
        for f in STATE.faults:
            if f.bank == bank and path.startswith(f.path_prefix) and f.remaining != 0:
                f.remaining -= 1
                return f
    return None


@app.middleware("http")
async def faults_and_tracing(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    path = request.url.path
    bank = API_KEYS.get(request.headers.get("authorization", "").removeprefix("Bearer ").strip(), "")
    if bank and not path.startswith("/_mock"):
        STATE.calls.append((bank, path))
        fault = _take_fault(bank, path)
        if fault is not None:
            request.state.fault = fault
            if fault.kind == "timeout":
                await asyncio.sleep(float(fault.value or "2"))
            elif fault.kind == "status":
                code = int(fault.value)
                headers = {"Retry-After": "1"} if code == 429 else {}
                return JSONResponse({"error": "SYNTHETIC_FAULT"}, status_code=code, headers=headers)
            elif fault.kind == "revoked":
                return JSONResponse({"error": "CONSENT_REVOKED"}, status_code=403)
    response = await call_next(request)
    response.headers["X-Correlation-Id"] = request.headers.get("x-correlation-id", "")
    return response


def _envelope(request: Request, body: dict[str, Any], as_of: datetime | None = None) -> dict[str, Any]:
    fault: Fault | None = getattr(request.state, "fault", None)
    version, stamp = SCHEMA_VERSION, as_of or _now()
    if fault is not None and fault.kind == "schema_version":
        version = fault.value
    if fault is not None and fault.kind == "stale":
        stamp = _now() - timedelta(hours=float(fault.value or "48"))
    return {"schema_version": version, "as_of": stamp.isoformat(), **body}


def _customer_or_404(bank: Bank, ref: str) -> dict[str, Any]:
    if (bank.key, ref) in STATE.revoked_customers:
        raise HTTPException(403, "CONSENT_REVOKED")
    customer = bank.customers.get(ref)
    if customer is None:
        raise HTTPException(404, "not found")
    return customer


Auth = Annotated[str | None, Header()]


@app.get("/v1/customers/{customer_ref}")
def get_customer(customer_ref: str, request: Request, authorization: Auth = None) -> dict[str, Any]:
    bank = _bank(authorization)
    return _envelope(request, {"customer": _customer_or_404(bank, customer_ref)})


@app.get("/v1/customers/{customer_ref}/documents")
def list_documents(customer_ref: str, request: Request, authorization: Auth = None) -> dict[str, Any]:
    bank = _bank(authorization)
    _customer_or_404(bank, customer_ref)
    docs = [d for d in bank.documents.values() if d["customer_ref"] == customer_ref]
    return _envelope(request, {"items": sorted(docs, key=lambda d: d["document_id"])})


@app.get("/v1/documents/{document_id}/content")
def document_content(document_id: str, authorization: Auth = None) -> Response:
    bank = _bank(authorization)
    doc = bank.documents.get(document_id)
    if doc is None:
        raise HTTPException(404, "not found")
    _customer_or_404(bank, doc["customer_ref"])
    if doc["media_type"] == "text/csv":
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(["booking_time", "amount", "currency", "direction", "status"])
        for t in bank.transactions[doc["customer_ref"]]:
            w.writerow([t["booking_time"], t["amount"], t["currency"], t["direction"], t["status"]])
        return Response(out.getvalue().encode(), media_type="text/csv")
    return Response(_minimal_pdf(document_lines(bank, doc)), media_type="application/pdf")


def document_lines(bank: Bank, doc: dict[str, Any]) -> list[str]:
    """Key/value lines a KYC document would carry. customer-test-001's passport shows a different
    date of birth than the profile: a deliberate SYNTHETIC data-quality conflict (FR-015)."""
    customer = bank.customers[doc["customer_ref"]]
    lines = [f"SYNTHETIC {doc['document_type']} - not a real document", f"Full name: {customer['full_name']}"]
    if doc["document_type"] == "PASSPORT":
        dob = customer["date_of_birth"]
        if doc["customer_ref"] == "customer-test-001":
            dob = f"{int(dob[:4]) + 1}{dob[4:]}"
        lines += [
            f"Document number: P{zlib.crc32(doc['document_id'].encode()) % 10**8:08d}",
            f"Date of birth: {dob}",
            f"Nationality: {customer['nationality']}",
            f"Expiry date: {doc['expires_on']}",
        ]
    else:
        lines += [
            f"Address: {doc['customer_ref'][-3:]} Synthetic Street, Dubai",
            f"Issue date: {doc['issued_on']}",
        ]
    return lines


def _minimal_pdf(text: str | list[str]) -> bytes:
    lines = [text] if isinstance(text, str) else text
    escaped = [ln.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") for ln in lines]
    body = " T* ".join(f"({ln}) Tj" for ln in escaped)
    stream = f"BT /F1 12 Tf 14 TL 72 720 Td {body} ET".encode()
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for i, obj in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer << /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def _cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(f"o:{offset}".encode()).decode()


def _offset(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        return int(base64.urlsafe_b64decode(cursor.encode()).decode().removeprefix("o:"))
    except ValueError as exc:
        raise HTTPException(400, "invalid cursor") from exc


@app.get("/v1/customers/{customer_ref}/transactions")
def transactions(
    customer_ref: str,
    request: Request,
    from_: Annotated[datetime, Query(alias="from")],
    to: datetime,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = 50,
    authorization: Auth = None,
) -> dict[str, Any]:
    """Half-open window [from, to) (DATA-D02), ordered by (booking_time, id), cursor pagination."""
    bank = _bank(authorization)
    _customer_or_404(bank, customer_ref)
    if from_.tzinfo is None or to.tzinfo is None or from_ >= to:
        raise HTTPException(400, "window must be timezone-aware with from < to")
    rows = [
        t for t in bank.transactions[customer_ref] if from_ <= datetime.fromisoformat(t["booking_time"]) < to
    ]
    offset = _offset(cursor)
    page = rows[offset : offset + limit]
    fault: Fault | None = getattr(request.state, "fault", None)
    if fault is not None and fault.kind == "duplicate_page" and offset > 0:
        page = rows[offset - 1 : offset - 1 + limit]  # source re-sends the previous page's last item
    next_offset = offset + limit
    return _envelope(
        request,
        {
            "items": page,
            "next_cursor": _cursor(next_offset) if next_offset < len(rows) else None,
            "total_count": len(rows),
            "window": {"from": from_.isoformat(), "to": to.isoformat(), "semantics": "[from, to)"},
            "ordering": "booking_time,source_transaction_id",
        },
    )


@app.get("/v1/sanctions/summary/{customer_ref}")
def sanctions(customer_ref: str, request: Request, authorization: Auth = None) -> dict[str, Any]:
    bank = _bank(authorization)
    _customer_or_404(bank, customer_ref)
    return _envelope(request, {"summary": bank.sanctions[customer_ref]})


@app.get("/v1/policies")
def policies(request: Request, authorization: Auth = None) -> dict[str, Any]:
    bank = _bank(authorization)
    return _envelope(request, {"items": bank.policies})


@app.get("/v1/alerts")
def alerts(
    request: Request,
    after: int = 0,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = 50,
    authorization: Auth = None,
) -> dict[str, Any]:
    bank = _bank(authorization)
    items = [a for a in bank.alerts if a["sequence"] > after][:limit]
    return _envelope(request, {"items": items, "high_watermark": items[-1]["sequence"] if items else after})


@app.put("/v1/cases/{case_ref}/status")
def put_case_status(
    case_ref: str,
    body: StatusUpdate,
    request: Request,
    idempotency_key: Annotated[str | None, Header()] = None,
    authorization: Auth = None,
) -> dict[str, Any]:
    bank = _bank(authorization)
    if not idempotency_key:
        raise HTTPException(400, "Idempotency-Key required")
    key = (bank.key, idempotency_key)
    with STATE.lock:
        existing = STATE.case_status_ops.get(key)
        if existing is not None:
            if existing["request"] != {"case_ref": case_ref, **body.model_dump()}:
                raise HTTPException(409, "idempotency key reused with a different request")
            replay: dict[str, Any] = existing["response"]
            return _envelope(request, replay)  # same result for the same key + body
        response = {
            "operation_ref": f"op-{len(STATE.case_status_ops) + 1:06d}",
            "case_ref": case_ref,
            "status": body.status,
            "applied_at": _now().isoformat(),
        }
        STATE.case_status_ops[key] = {
            "request": {"case_ref": case_ref, **body.model_dump()},
            "response": response,
        }
    return _envelope(request, response)


@app.get("/v1/case-status-operations/{idempotency_key}")
def case_status_operation(
    idempotency_key: str, request: Request, authorization: Auth = None
) -> dict[str, Any]:
    bank = _bank(authorization)
    found = STATE.case_status_ops.get((bank.key, idempotency_key))
    return _envelope(request, {"state": "APPLIED" if found else "NOT_FOUND"})


@app.post("/v1/notifications", status_code=202)
def notify(
    body: NotificationIn,
    request: Request,
    idempotency_key: Annotated[str | None, Header()] = None,
    authorization: Auth = None,
) -> dict[str, Any]:
    bank = _bank(authorization)
    if not idempotency_key:
        raise HTTPException(400, "Idempotency-Key required")
    with STATE.lock:
        STATE.notifications.setdefault((bank.key, idempotency_key), body.model_dump())
    return _envelope(request, {"accepted": True})


@app.get("/v1/notifications/{idempotency_key}")
def notification_status(idempotency_key: str, request: Request, authorization: Auth = None) -> dict[str, Any]:
    bank = _bank(authorization)
    found = (bank.key, idempotency_key) in STATE.notifications
    return _envelope(request, {"state": "DELIVERED" if found else "NOT_FOUND"})


# ------------------------------------------------------------------ test-only controls
def _control() -> None:
    if not CONTROL:
        raise HTTPException(404, "not found")


@app.post("/_mock/faults")
def add_fault(spec: FaultSpec) -> dict[str, str]:
    _control()
    with STATE.lock:
        STATE.faults.append(Fault(spec.bank, spec.path_prefix, spec.kind, spec.value, spec.times))
    return {"ok": "fault added"}


@app.post("/_mock/revoke")
def revoke(bank: str, customer_ref: str) -> dict[str, str]:
    _control()
    STATE.revoked_customers.add((bank, customer_ref))
    return {"ok": "revoked"}


@app.post("/_mock/reset")
def reset() -> dict[str, str]:
    _control()
    with STATE.lock:
        STATE.reset()
    return {"ok": "reset"}


@app.get("/_mock/state")
def state() -> dict[str, Any]:
    _control()
    return {
        "case_status_ops": len(STATE.case_status_ops),
        "notifications": len(STATE.notifications),
        "calls": [list(c) for c in STATE.calls[-200:]],
    }
