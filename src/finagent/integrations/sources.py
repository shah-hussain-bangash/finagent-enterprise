"""Typed read connectors + the single case-status write port (prompt §13; FR-012; DIR-005..012).

Field projection (DIR-021, SPR-019): each typed model keeps ONLY the allowlisted fields; anything
else the source sends is dropped before it can reach storage or a model. Every result carries
`SourceMeta` (source, endpoint, retrieval time, as-of, schema version, freshness, content hash).

Transactions (DIR-012): `fetch_window` follows the cursor to completion and returns a completeness
proof. A window is COMPLETE only if the cursor chain ended, no page limit was hit, and the unique
count equals the source's reported total; otherwise it is INCOMPLETE and must not be presented as
the full investigation window.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError

from finagent.integrations.http_client import ConnectorHttpClient
from finagent.integrations.notifications.port import DeliveryStatus, Notification, NotificationError
from finagent.integrations.resilience import ConnectorError, ErrorKind
from finagent.services.workflow_service.external_ops import EffectRejectedError, ProbeResult


class Freshness(StrEnum):
    FRESH = "FRESH"
    STALE = "STALE"


class SourceMeta(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_system: str
    endpoint: str
    retrieved_at: AwareDatetime
    as_of: AwareDatetime
    schema_version: str
    freshness: Freshness
    content_hash: str  # identity of the retrieved content, not proof of correctness (DIR-017)


class _Projected(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)  # projection: unknown fields are dropped


class Money(_Projected):
    amount: Decimal
    currency: str = Field(pattern=r"^[A-Z]{3}$")


class FieldState(_Projected):
    state: Literal["PRESENT", "MISSING", "UNKNOWN", "WITHHELD", "NOT_APPLICABLE"]
    value: str | None = None


class CustomerProfile(_Projected):
    customer_ref: str
    source_system: str
    full_name: str
    date_of_birth: date | None
    nationality: str | None
    residence_country: str | None
    occupation: str | None
    customer_type: str
    onboarding_date: date | None
    kyc_status: str
    kyc_last_review: date | None
    expected_monthly_turnover: Money | None
    tax_residency: FieldState
    classification: str


class DocumentMeta(_Projected):
    document_id: str
    customer_ref: str
    document_type: str
    version: int
    issued_on: date | None
    expires_on: date | None
    media_type: str
    classification: str
    status: str


class Transaction(_Projected):
    source_transaction_id: str
    account_ref: str
    booking_time: AwareDatetime
    value_date: date
    amount: Decimal
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    direction: Literal["CREDIT", "DEBIT"]
    status: Literal["POSTED", "PENDING", "REVERSED"]
    channel: str
    counterparty_ref: str | None = None


class SanctionsSummary(_Projected):
    customer_ref: str
    screening_status: Literal["NO_MATCH", "POTENTIAL_MATCH_PENDING_REVIEW", "CONFIRMED_MATCH", "NOT_SCREENED"]
    list_version: str
    screened_at: AwareDatetime


class PolicySection(_Projected):
    section: str
    heading: str
    text: str


class PolicyDocument(_Projected):
    policy_id: str
    title: str
    version: str
    effective_from: date
    effective_to: date | None
    department: str
    classification: str
    sections: list[PolicySection]


class Completeness(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: Literal["COMPLETE", "INCOMPLETE"]
    reported_total: int
    fetched: int
    unique: int
    duplicates_removed: int
    pages: int
    cursor_completed: bool
    window_start: AwareDatetime
    window_end: AwareDatetime
    window_semantics: str
    source_as_of: AwareDatetime  # oldest as-of across pages
    exclusions: list[str]
    reasons: list[str]


class TransactionWindow(BaseModel):
    model_config = ConfigDict(frozen=True)

    customer_ref: str
    items: list[Transaction]
    completeness: Completeness
    meta: SourceMeta


def _hash(body: Any) -> str:
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()


class _Base:
    def __init__(self, client: ConnectorHttpClient, max_age: timedelta) -> None:
        self._c, self._max_age = client, max_age

    def _meta(self, body: dict[str, Any], endpoint: str, content: Any) -> SourceMeta:
        now = datetime.now(UTC)
        as_of = datetime.fromisoformat(str(body["as_of"]))
        return SourceMeta(
            source_system=self._c.source,
            endpoint=endpoint,
            retrieved_at=now,
            as_of=as_of,
            schema_version=str(body["schema_version"]),
            freshness=Freshness.STALE if now - as_of > self._max_age else Freshness.FRESH,
            content_hash=_hash(content),
        )

    def _parse(self, model: type[Any], data: Any) -> Any:
        try:
            return model.model_validate(data)
        except ValidationError as exc:  # unsupported enum/shape: never silently misparsed
            raise ConnectorError(ErrorKind.INVALID_RESPONSE, self._c.source) from exc


class CustomerKycConnector(_Base):
    def profile(self, customer_ref: str) -> tuple[CustomerProfile, SourceMeta]:
        body = self._c.request_json("GET", "/v1/customers/{customer_ref}", {"customer_ref": customer_ref})
        profile = self._parse(CustomerProfile, body.get("customer"))
        return profile, self._meta(body, "customers.get", profile.model_dump(mode="json"))

    def documents(self, customer_ref: str) -> tuple[list[DocumentMeta], SourceMeta]:
        body = self._c.request_json(
            "GET", "/v1/customers/{customer_ref}/documents", {"customer_ref": customer_ref}
        )
        docs = [self._parse(DocumentMeta, d) for d in body.get("items", [])]
        return docs, self._meta(body, "customers.documents", [d.model_dump(mode="json") for d in docs])


class TransactionConnector(_Base):
    def __init__(
        self, client: ConnectorHttpClient, max_age: timedelta, page_size: int = 50, max_pages: int = 50
    ):
        super().__init__(client, max_age)
        self._page_size, self._max_pages = page_size, max_pages

    def fetch_window(self, customer_ref: str, start: datetime, end: datetime) -> TransactionWindow:
        if start.tzinfo is None or end.tzinfo is None or start >= end:
            raise ConnectorError(ErrorKind.INVALID_REQUEST, self._c.source)
        seen: dict[str, Transaction] = {}
        fetched = pages = 0
        cursor: str | None = None
        reported, as_ofs, schema, cursor_done = 0, [], "1.0", False
        last_body: dict[str, Any] = {}
        while pages < self._max_pages:
            query: dict[str, Any] = {
                "from": start.isoformat(),
                "to": end.isoformat(),
                "limit": self._page_size,
            }
            if cursor:
                query["cursor"] = cursor
            body = self._c.request_json(
                "GET",
                "/v1/customers/{customer_ref}/transactions",
                {"customer_ref": customer_ref},
                query=query,
            )
            last_body, pages = body, pages + 1
            schema = str(body["schema_version"])
            as_ofs.append(datetime.fromisoformat(str(body["as_of"])))
            reported = int(body.get("total_count", 0))
            for raw in body.get("items", []):
                tx = self._parse(Transaction, raw)
                fetched += 1
                seen.setdefault(tx.source_transaction_id, tx)  # dedupe re-sent items, keep first
            cursor = body.get("next_cursor")
            if not cursor:
                cursor_done = True
                break
        items = sorted(seen.values(), key=lambda t: (t.booking_time, t.source_transaction_id))
        reasons: list[str] = []
        if not cursor_done:
            reasons.append("page_limit_reached_before_cursor_end")
        if len(items) != reported:
            reasons.append("unique_count_differs_from_reported_total")
        if any(not (start <= t.booking_time < end) for t in items):
            reasons.append("item_outside_requested_window")
        oldest = min(as_ofs) if as_ofs else datetime.now(UTC)
        completeness = Completeness(
            status="COMPLETE" if not reasons else "INCOMPLETE",
            reported_total=reported,
            fetched=fetched,
            unique=len(items),
            duplicates_removed=fetched - len(items),
            pages=pages,
            cursor_completed=cursor_done,
            window_start=start,
            window_end=end,
            window_semantics=str(last_body.get("window", {}).get("semantics", "[from, to)")),
            source_as_of=oldest,
            exclusions=[],  # calculation-time exclusions are disclosed by domain.money rules
            reasons=reasons,
        )
        meta = self._meta(
            {"as_of": oldest.isoformat(), "schema_version": schema},
            "customers.transactions",
            [t.model_dump(mode="json") for t in items],
        )
        return TransactionWindow(customer_ref=customer_ref, items=items, completeness=completeness, meta=meta)


class SanctionsConnector(_Base):
    """Read-only screening SUMMARY. The platform never makes or changes a sanctions decision."""

    def summary(self, customer_ref: str) -> tuple[SanctionsSummary, SourceMeta]:
        body = self._c.request_json(
            "GET", "/v1/sanctions/summary/{customer_ref}", {"customer_ref": customer_ref}
        )
        s = self._parse(SanctionsSummary, body.get("summary"))
        return s, self._meta(body, "sanctions.summary", s.model_dump(mode="json"))


class PolicyConnector(_Base):
    def policies(self) -> tuple[list[PolicyDocument], SourceMeta]:
        body = self._c.request_json("GET", "/v1/policies")
        docs = [self._parse(PolicyDocument, d) for d in body.get("items", [])]
        return docs, self._meta(body, "policies.list", [d.model_dump(mode="json") for d in docs])


class DocumentConnector:
    def __init__(self, client: ConnectorHttpClient, max_bytes: int) -> None:
        self._c, self._max = client, max_bytes

    def content(self, document_id: str) -> tuple[bytes, str]:
        return self._c.get_bytes(
            "/v1/documents/{document_id}/content", {"document_id": document_id}, self._max
        )


class CaseStatusConnector:
    """THE ONLY business write in R1 (DATA-D06). Callers go through the external-operation ledger;
    the feature flag is enforced by the tool gateway / finalization service, never here alone."""

    target_system = "case-status"

    def __init__(self, client: ConnectorHttpClient, case_ref: str, status: str, reason_code: str) -> None:
        self._c, self._case, self._status, self._reason = client, case_ref, status, reason_code

    def apply(self, operation_key: str) -> str | None:
        try:
            body = self._c.request_json(
                "PUT",
                "/v1/cases/{case_ref}/status",
                {"case_ref": self._case},
                json_body={"status": self._status, "reason_code": self._reason},
                idempotency_key=operation_key,
                retry=False,  # never blind-retry a write; the ledger probes instead (DIR-029)
            )
        except ConnectorError as exc:
            if not exc.transient:
                raise EffectRejectedError(exc.kind.value) from exc
            raise
        return str(body.get("operation_ref"))

    def probe(self, operation_key: str) -> ProbeResult:
        try:
            body = self._c.request_json("GET", "/v1/case-status-operations/{key}", {"key": operation_key})
        except ConnectorError:
            return ProbeResult.UNKNOWN
        return ProbeResult.APPLIED if body.get("state") == "APPLIED" else ProbeResult.NOT_FOUND


class HttpNotificationAdapter:
    """NotificationPort over the (mock) bank notification service; references only."""

    def __init__(self, client: ConnectorHttpClient) -> None:
        self._c = client

    def send(self, notification: Notification) -> None:
        try:
            self._c.request_json(
                "POST",
                "/v1/notifications",
                json_body={
                    "recipient_role": notification.recipient_role,
                    "template": notification.template,
                    "resource_ref": notification.resource_ref,
                },
                idempotency_key=notification.idempotency_key,
            )
        except ConnectorError as exc:
            raise NotificationError(exc.kind.value, transient=exc.transient) from exc

    def status(self, tenant_id: uuid.UUID, idempotency_key: str) -> DeliveryStatus:
        """The client is already bound to one tenant's credential; tenant_id is for the port."""
        try:
            body = self._c.request_json("GET", "/v1/notifications/{key}", {"key": idempotency_key[:128]})
        except ConnectorError:
            return DeliveryStatus.UNKNOWN
        return DeliveryStatus.DELIVERED if body.get("state") == "DELIVERED" else DeliveryStatus.NOT_FOUND
