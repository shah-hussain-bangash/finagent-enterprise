"""Deterministic preparation of capability inputs from an immutable evidence snapshot.

Everything here is application logic, not a model: the KYC checklist evaluation (AER-006/007), the
AML calculations and indicators (AER-009), document summaries (AER-017) and context minimization per
capability (prompt §11). Checklist and indicator rules are SYNTHETIC v0 (AI-D02 OPEN).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from finagent.core.config import Settings
from finagent.database.models import Alert, Case, EvidenceConflict, EvidenceItem, EvidenceSnapshot
from finagent.domain.aml_calculations import CalcSet, Threshold, compute
from finagent.services.evidence_service.items import evidence_content
from finagent.services.evidence_service.snapshots import snapshot_items

CHECKLIST_VERSION = "kyc-checklist-0.1-SYNTHETIC"
KYC_SELECTION_VERSION = "kyc-document-selection-0.1-SYNTHETIC"
INDICATOR_VERSION = "aml-indicators-0.1-SYNTHETIC"
POA_MAX_AGE_DAYS = 90  # SYNTHETIC policy pol-kyc-refresh s3; the approved rule is AI-D02
KYC_REVIEW_MAX_DAYS = 365


@dataclass
class CaseFacts:
    tenant_id: uuid.UUID
    case: Case
    alert: Alert
    snapshot: EvidenceSnapshot
    items: list[EvidenceItem]
    calcs: CalcSet
    checklist: list[dict[str, Any]] = field(default_factory=list)
    indicators: list[dict[str, Any]] = field(default_factory=list)
    documents: list[dict[str, Any]] = field(default_factory=list)
    checklist_numbers: set[Decimal] = field(default_factory=set)
    document_selection: dict[str, Any] | None = None

    def ref(self, item: EvidenceItem) -> str:
        return f"ev:{item.id}"

    @property
    def evidence_map(self) -> dict[str, dict[str, Any]]:
        return {str(i.id): evidence_content(i) for i in self.items}

    @property
    def gap_codes(self) -> set[str]:
        return {g["code"] for g in self.snapshot.gaps}

    @property
    def blocking(self) -> bool:
        return (
            any(g["severity"] == "BLOCKING" for g in self.snapshot.gaps)
            or self.snapshot.completeness_status == "INCOMPLETE"
        )


def _first(items: list[EvidenceItem], source_type: str, ref: str | None = None) -> EvidenceItem | None:
    return next(
        (i for i in items if i.source_type == source_type and (ref is None or i.source_ref == ref)), None
    )


def _parse_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _doc_field(doc: EvidenceItem, key: str) -> dict[str, Any] | None:
    return next(
        (
            f
            for f in evidence_content(doc).get("fields", [])
            if f.get("field_key") == key and f.get("value_state") == "PRESENT"
        ),
        None,
    )


def _select_document(
    docs: list[EvidenceItem], kind: str, subject: str | None, *, single_subject: bool, today: date
) -> EvidenceItem | None:
    """Choose usable evidence, never whichever UUID happens to sort first.

    Unscoped case uploads can be considered only for a single-subject case. All
    documents remain in the snapshot and capability inputs, including failures;
    this selection does not resolve conflicts or verify a document's authenticity.
    """
    key = "expiry_date" if kind == "PASSPORT" else "issue_date"

    def rank(doc: EvidenceItem) -> tuple[bool, bool, int, str, str]:
        content = evidence_content(doc)
        parsed = content.get("status") == "PARSED"
        field = _doc_field(doc, key) if parsed else None
        value = _parse_date(field.get("value")) if field else None
        valid = bool(field and field.get("validation_status") == "VALID" and value is not None)
        if kind == "PROOF_OF_ADDRESS" and value is not None and value > today:
            valid = False
        return (
            parsed,
            valid,
            value.toordinal() if valid and value is not None else 0,
            doc.source_ref,
            str(doc.id),
        )

    candidates = []
    for doc in docs:
        content = evidence_content(doc)
        owner = content.get("customer_ref")
        if content.get("document_type") != kind:
            continue
        if subject is None or (owner != subject and not (owner is None and single_subject)):
            continue
        candidates.append(doc)
    return max(candidates, key=rank, default=None)


def _checklist(facts: CaseFacts, conflicts: list[EvidenceConflict], today: date) -> list[dict[str, Any]]:
    items, out = facts.items, []
    subject = facts.alert.subject_refs[0] if facts.alert.subject_refs else None
    profile = _first(items, "CUSTOMER_PROFILE", subject)
    kyc_list = _first(items, "KYC_DOCUMENT_LIST", subject)
    docs = [i for i in items if i.source_type == "DOCUMENT_EXTRACTION"]

    def doc_of(kind: str) -> EvidenceItem | None:
        return _select_document(
            docs, kind, subject, single_subject=len(facts.alert.subject_refs) == 1, today=today
        )

    def add(item: str, state: str, detail: str, refs: list[str]) -> None:
        out.append({"item": item, "state": state, "detail": detail, "refs": refs})

    passport = doc_of("PASSPORT")
    if passport is None:
        add("IDENTITY_DOCUMENT", "MISSING", "no identity document in the snapshot", [])
    elif evidence_content(passport).get("status") != "PARSED":
        add(
            "IDENTITY_DOCUMENT",
            "UNVERIFIED",
            f"identity document not readable ({evidence_content(passport).get('status')})",
            [facts.ref(passport)],
        )
    else:
        expiry = _doc_field(passport, "expiry_date")
        exp_date = _parse_date(expiry["value"]) if expiry else None
        if exp_date is None:
            add(
                "IDENTITY_DOCUMENT",
                "UNVERIFIED",
                "identity document expiry date not extracted",
                [facts.ref(passport)],
            )
        elif exp_date < today:
            add(
                "IDENTITY_DOCUMENT",
                "MISSING",
                f"identity document expired on {exp_date.isoformat()}",
                [facts.ref(passport)],
            )
        else:
            add(
                "IDENTITY_DOCUMENT",
                "PRESENT",
                f"identity document valid until {exp_date.isoformat()}",
                [facts.ref(passport)],
            )
    poa = doc_of("PROOF_OF_ADDRESS")
    # Record precisely the candidates used for this checklist. An absent candidate and an
    # unreadable selected candidate remain distinct; selection does not establish validity.
    facts.document_selection = {
        "rule_version": KYC_SELECTION_VERSION,
        "review_date": today.isoformat(),
        "basis": "Subject-scoped candidates ranked by parsed status, valid date and deterministic "
        "tie-breakers.",
        "items": [
            {
                "document_type": kind,
                "selection": "SELECTED" if doc is not None else "NO_CANDIDATE",
                "evidence_ref": facts.ref(doc) if doc is not None else None,
                "source_ref": doc.source_ref if doc is not None else None,
                "processing_status": evidence_content(doc).get("status") if doc is not None else None,
            }
            for kind, doc in (("PASSPORT", passport), ("PROOF_OF_ADDRESS", poa))
        ],
    }
    source_poa = next(
        (
            d
            for d in (evidence_content(kyc_list).get("documents", []) if kyc_list else [])
            if d.get("document_type") == "PROOF_OF_ADDRESS"
        ),
        None,
    )
    refs = [facts.ref(x) for x in (poa, kyc_list) if x is not None]
    issue_field = _doc_field(poa, "issue_date") if poa is not None else None
    issued = _parse_date(issue_field["value"]) if issue_field else None
    if poa is None and source_poa is None:
        add("PROOF_OF_ADDRESS", "MISSING", "no proof of address", refs)
    elif source_poa is not None and source_poa.get("status") not in ("VERIFIED", "CURRENT"):
        add(
            "PROOF_OF_ADDRESS",
            "MISSING",
            f"source status {source_poa.get('status')}; a current proof of address is needed",
            refs,
        )
    elif (
        poa is None
        or issued is None
        or evidence_content(poa).get("status") != "PARSED"
        or issue_field is None
        or issue_field.get("validation_status") != "VALID"
    ):
        add("PROOF_OF_ADDRESS", "UNVERIFIED", "issue date not extracted", refs)
    elif issued > today:
        add("PROOF_OF_ADDRESS", "UNVERIFIED", "issue date is after the review date", refs)
    else:
        age = (today - issued).days
        facts.checklist_numbers.add(Decimal(age))
        state = "PRESENT" if age <= POA_MAX_AGE_DAYS else "MISSING"
        add(
            "PROOF_OF_ADDRESS",
            state,
            f"issued {issued.isoformat()} ({age} days before the review date)",
            refs,
        )
    open_c = [c for c in conflicts if c.status == "OPEN"]
    resolved = [c for c in conflicts if c.status == "RESOLVED"]
    if open_c:
        add(
            "IDENTITY_DATA_CONSISTENCY",
            "CONFLICTING",
            "differing values between sources for: " + ", ".join(sorted({c.field_key for c in open_c})),
            sorted({f"ev:{c.item_a_id}" for c in open_c} | {f"ev:{c.item_b_id}" for c in open_c}),
        )
    elif resolved:
        add(
            "IDENTITY_DATA_CONSISTENCY",
            "PRESENT",
            "differences were resolved by an analyst with a recorded reason",
            sorted({f"ev:{c.item_a_id}" for c in resolved} | {f"ev:{c.item_b_id}" for c in resolved}),
        )
    elif passport is not None and profile is not None:
        add(
            "IDENTITY_DATA_CONSISTENCY",
            "PRESENT",
            "no differing values detected between profile and documents",
            [facts.ref(profile), facts.ref(passport)],
        )
    else:
        add("IDENTITY_DATA_CONSISTENCY", "UNVERIFIED", "not enough sources to compare", [])
    p = evidence_content(profile).get("profile", {}) if profile else {}
    last = _parse_date(p.get("kyc_last_review"))
    if profile is None:
        add("KYC_REVIEW_CURRENT", "MISSING", "customer profile not retrieved", [])
    elif p.get("kyc_status") == "CURRENT" and last is not None and (today - last).days <= KYC_REVIEW_MAX_DAYS:
        add(
            "KYC_REVIEW_CURRENT",
            "PRESENT",
            f"KYC status CURRENT, last review {last.isoformat()}",
            [facts.ref(profile)],
        )
    else:
        add(
            "KYC_REVIEW_CURRENT",
            "MISSING",
            f"KYC status {p.get('kyc_status')}, last review {p.get('kyc_last_review')}",
            [facts.ref(profile)],
        )
    sanctions = _first(items, "SANCTIONS_SUMMARY", subject)
    s_status = (evidence_content(sanctions).get("summary", {}) if sanctions else {}).get("screening_status")
    if sanctions is None:
        add("SANCTIONS_SCREENING", "MISSING", "screening summary not retrieved", [])
    elif s_status == "NO_MATCH":
        add("SANCTIONS_SCREENING", "PRESENT", "screening summary reports no match", [facts.ref(sanctions)])
    else:
        add(
            "SANCTIONS_SCREENING",
            "UNVERIFIED",
            f"screening status {s_status}; resolution is outside platform authority",
            [facts.ref(sanctions)],
        )
    tax = p.get("tax_residency", {}) if profile else {}
    if tax.get("state") == "PRESENT":
        add(
            "TAX_RESIDENCY",
            "PRESENT",
            "tax residency recorded at source",
            [facts.ref(profile)] if profile else [],
        )
    else:
        add(
            "TAX_RESIDENCY",
            "UNVERIFIED",
            f"tax residency {str(tax.get('state', 'UNKNOWN')).lower()} at source",
            [facts.ref(profile)] if profile else [],
        )
    return out


def _indicators(calcs: CalcSet, threshold: Threshold, settings: Settings) -> list[dict[str, Any]]:
    v = calcs.values
    out = []
    count = int(v["cash_below_threshold_count"].value) if "cash_below_threshold_count" in v else 0
    days = int(v["cash_below_threshold_days"].value) if "cash_below_threshold_days" in v else 0
    out.append(
        {
            "indicator_id": "STRUCTURING_PATTERN",
            "label": "Repeated cash deposits just below the SYNTHETIC reporting threshold",
            "observed": count >= 3 and days >= 3,
            "calc_refs": [
                "cash_below_threshold_count",
                f"cash_below_threshold_total_{threshold.currency}",
                "cash_below_threshold_days",
            ],
        }
    )
    ratio = v.get(f"credit_to_expected_ratio_{threshold.currency}")
    out.append(
        {
            "indicator_id": "TURNOVER_ABOVE_EXPECTED",
            "label": "Posted credits well above the expected monthly turnover",
            "observed": ratio is not None and Decimal(ratio.value) > Decimal("1.5"),
            "calc_refs": [f"credit_to_expected_ratio_{threshold.currency}"] if ratio else [],
        }
    )
    largest = v.get(f"largest_posted_debit_{threshold.currency}")
    out.append(
        {
            "indicator_id": "LARGE_OUTGOING_TRANSFER",
            "label": "A large outgoing transfer after the deposits",
            "observed": largest is not None and Decimal(largest.value) >= threshold.amount,
            "calc_refs": [f"largest_posted_debit_{threshold.currency}"] if largest else [],
        }
    )
    return out


def threshold_of(settings: Settings) -> Threshold:
    return Threshold(
        Decimal(settings.aml_threshold_amount),
        settings.aml_threshold_currency,
        Decimal(settings.aml_threshold_band_pct),
    )


def prepare(
    session: Session, tenant_id: uuid.UUID, case_id: uuid.UUID, snapshot_id: uuid.UUID, settings: Settings
) -> CaseFacts:
    row = session.execute(
        select(Case, Alert)
        .join(Alert, and_(Alert.tenant_id == Case.tenant_id, Alert.id == Case.alert_id))
        .where(Case.tenant_id == tenant_id, Case.id == case_id)
    ).one()
    case, alert = row
    snapshot = session.scalar(
        select(EvidenceSnapshot).where(
            EvidenceSnapshot.tenant_id == tenant_id,
            EvidenceSnapshot.case_id == case_id,
            EvidenceSnapshot.id == snapshot_id,
        )
    )
    if snapshot is None:
        raise ValueError("snapshot does not belong to this case")
    items = snapshot_items(session, tenant_id, snapshot_id)
    subject = alert.subject_refs[0] if alert.subject_refs else None
    txn = _first(items, "TRANSACTIONS", subject)
    profile = _first(items, "CUSTOMER_PROFILE", subject)
    threshold = threshold_of(settings)
    calcs = compute(
        list(evidence_content(txn).get("items", [])) if txn else [],
        threshold=threshold,
        window_start=alert.window_start,
        window_end=alert.window_end,
        expected_turnover=(
            evidence_content(profile).get("profile", {}).get("expected_monthly_turnover") if profile else None
        ),
    )
    if txn is None:
        calcs.disclosures.append("No transaction evidence in the snapshot: totals are not computed.")
    facts = CaseFacts(tenant_id, case, alert, snapshot, items, calcs)
    conflicts = list(
        session.scalars(
            select(EvidenceConflict).where(
                EvidenceConflict.tenant_id == tenant_id,
                EvidenceConflict.case_id == case_id,
                EvidenceConflict.status != "SUPERSEDED",
            )
        )
    )
    facts.checklist = _checklist(facts, conflicts, datetime.now(UTC).date())
    facts.indicators = _indicators(calcs, threshold, settings) if txn else []
    facts.documents = [
        {
            "evidence_ref": facts.ref(d),
            "document_type": evidence_content(d).get("document_type"),
            "status": evidence_content(d).get("status"),
            "fields": [
                {
                    k: f.get(k)
                    for k in (
                        "field_key",
                        "value",
                        "value_state",
                        "validation_status",
                        "page",
                        "row",
                        "origin",
                    )
                }
                for f in evidence_content(d).get("fields", [])
                if not str(f.get("field_key", "")).startswith(
                    "transaction."
                )  # statement rows stay in evidence
            ][:30],
        }
        for d in items
        if d.source_type == "DOCUMENT_EXTRACTION"
    ]
    return facts


def minimized_facts(
    capability_id: str, facts: CaseFacts, extra: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Context minimization (prompt §11): each capability sees only what its task needs."""
    if capability_id == "AG-KYC":
        return {
            "checklist": facts.checklist,
            "checklist_version": CHECKLIST_VERSION,
            "review_date": datetime.now(UTC).date().isoformat(),
        }
    if capability_id == "AG-AML":
        subject = facts.alert.subject_refs[0] if facts.alert.subject_refs else None
        profile = _first(facts.items, "CUSTOMER_PROFILE", subject)
        p = evidence_content(profile).get("profile", {}) if profile else {}
        return {
            "calculations": facts.calcs.as_dict(),
            "indicators": facts.indicators,
            "indicator_version": INDICATOR_VERSION,
            # minimized profile: no name, date of birth, nationality or identifiers
            "customer": {k: p.get(k) for k in ("customer_type", "occupation", "expected_monthly_turnover")},
        }
    if capability_id == "CAP-DOCUMENT":
        return {"documents": facts.documents}
    return dict(extra or {})


def evidence_refs_for(
    capability_id: str, facts: CaseFacts, permitted: frozenset[str]
) -> list[dict[str, Any]]:
    return [
        {
            "ref": facts.ref(i),
            "source_type": i.source_type,
            "source_ref": i.source_ref,
            "version_no": i.version_no,
            "content_hash": i.content_hash,
        }
        for i in facts.items
        if not permitted or i.source_type in permitted
    ]
