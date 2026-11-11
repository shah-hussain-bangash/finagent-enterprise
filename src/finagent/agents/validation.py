"""Deterministic validation of capability output and report narratives.

Model output is a proposal. Deterministic rules decide what is accepted.
Report sections must contain exact cited claims or trusted context text.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import cast

from finagent.agents.contracts import AgentOutputModel, Finding, ReportDraftModel

JsonDict = dict[str, object]

_NUMBER = re.compile(r"(?<![A-Za-z])\d[\d,]*(?:\.\d+)?")

_BOUNDARY = [
    (
        re.compile(
            r"\b(is|are|was|were) guilty\b"
            + r"|\bcommitted (a |an )?(crime|fraud|offen[cs]e|money laundering)\b"
            + r"|\bcriminal(ly)? liable\b|\b(is|was) laundering\b",
            re.I,
        ),
        "GUILT_DETERMINATION",
    ),
    (
        re.compile(
            r"\b(freeze|frozen|unfreeze)\b" + r"|\bblock(ed|ing)? the account\b|\bclose the account\b",
            re.I,
        ),
        "ACCOUNT_ACTION",
    ),
    (
        re.compile(
            r"\b(file|filed|submit|submitted)\b.{0,20}"
            + r"\b(SAR|STR|suspicious (activity|transaction) report)",
            re.I,
        ),
        "REGULATORY_FILING",
    ),
    (
        re.compile(
            r"\bsanctions? (match )?(is |are )?(confirmed|cleared)\b" + r"|\bconfirmed sanctions\b",
            re.I,
        ),
        "SANCTIONS_DECISION",
    ),
    (
        re.compile(
            r"\brisk (score|rating) (is |was |has been )?" + r"(changed|updated|raised|lowered|set)\b",
            re.I,
        ),
        "RISK_SCORE_CHANGE",
    ),
    (
        re.compile(
            r"\b(is|has been) approved\b|\bapproved by compliance\b"
            + r"|\bno (further )?review (is )?required\b"
            + r"|\bwithout (a |any )?review\b|\bapprove the case\b",
            re.I,
        ),
        "APPROVAL_CLAIM",
    ),
    (
        re.compile(
            r"\b(transfer|move|release) (the )?(funds|money)\b",
            re.I,
        ),
        "MONEY_MOVEMENT",
    ),
]

KNOWN_ABSTENTION_GAPS = frozenset({"NO_APPLICABLE_POLICY", "CONFLICTING_POLICY_VERSIONS"})


def numbers(text: str) -> set[Decimal]:
    out: set[Decimal] = set()

    for match in _NUMBER.finditer(text):
        try:
            out.add(Decimal(match.group(0).replace(",", "")).normalize())
        except InvalidOperation:
            continue

    return out


def _leaves(value: object) -> Iterable[str]:
    if isinstance(value, dict):
        for item in cast(JsonDict, value).values():
            yield from _leaves(item)
    elif isinstance(value, list):
        for item in cast(list[object], value):
            yield from _leaves(item)
    elif value is not None:
        yield str(value)


def numbers_in(value: object) -> set[Decimal]:
    out: set[Decimal] = set()

    for leaf in _leaves(value):
        out |= numbers(leaf)

    return out


def boundary_violations(texts: Iterable[str]) -> list[str]:
    found: list[str] = []

    for text in texts:
        for pattern, code in _BOUNDARY:
            if pattern.search(text):
                found.append(code)

    return sorted(set(found))


@dataclass
class ValidationContext:
    evidence: dict[str, JsonDict]
    calculations: dict[str, JsonDict]
    citations: dict[str, JsonDict]
    gap_codes: set[str]
    blocking_gaps: bool
    citation_check: Callable[[JsonDict, str], tuple[bool, list[str]]] | None = None
    extra_numbers: set[Decimal] = field(default_factory=set)


@dataclass
class ValidationReport:
    status: str
    accepted: list[JsonDict]
    rejected: list[JsonDict]
    boundary_violations: list[str]
    notes: list[str]

    @property
    def passed(self) -> bool:
        return not self.boundary_violations and self.status not in ("FAILED", "ESCALATED")

    def as_dict(self) -> JsonDict:
        return {
            "status": self.status,
            "passed": self.passed,
            "accepted_claims": [claim["claim_id"] for claim in self.accepted],
            "rejected": self.rejected,
            "boundary_violations": self.boundary_violations,
            "notes": self.notes,
            "validator": "deterministic-validator-2",
        }


def _check_finding(
    finding: Finding,
    ctx: ValidationContext,
) -> tuple[list[str], str]:
    reasons: list[str] = []
    support = finding.support_status

    if finding.claim_type in ("FACT", "DETERMINISTIC_RESULT", "INTERPRETATION") and not finding.evidence_refs:
        reasons.append("NO_EVIDENCE_REFERENCE")

    allowed: set[Decimal] = set()

    for ref in finding.evidence_refs:
        kind, _, key = ref.partition(":")

        if kind == "ev":
            if key not in ctx.evidence:
                reasons.append(f"UNKNOWN_EVIDENCE:{key[:40]}")
            else:
                allowed |= numbers_in(ctx.evidence[key])

        elif kind == "calc":
            calc = ctx.calculations.get(key)

            if calc is None:
                reasons.append(f"UNKNOWN_CALCULATION:{key[:40]}")
            else:
                allowed |= numbers(cast(str, calc["value"]))

        elif kind == "cite":
            citation = ctx.citations.get(key)

            if citation is None:
                reasons.append("CITATION_NOT_RETRIEVED_IN_THIS_RUN")
                continue

            allowed |= numbers(str(citation.get("excerpt", "")))
            allowed |= numbers_in({name: citation.get(name) for name in ("version_label", "section", "page")})

            if ctx.citation_check is not None:
                ok, why = ctx.citation_check(citation, finding.claim)

                if not ok:
                    reasons += why

        elif kind == "gap" and key not in ctx.gap_codes and key not in KNOWN_ABSTENTION_GAPS:
            reasons.append(f"UNKNOWN_GAP:{key[:40]}")

    if finding.claim_type == "DETERMINISTIC_RESULT":
        calc_values = [
            ctx.calculations[ref[5:]]["value"]
            for ref in finding.evidence_refs
            if ref.startswith("calc:") and ref[5:] in ctx.calculations
        ]

        if finding.value is None or finding.value not in calc_values:
            reasons.append("VALUE_NOT_EQUAL_TO_DETERMINISTIC_RESULT")

    if finding.claim_type != "GAP":
        stray = numbers(finding.claim) - allowed - ctx.extra_numbers

        if stray:
            reasons.append("NUMBER_NOT_IN_REFERENCED_SOURCES")

    if reasons:
        support = "UNSUPPORTED"

    return reasons, support


def validate_output(
    output: AgentOutputModel,
    ctx: ValidationContext,
) -> ValidationReport:
    texts = [finding.claim for finding in output.findings] + list(output.recommendations)
    violations = boundary_violations(texts)
    notes: list[str] = []

    if violations:
        return ValidationReport(
            "ESCALATED",
            [],
            [{"claim_id": "*", "reasons": violations}],
            violations,
            ["Output rejected: it asserted an action or determination " + "outside platform authority."],
        )

    accepted: list[JsonDict] = []
    rejected: list[JsonDict] = []

    for finding in output.findings:
        reasons, support = _check_finding(finding, ctx)

        if reasons:
            rejected.append(
                {
                    "claim_id": finding.claim_id,
                    "reasons": reasons,
                }
            )
        else:
            accepted.append(
                {
                    **cast(JsonDict, finding.model_dump()),
                    "support_status": support,
                }
            )

    status = output.status

    if status == "SUCCEEDED" and (ctx.blocking_gaps or rejected):
        status = "PARTIAL"
        notes.append("Status downgraded to PARTIAL: " + "blocking evidence gaps or rejected claims.")

    if not accepted and status in ("SUCCEEDED", "PARTIAL"):
        status = "FAILED"
        notes.append("No claim passed validation.")

    return ValidationReport(status, accepted, rejected, [], notes)


_REPORT_TITLES = {
    "alert_context": "Alert context",
    "evidence_scope": "Evidence scope",
    "relevant_activity": "Relevant activity",
    "calculations": "Validated calculations",
    "kyc_review": "KYC review",
    "policy_considerations": "Policy considerations",
    "gaps_and_limitations": "Gaps and limitations",
    "analyst_questions": "Analyst questions",
}


def validate_report(
    draft: ReportDraftModel,
    upstream: dict[str, JsonDict],
    allowed_numbers: set[Decimal],
    blocking: bool,
    *,
    trusted_sections: Mapping[str, str] | None = None,
    trusted_limitations: Mapping[str, Sequence[str]] | None = None,
) -> ValidationReport:
    """Accept exact upstream claims and deterministic section text only.

    Context sections and limitations must come from the runner.
    allowed_numbers remains for caller compatibility; global numeric
    membership does not establish support for a statement.
    """
    _ = allowed_numbers  # Retained for caller compatibility.
    texts = [finding.claim for finding in draft.findings]
    texts += [section.title for section in draft.sections]
    texts += [section.text for section in draft.sections]
    texts += list(draft.limitations.missing_evidence)
    texts += list(draft.limitations.contradictions)
    texts += list(draft.limitations.unsupported_claims)
    texts += list(draft.limitations.unresolved_questions)

    violations = boundary_violations(texts)

    if violations:
        return ValidationReport(
            "ESCALATED",
            [],
            [{"claim_id": "*", "reasons": violations}],
            violations,
            ["Report draft rejected: authority boundary."],
        )

    accepted: list[JsonDict] = []
    rejected: list[JsonDict] = []
    notes: list[str] = []
    seen_claims: set[str] = set()

    for finding in draft.findings:
        reasons: list[str] = []
        src = upstream.get(finding.claim_id)

        if finding.claim_id in seen_claims:
            reasons.append("DUPLICATE_REPORT_CLAIM")

        seen_claims.add(finding.claim_id)

        if src is None:
            reasons.append("NOT_AN_ACCEPTED_UPSTREAM_FINDING")
        else:
            if (
                src.get("claim_id") != finding.claim_id
                or src.get("claim") != finding.claim
                or src.get("claim_type") != finding.claim_type
                or src.get("evidence_refs") != list(finding.evidence_refs)
                or src.get("value") != finding.value
            ):
                reasons.append("NOT_AN_ACCEPTED_UPSTREAM_FINDING")

            if src.get("support_status") != finding.support_status:
                reasons.append(
                    "SUPPORT_STATUS_UPGRADED"
                    if finding.support_status == "SUPPORTED"
                    else "SUPPORT_STATUS_CHANGED"
                )

        if reasons:
            rejected.append(
                {
                    "claim_id": finding.claim_id,
                    "reasons": reasons,
                }
            )
        else:
            accepted.append(cast(JsonDict, finding.model_dump()))

    known = {cast(str, finding["claim_id"]): finding for finding in accepted}
    context = trusted_sections or {}
    seen_sections: set[str] = set()

    for section in draft.sections:
        reasons = []

        if section.section_id in seen_sections:
            reasons.append("DUPLICATE_REPORT_SECTION")

        seen_sections.add(section.section_id)

        if section.title != _REPORT_TITLES[section.section_id]:
            reasons.append("SECTION_TITLE_CHANGED")

        if len(section.claim_ids) != len(set(section.claim_ids)):
            reasons.append("DUPLICATE_SECTION_CLAIM")

        if section.section_id in (
            "alert_context",
            "evidence_scope",
            "analyst_questions",
        ):
            if section.claim_ids:
                reasons.append("CONTEXT_SECTION_HAS_CLAIM_IDS")

            expected = context.get(section.section_id)

            if expected is None or section.text != expected:
                reasons.append("SECTION_TEXT_NOT_IN_VALIDATED_INPUTS")

        elif set(section.claim_ids) - known.keys():
            reasons.append("SECTION_CITES_UNACCEPTED_CLAIM")

        elif not section.claim_ids:
            reasons.append("SECTION_HAS_NO_ACCEPTED_CLAIMS")

        else:
            expected = "\n".join(f"- {known[claim_id]['claim']}" for claim_id in section.claim_ids)

            if section.text != expected:
                reasons.append("SECTION_TEXT_NOT_IN_CITED_CLAIMS")

            local_numbers: set[Decimal] = set()

            for claim_id in section.claim_ids:
                local_numbers |= numbers(cast(str, known[claim_id]["claim"]))

            if numbers(section.text) - local_numbers:
                reasons.append("NUMBER_NOT_IN_VALIDATED_INPUTS")

        if reasons:
            rejected.append(
                {
                    "claim_id": f"section:{section.section_id}",
                    "reasons": reasons,
                }
            )

    limits = trusted_limitations or {}

    for name, values in (
        ("missing_evidence", draft.limitations.missing_evidence),
        ("contradictions", draft.limitations.contradictions),
        ("unsupported_claims", draft.limitations.unsupported_claims),
        ("unresolved_questions", draft.limitations.unresolved_questions),
    ):
        if set(values) - set(limits.get(name, ())):
            rejected.append(
                {
                    "claim_id": f"limitations:{name}",
                    "reasons": ["LIMITATION_NOT_IN_VALIDATED_INPUTS"],
                }
            )

    status = draft.status

    if rejected:
        status = "FAILED"
        notes.append("Report draft rejected: content differs from validated inputs.")
    elif status == "SUCCEEDED" and blocking:
        status = "PARTIAL"
        notes.append("Status downgraded to PARTIAL: blocking evidence gaps.")

    return ValidationReport(status, accepted, rejected, [], notes)
