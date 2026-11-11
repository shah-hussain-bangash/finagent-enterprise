"""Connector contract tests against the SYNTHETIC mock bank over real HTTP (prompt §13, §30).

Per connector: pagination, empty, timeout, 403, stale, duplicate, revocation, schema version
(CURSOR checklist P5). DIR-005..012, DIR-021, DIR-028, DIR-038, FR-012, MST:INT-001/002.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from finagent.database.seeds import sid
from finagent.database.session import session_factory
from finagent.integrations.factory import ConnectorFactory
from finagent.integrations.notifications.port import DeliveryStatus, Notification
from finagent.integrations.resilience import ConnectorError, ErrorKind
from finagent.integrations.sources import CaseStatusConnector, Freshness, HttpNotificationAdapter
from finagent.services.workflow_service.external_ops import ProbeResult

pytestmark = pytest.mark.integration
A, B = sid("tenant:a"), sid("tenant:b")
W0, W1 = datetime(2026, 9, 15, tzinfo=UTC), datetime(2026, 10, 15, tzinfo=UTC)


@pytest.fixture
def connectors(make_settings, app_db_url, bank):  # type: ignore[no-untyped-def]
    def make(**over: Any) -> ConnectorFactory:
        settings = make_settings(
            bank_base_url=bank.url,
            connector_timeout_seconds=over.pop("timeout", 1.0),
            connector_max_attempts=over.pop("attempts", 3),
            connector_circuit_failures=over.pop("circuit", 5),
            **over,
        )
        return ConnectorFactory(settings, session_factory(app_db_url))

    return make


# ------------------------------------------------------------------ customer / KYC
@pytest.mark.req("FR-012", "DIR-021", "DIR-009", "SPR-019", "DIR-006", "MST:INT-002")
def test_profile_is_projected_typed_fresh_and_tenant_scoped(connectors, bank) -> None:  # type: ignore[no-untyped-def]
    f = connectors()
    profile, meta = f.customers(A).profile("customer-test-001")
    other, _ = f.customers(B).profile("customer-test-001")
    assert profile.customer_ref == "customer-test-001" and profile.full_name.startswith("SYNTHETIC")
    assert profile.date_of_birth != other.date_of_birth  # each tenant sees only its own bank's record
    assert profile.expected_monthly_turnover is not None
    assert isinstance(profile.expected_monthly_turnover.amount, Decimal)
    assert profile.tax_residency.state in {"PRESENT", "WITHHELD"}  # withheld != missing (DIR-009)
    assert meta.freshness is Freshness.FRESH and meta.schema_version == "1.0" and len(meta.content_hash) == 64
    assert bank.calls("/customers/customer-test-001", "bank-a") == 1
    assert bank.calls("/customers/customer-test-001", "bank-b") == 1
    docs, _ = f.customers(A).documents("customer-test-001")
    assert {d.document_type for d in docs} == {"PASSPORT", "PROOF_OF_ADDRESS", "BANK_STATEMENT"}


@pytest.mark.req("DIR-021", "DIR-028", "AER-022")
def test_not_found_forbidden_and_revocation_are_permanent_and_not_retried(connectors, bank) -> None:  # type: ignore[no-untyped-def]
    f = connectors()
    with pytest.raises(ConnectorError) as err:
        f.customers(A).profile("customer-999")
    assert err.value.kind is ErrorKind.NOT_FOUND
    bank.fault("/v1/customers/customer-002", "status", "403")
    with pytest.raises(ConnectorError) as err:
        f.customers(A).profile("customer-002")
    assert err.value.kind is ErrorKind.FORBIDDEN and bank.calls("/customers/customer-002") == 1
    bank.revoke("customer-003")
    with pytest.raises(ConnectorError) as err:
        f.customers(A).profile("customer-003")
    assert err.value.kind is ErrorKind.REVOKED and bank.calls("/customers/customer-003") == 1


@pytest.mark.req("DIR-038", "DIR-021", "MST:INT-002")
def test_unsupported_schema_version_is_rejected_not_misparsed(connectors, bank) -> None:  # type: ignore[no-untyped-def]
    bank.fault("/v1/customers/customer-test-001", "schema_version", "2.0")
    with pytest.raises(ConnectorError) as err:
        connectors().customers(A).profile("customer-test-001")
    assert err.value.kind is ErrorKind.SCHEMA_UNSUPPORTED


@pytest.mark.req("DIR-024", "DIR-012", "FR-012")
def test_stale_source_is_labelled_stale(connectors, bank) -> None:  # type: ignore[no-untyped-def]
    bank.fault("/v1/customers/customer-test-001", "stale", "72")
    _, meta = connectors().customers(A).profile("customer-test-001")
    assert meta.freshness is Freshness.STALE
    assert datetime.now(UTC) - meta.as_of > timedelta(hours=70)


# ------------------------------------------------------------------ transactions (DIR-012)
@pytest.mark.release_blocker
@pytest.mark.req("DIR-012", "DIR-007", "DIR-008", "DIR-013", "FR-012", "FR-023", "DIR-005")
def test_transaction_window_paginates_to_a_complete_proof(connectors) -> None:  # type: ignore[no-untyped-def]
    conn = connectors().transactions(A)
    conn._page_size = 5  # force several pages
    window = conn.fetch_window("customer-test-001", W0, W1)
    c = window.completeness
    assert c.status == "COMPLETE" and c.cursor_completed and c.pages > 3
    assert c.unique == c.reported_total == len(window.items) and c.duplicates_removed == 0
    assert c.window_semantics == "[from, to)"
    by_id = {t.source_transaction_id: t for t in window.items}
    assert by_id["txn-0001"].amount == Decimal("100.00") and by_id["txn-0002"].amount == Decimal("250.50")
    assert by_id["txn-0003"].status == "PENDING" and by_id["txn-0004"].currency == "USD"
    assert by_id["txn-0001"].booking_time.utcoffset() == timedelta(hours=4)  # original offset kept
    assert [t.booking_time for t in window.items] == sorted(t.booking_time for t in window.items)


@pytest.mark.req("DIR-012")
def test_empty_window_is_complete_with_zero_items(connectors) -> None:  # type: ignore[no-untyped-def]
    window = connectors().transactions(A).fetch_window("customer-test-001", W1, W1 + timedelta(days=1))
    assert window.items == [] and window.completeness.status == "COMPLETE"
    assert window.completeness.reported_total == 0 and window.completeness.pages == 1


@pytest.mark.release_blocker
@pytest.mark.req("DIR-012", "NQ-020")
def test_duplicate_items_are_removed_and_counted(connectors, bank) -> None:  # type: ignore[no-untyped-def]
    bank.fault("/v1/customers/customer-test-001/transactions", "duplicate_page", times=-1)
    conn = connectors().transactions(A)
    conn._page_size = 5
    c = conn.fetch_window("customer-test-001", W0, W1).completeness
    assert c.duplicates_removed > 0 and c.unique == c.reported_total and c.status == "COMPLETE"


@pytest.mark.release_blocker
@pytest.mark.req("DIR-012")
def test_truncated_page_sequence_is_never_labelled_complete(connectors) -> None:  # type: ignore[no-untyped-def]
    conn = connectors().transactions(A)
    conn._page_size, conn._max_pages = 5, 2
    c = conn.fetch_window("customer-test-001", W0, W1).completeness
    assert c.status == "INCOMPLETE" and not c.cursor_completed
    assert "page_limit_reached_before_cursor_end" in c.reasons


@pytest.mark.req("DIR-012", "DIR-021")
def test_transactions_reject_naive_or_inverted_windows(connectors) -> None:  # type: ignore[no-untyped-def]
    conn = connectors().transactions(A)
    for start, end in ((W1, W0), (datetime(2026, 9, 15), datetime(2026, 10, 15))):
        with pytest.raises(ConnectorError) as err:
            conn.fetch_window("customer-test-001", start, end)
        assert err.value.kind is ErrorKind.INVALID_REQUEST


# ------------------------------------------------------------------ resilience
@pytest.mark.req("DIR-028", "AER-022", "NFR-008")
def test_timeout_is_retried_then_succeeds_and_persistent_timeout_fails_bounded(connectors, bank) -> None:  # type: ignore[no-untyped-def]
    bank.fault("/v1/sanctions/summary/customer-test-001", "timeout", "1.5", times=1)
    summary, _ = connectors(timeout=0.5).sanctions(A).summary("customer-test-001")
    assert summary.screening_status == "NO_MATCH"
    assert bank.calls("/sanctions/summary/customer-test-001") == 2
    bank.fault("/v1/sanctions/summary/customer-002", "timeout", "1.5", times=-1)
    t0 = time.monotonic()
    with pytest.raises(ConnectorError) as err:
        connectors(timeout=0.3, attempts=2).sanctions(A).summary("customer-002")
    assert err.value.kind is ErrorKind.TIMEOUT and time.monotonic() - t0 < 5
    assert bank.calls("/sanctions/summary/customer-002") == 2  # bounded: exactly max_attempts


@pytest.mark.req("DIR-028", "NQ-028")
def test_rate_limited_source_is_retried_after_retry_after(connectors, bank) -> None:  # type: ignore[no-untyped-def]
    bank.fault("/v1/customers/customer-002", "status", "429", times=1)
    profile, _ = connectors().customers(A).profile("customer-002")
    assert profile.customer_ref == "customer-002" and bank.calls("/customers/customer-002") == 2


@pytest.mark.release_blocker
@pytest.mark.req("DIR-028", "NQ-017", "NQ-028")
def test_circuit_breaker_opens_fails_fast_and_is_isolated_per_tenant(connectors, bank) -> None:  # type: ignore[no-untyped-def]
    bank.fault("/v1/customers/", "status", "503", times=-1)
    f = connectors(attempts=1, circuit=3)
    for _ in range(3):
        with pytest.raises(ConnectorError):
            f.customers(A).profile("customer-002")
    before = bank.calls("/customers/")
    with pytest.raises(ConnectorError) as err:
        f.customers(A).profile("customer-002")
    assert err.value.kind is ErrorKind.CIRCUIT_OPEN and bank.calls("/customers/") == before  # no call made
    profile, _ = f.customers(B).profile("customer-002")  # tenant B's circuit is unaffected
    assert profile.customer_ref == "customer-002"


@pytest.mark.release_blocker
@pytest.mark.req("SPR-013", "SPR-016", "SPR-029", "AER-025")
def test_parameters_cannot_change_path_host_or_traverse(connectors, bank) -> None:  # type: ignore[no-untyped-def]
    for ref in ("../admin", "customer-001/../../x", "http://evil.example/", "a b", "x?y=1", "%2e%2e"):
        with pytest.raises(ConnectorError) as err:
            connectors().customers(A).profile(ref)
        assert err.value.kind is ErrorKind.INVALID_REQUEST
    assert bank.state()["calls"] == []  # rejected before any network call


# ------------------------------------------------------------------ the single write + notifications
@pytest.mark.release_blocker
@pytest.mark.req("DIR-029", "NQ-020", "MST:REC-002", "MST:INT-001")
def test_case_status_write_is_idempotent_by_key_and_probeable(connectors, bank) -> None:  # type: ignore[no-untyped-def]
    client = connectors().client(A)
    effect = CaseStatusConnector(client, "case-ref-1", "UNDER_REVIEW_COMPLETE", "REVIEW_APPROVED")
    assert effect.probe("op-key-0001") is ProbeResult.NOT_FOUND
    first, again = effect.apply("op-key-0001"), effect.apply("op-key-0001")
    assert first == again and bank.state()["case_status_ops"] == 1
    assert effect.probe("op-key-0001") is ProbeResult.APPLIED
    other = CaseStatusConnector(client, "case-ref-1", "RETURNED_FOR_REWORK", "REVIEW_APPROVED")
    from finagent.services.workflow_service.external_ops import EffectRejectedError

    with pytest.raises(EffectRejectedError):  # same key, different request -> definite rejection
        other.apply("op-key-0001")


@pytest.mark.req("FR-011", "NQ-020")
def test_http_notification_adapter_dedupes_and_reports_status(connectors, bank) -> None:  # type: ignore[no-untyped-def]
    adapter = HttpNotificationAdapter(connectors().client(A))
    n = Notification(A, "escalation:test-0001", "AML_SUPERVISOR", "case.escalated", "case-1")
    adapter.send(n)
    adapter.send(n)
    assert bank.state()["notifications"] == 1
    assert adapter.status(A, "escalation:test-0001") is DeliveryStatus.DELIVERED
    assert adapter.status(A, "escalation:other") is DeliveryStatus.NOT_FOUND


@pytest.mark.req("DIR-021", "SPR-007")
def test_tenant_without_configured_credentials_cannot_connect(connectors) -> None:  # type: ignore[no-untyped-def]
    f = connectors(connector_credentials='{"synthetic-bank-a": "mock-key-a"}')
    f.customers(A).profile("customer-002")
    with pytest.raises(ConnectorError) as err:
        f.customers(B).profile("customer-002")
    assert err.value.kind is ErrorKind.UNAUTHORIZED


@pytest.mark.req("FR-011", "DIR-021", "SPR-007")
def test_bank_notification_router_uses_each_tenants_own_connector(connectors, bank) -> None:  # type: ignore[no-untyped-def]
    from finagent.integrations.factory import BankNotificationRouter

    router = BankNotificationRouter(connectors())
    router.send(Notification(A, "run:x:notify-started", "ANALYST", "workflow.started", "case-1"))
    assert router.status(A, "run:x:notify-started") is DeliveryStatus.DELIVERED
    assert (
        router.status(B, "run:x:notify-started") is DeliveryStatus.NOT_FOUND
    )  # tenant B's bank never saw it
