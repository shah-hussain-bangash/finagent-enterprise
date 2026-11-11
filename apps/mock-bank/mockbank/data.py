"""Deterministic SYNTHETIC data per bank. Same seed -> same data on every machine.

Includes the DIR-013 calculation fixture (AED 100.00 + 250.50 posted, AED 40.00 pending, USD 20.00)
and a structuring-like pattern of just-below-threshold cash deposits for customer-test-001, plus
two same-name customers (customer-007 / customer-008) for identity-resolution tests (DIR-011).
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

BANKS = ("bank-a", "bank-b")
API_KEYS = {"mock-key-a": "bank-a", "mock-key-b": "bank-b"}  # SYNTHETIC local credentials
DUBAI = timezone(timedelta(hours=4))
WINDOW_START = datetime(2026, 9, 15, tzinfo=UTC)
WINDOW_END = datetime(2026, 10, 15, tzinfo=UTC)


@dataclass
class Bank:
    key: str
    customers: dict[str, dict[str, Any]] = field(default_factory=dict)
    transactions: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    documents: dict[str, dict[str, Any]] = field(default_factory=dict)
    policies: list[dict[str, Any]] = field(default_factory=list)
    sanctions: dict[str, dict[str, Any]] = field(default_factory=dict)
    alerts: list[dict[str, Any]] = field(default_factory=list)


def _tx(
    tid: str, account: str, when: datetime, amount: str, ccy: str, direction: str, status: str, channel: str
) -> dict[str, Any]:
    return {
        "source_transaction_id": tid,
        "account_ref": account,
        "booking_time": when.isoformat(),  # original offset preserved (DIR-007)
        "value_date": when.date().isoformat(),  # date-only business field stays a date
        "amount": amount,  # decimal string, never float (DIR-008)
        "currency": ccy,
        "direction": direction,
        "status": status,
        "channel": channel,
        "counterparty_ref": f"cp-{tid[-4:]}",
        "description": "SYNTHETIC transaction",
    }


def _customer(bank: str, ref: str, name: str, rng: random.Random) -> dict[str, Any]:
    dob = date(1960 + rng.randint(0, 40), rng.randint(1, 12), rng.randint(1, 28))
    return {
        "customer_ref": ref,
        "source_system": f"core-banking-synthetic-{bank}",
        "full_name": name,
        "date_of_birth": dob.isoformat(),
        "nationality": rng.choice(["AE", "GB", "IN", "PK", "EG", "PH"]),
        "residence_country": "AE",
        "occupation": rng.choice(["Trader", "Engineer", "Retail owner", "Consultant"]),
        "customer_type": "INDIVIDUAL",
        "onboarding_date": date(2019 + rng.randint(0, 5), rng.randint(1, 12), 1).isoformat(),
        "kyc_status": "CURRENT",
        "kyc_last_review": date(2026, rng.randint(1, 8), 15).isoformat(),
        "expected_monthly_turnover": {"amount": f"{rng.randint(5, 40) * 1000}.00", "currency": "AED"},
        # Withheld vs unknown are different states (DIR-009):
        "tax_residency": {"state": "WITHHELD"} if rng.random() < 0.2 else {"state": "PRESENT", "value": "AE"},
        "classification": "CONFIDENTIAL",
    }


def build_bank(bank: str) -> Bank:
    rng = random.Random(f"finagent-mockbank-{bank}")  # noqa: S311 - synthetic data only
    b = Bank(bank)
    refs = ["customer-test-001", "customer-demo-001"] + [f"customer-{n:03d}" for n in range(2, 21)]
    for ref in refs:
        name = f"SYNTHETIC Person {ref[-3:]}"
        if ref in ("customer-007", "customer-008"):
            name = "SYNTHETIC Same Name"
        b.customers[ref] = _customer(bank, ref, name, rng)
        account = f"acct-{ref}-01"
        txs: list[dict[str, Any]] = []
        if ref == "customer-test-001":
            base = datetime(2026, 9, 20, 9, 0, tzinfo=DUBAI)
            txs += [
                _tx("txn-0001", account, base, "100.00", "AED", "CREDIT", "POSTED", "BRANCH"),
                _tx(
                    "txn-0002",
                    account,
                    base + timedelta(days=1),
                    "250.50",
                    "AED",
                    "CREDIT",
                    "POSTED",
                    "BRANCH",
                ),
                _tx(
                    "txn-0003",
                    account,
                    base + timedelta(days=2),
                    "40.00",
                    "AED",
                    "CREDIT",
                    "PENDING",
                    "BRANCH",
                ),
                _tx(
                    "txn-0004", account, base + timedelta(days=3), "20.00", "USD", "CREDIT", "POSTED", "WIRE"
                ),
            ]
            for i in range(12):  # cash deposits just below a SYNTHETIC 10,000 reporting threshold
                when = base + timedelta(days=4 + i, hours=i % 5)
                amt = f"{9300 + rng.randint(0, 690)}.00"
                txs.append(_tx(f"txn-{1000 + i:04d}", account, when, amt, "AED", "CREDIT", "POSTED", "CASH"))
            txs.append(
                _tx(
                    "txn-0900",
                    account,
                    base + timedelta(days=17),
                    "95000.00",
                    "AED",
                    "DEBIT",
                    "POSTED",
                    "WIRE",
                )
            )
            txs.append(
                _tx(
                    "txn-0901",
                    account,
                    base + timedelta(days=18),
                    "500.00",
                    "AED",
                    "CREDIT",
                    "REVERSED",
                    "CARD",
                )
            )
        else:
            for i in range(rng.randint(5, 40)):
                when = WINDOW_START + timedelta(hours=rng.randint(0, 29 * 24))
                txs.append(
                    _tx(
                        f"txn-{ref[-3:]}-{i:04d}",
                        account,
                        when,
                        f"{rng.randint(10, 5000)}.{rng.randint(0, 99):02d}",
                        "AED",
                        rng.choice(["CREDIT", "DEBIT"]),
                        "POSTED",
                        rng.choice(["CARD", "WIRE", "CASH"]),
                    )
                )
        txs.sort(key=lambda t: (t["booking_time"], t["source_transaction_id"]))
        b.transactions[ref] = txs
        for kind, days in (("PASSPORT", 900), ("PROOF_OF_ADDRESS", 60), ("BANK_STATEMENT", 20)):
            doc_id = f"doc-{ref}-{kind.lower()}"
            b.documents[doc_id] = {
                "document_id": doc_id,
                "customer_ref": ref,
                "document_type": kind,
                "version": 1,
                "issued_on": (date(2026, 10, 1) - timedelta(days=days)).isoformat(),
                "expires_on": (date(2026, 10, 1) + timedelta(days=3650)).isoformat()
                if kind == "PASSPORT"
                else None,
                "media_type": "text/csv" if kind == "BANK_STATEMENT" else "application/pdf",
                "classification": "CONFIDENTIAL",
                "status": "VERIFIED"
                if kind != "PROOF_OF_ADDRESS" or ref != "customer-test-001"
                else "EXPIRED",
            }
        b.sanctions[ref] = {
            "customer_ref": ref,
            "screening_status": "POTENTIAL_MATCH_PENDING_REVIEW" if ref == "customer-012" else "NO_MATCH",
            "list_version": "SYNTHETIC-LIST-2026-10-01",
            "screened_at": "2026-10-14T22:00:00+00:00",
        }
    b.policies = _policies(bank)
    return b


def _policies(bank: str) -> list[dict[str, Any]]:
    """SYNTHETIC policy corpus (P7 ingests it). Not an institutional policy."""
    return [
        {
            "policy_id": "pol-aml-structuring",
            "title": "SYNTHETIC Policy: Cash structuring indicators",
            "version": "2.0",
            "effective_from": "2026-01-01",
            "effective_to": None,
            "department": "AML",
            "classification": "INTERNAL",
            "sections": [
                {
                    "section": "1",
                    "heading": "Scope",
                    "text": "Applies to cash deposits reviewed in AML alerts.",
                },
                {
                    "section": "2",
                    "heading": "Indicators",
                    "text": "Repeated cash deposits just below the reporting threshold within 30 days "
                    "are an indicator requiring analyst review. An indicator is not a determination.",
                },
            ],
        },
        {
            "policy_id": "pol-aml-structuring",
            "title": "SYNTHETIC Policy: Cash structuring indicators",
            "version": "1.0",
            "effective_from": "2024-01-01",
            "effective_to": "2025-12-31",
            "department": "AML",
            "classification": "INTERNAL",
            "sections": [
                {"section": "2", "heading": "Indicators", "text": "Superseded wording kept for history only."}
            ],
        },
        {
            "policy_id": "pol-kyc-refresh",
            "title": "SYNTHETIC Policy: KYC refresh",
            "version": "1.3",
            "effective_from": "2025-06-01",
            "effective_to": None,
            "department": "KYC",
            "classification": "RESTRICTED",
            "sections": [
                {
                    "section": "3",
                    "heading": "Proof of address",
                    "text": "Proof of address must be dated within 90 days of the review.",
                }
            ],
        },
    ]


def build_all() -> dict[str, Bank]:
    return {b: build_bank(b) for b in BANKS}


def to_decimal(value: str) -> Decimal:
    return Decimal(value)
