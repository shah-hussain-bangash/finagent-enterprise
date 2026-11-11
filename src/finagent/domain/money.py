"""Exact monetary values and deterministic calculation rules (DIR-008, DIR-013, FR-023, AER-009).

Agents never compute totals. They receive these results with the rule version and the excluded
items, and may only explain them.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import StrEnum

_CURRENCY = re.compile(r"^[A-Z]{3}$")


class Money:
    __slots__ = ("amount", "currency")

    def __init__(self, amount: Decimal | str, currency: str) -> None:
        if isinstance(amount, float):  # defensive: callers may bypass typing
            raise TypeError("float amounts are forbidden; use Decimal or str")
        try:
            value = Decimal(amount)
        except InvalidOperation as exc:
            raise ValueError("invalid decimal amount") from exc
        if not value.is_finite():
            raise ValueError("amount must be finite")
        if not _CURRENCY.fullmatch(currency):
            raise ValueError("currency must be an ISO-4217 style 3-letter code")
        self.amount, self.currency = value, currency

    def __add__(self, other: Money) -> Money:
        if other.currency != self.currency:
            raise ValueError("cannot add different currencies without an explicit conversion rule")
        return Money(self.amount + other.amount, self.currency)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Money) and (self.amount, self.currency) == (other.amount, other.currency)

    def __hash__(self) -> int:
        return hash((self.amount, self.currency))

    def __repr__(self) -> str:
        return f"Money({self.amount} {self.currency})"


class TxStatus(StrEnum):
    PENDING = "PENDING"
    POSTED = "POSTED"
    REVERSED = "REVERSED"


class Direction(StrEnum):
    CREDIT = "CREDIT"
    DEBIT = "DEBIT"


@dataclass(frozen=True)
class Txn:
    source_transaction_id: str
    amount: Money
    direction: Direction
    status: TxStatus


@dataclass(frozen=True)
class Exclusion:
    source_transaction_id: str
    reason: str


@dataclass
class CalculationResult:
    rule_id: str
    rule_version: str
    totals: dict[str, Money] = field(default_factory=dict)  # per currency, never merged
    counts: dict[str, int] = field(default_factory=dict)
    exclusions: list[Exclusion] = field(default_factory=list)


POSTED_CREDIT_RULE_ID = "posted-credit-total"
POSTED_CREDIT_RULE_VERSION = "0.1-SYNTHETIC"  # test rule, NOT an approved AML rule (DIR-013)


def posted_credit_totals(transactions: list[Txn]) -> CalculationResult:
    """Sum posted credits per currency; disclose every excluded item with its reason."""
    result = CalculationResult(POSTED_CREDIT_RULE_ID, POSTED_CREDIT_RULE_VERSION)
    sums: dict[str, Decimal] = defaultdict(Decimal)
    for tx in transactions:
        if tx.direction is not Direction.CREDIT:
            result.exclusions.append(Exclusion(tx.source_transaction_id, "not_credit"))
            continue
        if tx.status is not TxStatus.POSTED:
            result.exclusions.append(Exclusion(tx.source_transaction_id, f"status_{tx.status.lower()}"))
            continue
        sums[tx.amount.currency] += tx.amount.amount
        result.counts[tx.amount.currency] = result.counts.get(tx.amount.currency, 0) + 1
    result.totals = {ccy: Money(total, ccy) for ccy, total in sorted(sums.items())}
    return result
