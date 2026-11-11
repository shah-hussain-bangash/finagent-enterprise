"""Deterministic AML calculations for one transaction window (AER-009, DIR-013, FR-023, SPR-031).

Every value an agent may state as a number comes from here, with a stable `calc_id`, the rule id and
version, and the excluded items with reasons. Currencies are never merged; a ratio against expected
turnover is produced only when both are in the same currency. The reporting threshold and the
"just below" band are SYNTHETIC configuration (DEC-02/AI-D02), not an institutional rule.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any

from finagent.domain.money import Direction, Money, Txn, TxStatus, posted_credit_totals

RULE_VERSION = "aml-calcs-0.1-SYNTHETIC"
CENT = Decimal("0.01")


def fmt(amount: Decimal) -> str:
    return str(amount.quantize(CENT, rounding=ROUND_HALF_EVEN))


@dataclass(frozen=True)
class CalcValue:
    calc_id: str
    label: str
    value: str  # canonical: "12" or "113000.00"
    currency: str | None
    rule_id: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "calc_id": self.calc_id,
            "label": self.label,
            "value": self.value,
            "currency": self.currency,
            "rule_id": self.rule_id,
            "rule_version": RULE_VERSION,
        }


@dataclass
class CalcSet:
    values: dict[str, CalcValue] = field(default_factory=dict)
    exclusions: list[dict[str, str]] = field(default_factory=list)
    disclosures: list[str] = field(default_factory=list)

    def add(self, calc_id: str, label: str, value: str, currency: str | None, rule_id: str) -> None:
        self.values[calc_id] = CalcValue(calc_id, label, value, currency, rule_id)

    def as_dict(self) -> dict[str, Any]:
        return {
            "rule_version": RULE_VERSION,
            "values": {k: v.as_dict() for k, v in sorted(self.values.items())},
            "exclusions": self.exclusions,
            "disclosures": self.disclosures,
        }


@dataclass(frozen=True)
class Threshold:
    amount: Decimal
    currency: str
    band_pct: Decimal  # "just below" = [amount * (1 - band), amount)


def compute(
    items: list[dict[str, Any]],
    *,
    threshold: Threshold,
    window_start: datetime,
    window_end: datetime,
    expected_turnover: dict[str, Any] | None,
) -> CalcSet:
    out = CalcSet()
    txns = [
        Txn(
            str(t["source_transaction_id"]),
            Money(str(t["amount"]), str(t["currency"])),
            Direction(str(t["direction"])),
            TxStatus(str(t["status"])),
        )
        for t in items
    ]
    credits = posted_credit_totals(txns)
    for ccy, money in credits.totals.items():
        out.add(
            f"posted_credit_total_{ccy}", f"Posted credits ({ccy})", fmt(money.amount), ccy, credits.rule_id
        )
        out.add(
            f"posted_credit_count_{ccy}",
            f"Posted credit count ({ccy})",
            str(credits.counts[ccy]),
            None,
            credits.rule_id,
        )
    out.exclusions += [
        {"source_transaction_id": e.source_transaction_id, "reason": e.reason} for e in credits.exclusions
    ]
    debits: dict[str, Decimal] = defaultdict(Decimal)
    largest: dict[str, Decimal] = {}
    for tx in txns:
        if tx.direction is Direction.DEBIT and tx.status is TxStatus.POSTED:
            debits[tx.amount.currency] += tx.amount.amount
            largest[tx.amount.currency] = max(largest.get(tx.amount.currency, Decimal(0)), tx.amount.amount)
    for ccy, total in sorted(debits.items()):
        out.add(f"posted_debit_total_{ccy}", f"Posted debits ({ccy})", fmt(total), ccy, "posted-debit-total")
        out.add(
            f"largest_posted_debit_{ccy}",
            f"Largest posted debit ({ccy})",
            fmt(largest[ccy]),
            ccy,
            "largest-debit",
        )
    pending = sum(1 for t in txns if t.status is TxStatus.PENDING)
    reversed_ = sum(1 for t in txns if t.status is TxStatus.REVERSED)
    out.add("excluded_pending_count", "Pending transactions excluded", str(pending), None, "status-exclusion")
    out.add(
        "excluded_reversed_count", "Reversed transactions excluded", str(reversed_), None, "status-exclusion"
    )
    low = threshold.amount * (1 - threshold.band_pct / 100)
    band = [
        t
        for t in items
        if t["channel"] == "CASH"
        and t["direction"] == "CREDIT"
        and t["status"] == "POSTED"
        and t["currency"] == threshold.currency
        and low <= Decimal(str(t["amount"])) < threshold.amount
    ]
    rule = "cash-below-threshold"
    out.add(
        "cash_below_threshold_count", "Cash deposits just below the threshold", str(len(band)), None, rule
    )
    band_total = sum((Decimal(str(t["amount"])) for t in band), Decimal(0))
    out.add(
        f"cash_below_threshold_total_{threshold.currency}",
        f"Total of those deposits ({threshold.currency})",
        fmt(band_total),
        threshold.currency,
        rule,
    )
    out.add(
        "cash_below_threshold_days",
        "Distinct days with such deposits",
        str(len({t["value_date"] for t in band})),
        None,
        rule,
    )
    out.add(
        "threshold_amount",
        f"SYNTHETIC reporting threshold ({threshold.currency})",
        fmt(threshold.amount),
        threshold.currency,
        rule,
    )
    out.add(
        "window_days",
        "Investigation window length (days)",
        str((window_end - window_start).days),
        None,
        "window",
    )
    if expected_turnover and expected_turnover.get("currency"):
        ccy = str(expected_turnover["currency"])
        expected = Decimal(str(expected_turnover["amount"]))
        out.add(
            f"expected_monthly_turnover_{ccy}",
            f"Expected monthly turnover ({ccy})",
            fmt(expected),
            ccy,
            "profile",
        )
        observed = credits.totals.get(ccy)
        if observed is not None and expected > 0:
            ratio = (observed.amount / expected).quantize(CENT, rounding=ROUND_HALF_EVEN)
            out.add(
                f"credit_to_expected_ratio_{ccy}",
                f"Posted credits / expected turnover ({ccy})",
                str(ratio),
                None,
                "turnover-ratio",
            )
        other = sorted(c for c in credits.totals if c != ccy)
        if other:
            out.disclosures.append(
                f"Credits in {', '.join(other)} are not compared with the {ccy} expected turnover "
                "(no conversion rule)."
            )
    out.disclosures.append(
        f"Window semantics [start, end); threshold and band are SYNTHETIC ({RULE_VERSION})."
    )
    return out
