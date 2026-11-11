"""Model provider adapters (prompt §11; AI-D03).

The model gateway is the only caller.

MockModelProvider is deterministic, SYNTHETIC and clearly labelled.
AzureOpenAIProvider is LIVE NOT VERIFIED.
"""

from __future__ import annotations

import json
import threading
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import NotRequired, Protocol, TypedDict, cast

import httpx

FAULT_MODES = frozenset(
    {
        "ok",
        "timeout",
        "rate_limited",
        "invalid_json",
        "schema_violation",
        "hallucinated_number",
        "invented_citation",
        "unknown_evidence",
        "guilt_claim",
        "injection_obeyed",
        "content_filtered",
    }
)


class ProviderError(Exception):
    kind: str
    transient: bool

    def __init__(self, kind: str, transient: bool) -> None:
        super().__init__(kind)
        self.kind = kind
        self.transient = transient


@dataclass(frozen=True)
class ProviderRequest:
    capability_id: str
    model: str
    system: str
    user: str
    output_schema: Mapping[str, object]
    timeout_s: float
    max_output_tokens: int


@dataclass(frozen=True)
class ProviderResponse:
    text: str
    input_tokens: int
    output_tokens: int
    model: str


class ModelProvider(Protocol):
    name: str

    def complete(self, request: ProviderRequest) -> ProviderResponse: ...


def _tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _gap(item: Mapping[str, object]) -> str:
    return f"gap:{item.get('code', 'UNSPECIFIED')}"


class _KycChecklistItem(TypedDict):
    item: str
    state: str
    detail: str
    refs: NotRequired[list[str]]


class _KycFacts(TypedDict):
    checklist: NotRequired[list[_KycChecklistItem]]


def _kyc(inp: Mapping[str, object]) -> dict[str, object]:
    facts = cast(_KycFacts, inp["facts"])
    findings: list[dict[str, object]] = []
    missing: list[str] = []
    contradictions: list[str] = []

    for i, item in enumerate(facts.get("checklist", []), start=1):
        state = item["state"]
        refs = list(item.get("refs", []))
        text = f"Checklist item {item['item']}: " + f"{state.lower().replace('_', ' ')} - {item['detail']}"

        if state == "PRESENT":
            findings.append(
                {
                    "claim_id": f"kyc-{i}",
                    "claim_type": "FACT",
                    "claim": text,
                    "support_status": "SUPPORTED",
                    "evidence_refs": refs,
                }
            )
        else:
            findings.append(
                {
                    "claim_id": f"kyc-{i}",
                    "claim_type": "GAP",
                    "claim": text,
                    "support_status": "NOT_APPLICABLE",
                    "evidence_refs": refs,
                }
            )

            if state == "CONFLICTING":
                contradictions.append(text)
            else:
                missing.append(text)

    return {
        "status": "SUCCEEDED" if not (missing or contradictions) else "PARTIAL",
        "findings": findings,
        "limitations": {
            "missing_evidence": missing,
            "contradictions": contradictions,
            "unsupported_claims": [],
            "unresolved_questions": [],
        },
        "recommendations": (
            ["Obtain current documents for every checklist item that is not PRESENT."] if missing else []
        ),
    }


def _money(calc: Mapping[str, object]) -> str:
    value = calc["value"]
    currency = calc.get("currency")

    if not isinstance(value, str):
        raise ValueError("calculation value must be a string")

    if currency:
        if not isinstance(currency, str):
            raise ValueError("calculation currency must be a string")
        return f"{currency} {value}"

    return value


class _AmlCalculation(TypedDict):
    label: str
    value: str
    currency: NotRequired[str | None]


class _AmlCalculations(TypedDict):
    values: NotRequired[dict[str, _AmlCalculation]]
    disclosures: NotRequired[list[str]]


class _AmlIndicator(TypedDict):
    indicator_id: str
    label: str
    observed: NotRequired[bool]
    calc_refs: NotRequired[list[str]]


class _AmlGap(TypedDict):
    code: str
    detail: str
    severity: NotRequired[str]
    source_type: NotRequired[str]


class _AmlFacts(TypedDict):
    calculations: NotRequired[_AmlCalculations]
    indicators: NotRequired[list[_AmlIndicator]]


class _AmlEvidence(TypedDict):
    known_gaps: NotRequired[list[_AmlGap]]


def _aml(inp: Mapping[str, object]) -> dict[str, object]:
    facts = cast(_AmlFacts, inp["facts"])
    evidence = cast(_AmlEvidence, inp["evidence"])
    calculations = facts.get("calculations", _AmlCalculations())
    calcs = calculations.get("values", {})
    findings: list[dict[str, object]] = []

    for cid in sorted(calcs):
        calculation = calcs[cid]

        if cid.startswith(
            (
                "posted_credit_total",
                "posted_debit_total",
                "cash_below_threshold",
                "credit_to_expected",
                "excluded_",
            )
        ):
            findings.append(
                {
                    "claim_id": f"aml-{cid}"[:64],
                    "claim_type": "DETERMINISTIC_RESULT",
                    "claim": f"{calculation['label']}: {_money(calculation)}.",
                    "support_status": "SUPPORTED",
                    "evidence_refs": [f"calc:{cid}"],
                    "value": calculation["value"],
                }
            )

    for indicator in facts.get("indicators", []):
        if indicator.get("observed"):
            findings.append(
                {
                    "claim_id": f"aml-ind-{indicator['indicator_id']}"[:64],
                    "claim_type": "INTERPRETATION",
                    "claim": (
                        f"{indicator['label']} was observed in the window. "
                        + "This is an indicator that requires analyst review; "
                        + "it is not a determination."
                    ),
                    "support_status": "SUPPORTED",
                    "evidence_refs": [f"calc:{ref}" for ref in indicator.get("calc_refs", [])],
                }
            )

    gaps = evidence.get("known_gaps", [])
    blocking = [gap for gap in gaps if gap.get("severity") == "BLOCKING"]

    for gap in gaps:
        if gap.get("source_type") in ("TRANSACTIONS", "CUSTOMER_PROFILE"):
            findings.append(
                {
                    "claim_id": f"aml-gap-{gap['code']}"[:64],
                    "claim_type": "GAP",
                    "claim": gap["detail"],
                    "support_status": "NOT_APPLICABLE",
                    "evidence_refs": [_gap(gap)],
                }
            )

    return {
        "status": "PARTIAL" if blocking else "SUCCEEDED",
        "findings": findings,
        "limitations": {
            "missing_evidence": [gap["detail"] for gap in blocking],
            "contradictions": [],
            "unsupported_claims": [],
            "unresolved_questions": calculations.get("disclosures", []),
        },
        "recommendations": [
            "Review transaction records by transaction ID, date, amount and type.",
            "Ask about transaction source and purpose only when supported by evidence.",
            "Record unresolved evidence gaps for analyst review.",
        ],
    }


class _PolicyCitation(TypedDict):
    policy_key: str
    version_label: str
    section: str
    heading: str
    excerpt: str
    chunk_id: str
    historical: NotRequired[bool]


class _ComplianceFacts(TypedDict):
    retrieval_outcome: NotRequired[str]
    citations: NotRequired[list[_PolicyCitation]]


def _compliance(inp: Mapping[str, object]) -> dict[str, object]:
    facts = cast(_ComplianceFacts, inp["facts"])

    if facts.get("retrieval_outcome") != "ANSWERED":
        code = (
            "CONFLICTING_POLICY_VERSIONS"
            if facts.get("retrieval_outcome") == "CONFLICTING_GUIDANCE"
            else "NO_APPLICABLE_POLICY"
        )

        return {
            "status": ("ESCALATED" if code == "CONFLICTING_POLICY_VERSIONS" else "INSUFFICIENT_EVIDENCE"),
            "findings": [
                {
                    "claim_id": "cmp-no-policy",
                    "claim_type": "GAP",
                    "claim": (
                        "No single approved, applicable policy passage supports "
                        + "a policy statement for this case; "
                        + "policy-owner review is requested."
                    ),
                    "support_status": "NOT_APPLICABLE",
                    "evidence_refs": [f"gap:{code}"],
                }
            ],
            "limitations": {
                "missing_evidence": ["approved applicable policy"],
                "contradictions": [],
                "unsupported_claims": [],
                "unresolved_questions": [],
            },
            "recommendations": ["Request policy-owner review (Policy Administrator)."],
        }

    findings: list[dict[str, object]] = []

    for i, citation in enumerate(facts.get("citations", [])[:3], start=1):
        label = " (historical guidance)" if citation.get("historical") else ""
        where = (
            f"Policy {citation['policy_key']} "
            + f"version {citation['version_label']} "
            + f"section {citation['section']}"
        )

        findings.append(
            {
                "claim_id": f"cmp-{i}",
                "claim_type": "INTERPRETATION",
                "claim": (f"{where} ({citation['heading']})" + f"{label}: {citation['excerpt']}")[:1200],
                "support_status": "SUPPORTED",
                "evidence_refs": [f"cite:{citation['chunk_id']}"],
            }
        )

    return {
        "status": "SUCCEEDED",
        "findings": findings,
        "limitations": {
            "missing_evidence": [],
            "contradictions": [],
            "unsupported_claims": [],
            "unresolved_questions": [],
        },
        "recommendations": [
            "Reviewer: confirm the cited policy versions apply to this alert family and date."
        ],
    }


class _DocumentField(TypedDict):
    field_key: str
    value_state: str
    value: object
    validation_status: str
    page: NotRequired[int | None]
    row: NotRequired[int | None]
    origin: NotRequired[str]


class _DocumentItem(TypedDict):
    document_type: str
    evidence_ref: str
    fields: NotRequired[list[_DocumentField]]


class _DocumentFacts(TypedDict):
    documents: NotRequired[list[_DocumentItem]]


def _document(inp: Mapping[str, object]) -> dict[str, object]:
    facts = cast(_DocumentFacts, inp["facts"])
    findings: list[dict[str, object]] = []
    missing: list[str] = []

    for document in facts.get("documents", []):
        document_type = document["document_type"]

        for item in document.get("fields", []):
            page = item.get("page")
            row = item.get("row")

            if page:
                provenance = f"page {page}"
            elif row:
                provenance = f"row {row}"
            else:
                provenance = "document"

            claim_id = (f"doc-{document_type[:8]}-{item['field_key']}")[:64].replace(" ", "_")

            if item.get("value_state") == "PRESENT":
                origin = "corrected by an analyst" if item.get("origin") == "CORRECTED" else "extracted"
                validation_status = item["validation_status"]
                check = validation_status.lower()
                text = (
                    f"{document_type} {item['field_key']} "
                    + f"{origin} as {item['value']} "
                    + f"({provenance}; {check})."
                )
                claim_type = "FACT"
                support_status = "SUPPORTED" if validation_status in ("VALID", "UNVERIFIED") else "PARTIAL"
            else:
                state = item["value_state"].lower().replace("_", " ")
                text = f"{document_type} {item['field_key']} is {state}."
                missing.append(text)
                claim_type = "GAP"
                support_status = "NOT_APPLICABLE"

            findings.append(
                {
                    "claim_id": claim_id,
                    "claim_type": claim_type,
                    "claim": text,
                    "support_status": support_status,
                    "evidence_refs": [document["evidence_ref"]],
                }
            )

    return {
        "status": "PARTIAL" if missing else "SUCCEEDED",
        "findings": findings[:60],
        "limitations": {
            "missing_evidence": missing[:50],
            "contradictions": [],
            "unsupported_claims": [],
            "unresolved_questions": [],
        },
        "recommendations": [],
    }


_SECTION_TITLES = {
    "alert_context": "Alert context",
    "evidence_scope": "Evidence scope",
    "relevant_activity": "Relevant activity",
    "calculations": "Validated calculations",
    "kyc_review": "KYC review",
    "policy_considerations": "Policy considerations",
    "gaps_and_limitations": "Gaps and limitations",
    "analyst_questions": "Analyst questions",
}


class _AcceptedFinding(TypedDict):
    capability_id: str
    claim_id: str
    claim_type: str
    claim: str
    support_status: str
    evidence_refs: list[str]
    value: NotRequired[object]


class _ReportCase(TypedDict):
    source_alert_id: NotRequired[object]
    alert_type: NotRequired[object]
    severity: NotRequired[object]
    subject_refs: NotRequired[list[str]]
    window_start: NotRequired[object]
    window_end: NotRequired[object]


class _ReportFacts(TypedDict):
    accepted_findings: NotRequired[list[_AcceptedFinding]]
    case: NotRequired[_ReportCase]
    snapshot_version: NotRequired[object]
    snapshot_completeness: NotRequired[object]
    sources: NotRequired[list[str]]
    recommendations: NotRequired[list[str]]
    upstream_statuses: NotRequired[list[str]]
    missing_evidence: NotRequired[list[str]]


def _report(inp: Mapping[str, object]) -> dict[str, object]:
    facts = cast(_ReportFacts, inp["facts"])
    accepted = facts.get("accepted_findings", [])
    by_section: dict[str, list[_AcceptedFinding]] = {key: [] for key in _SECTION_TITLES}

    for finding in accepted:
        source = finding["capability_id"]

        if finding["claim_type"] == "GAP":
            by_section["gaps_and_limitations"].append(finding)
        elif finding["claim_type"] == "DETERMINISTIC_RESULT":
            by_section["calculations"].append(finding)
        elif source in ("AG-KYC", "CAP-DOCUMENT"):
            by_section["kyc_review"].append(finding)
        elif source == "AG-COMPLIANCE":
            by_section["policy_considerations"].append(finding)
        else:
            by_section["relevant_activity"].append(finding)

    case = facts.get("case", _ReportCase())
    subjects = ", ".join(case.get("subject_refs", []))
    sources = ", ".join(facts.get("sources", []))

    sections: list[dict[str, object]] = [
        {
            "section_id": "alert_context",
            "title": _SECTION_TITLES["alert_context"],
            "text": (
                f"Alert {case.get('source_alert_id')} "
                + f"({case.get('alert_type')}, "
                + f"severity {case.get('severity')}) "
                + f"for subject(s) {subjects}; "
                + f"window {case.get('window_start')} "
                + f"to {case.get('window_end')}."
            ),
            "claim_ids": [],
        },
        {
            "section_id": "evidence_scope",
            "title": _SECTION_TITLES["evidence_scope"],
            "text": (
                f"Evidence snapshot version {facts.get('snapshot_version')} "
                + f"({facts.get('snapshot_completeness')}); "
                + f"sources: {sources}."
            ),
            "claim_ids": [],
        },
    ]

    for section_id, items in by_section.items():
        if not items:
            continue

        sections.append(
            {
                "section_id": section_id,
                "title": _SECTION_TITLES[section_id],
                "text": "\n".join(f"- {finding['claim']}" for finding in items),
                "claim_ids": [finding["claim_id"] for finding in items],
            }
        )

    questions = facts.get("recommendations", [])

    if questions:
        sections.append(
            {
                "section_id": "analyst_questions",
                "title": _SECTION_TITLES["analyst_questions"],
                "text": "\n".join(f"- {question}" for question in questions),
                "claim_ids": [],
            }
        )

    upstream = set(facts.get("upstream_statuses", []))
    status = "SUCCEEDED" if upstream <= {"SUCCEEDED"} else "PARTIAL"

    finding_keys = {
        "claim_id",
        "claim_type",
        "claim",
        "support_status",
        "evidence_refs",
        "value",
    }
    report_findings: list[dict[str, object]] = [
        {key: value for key, value in finding.items() if key in finding_keys} for finding in accepted
    ][:80]

    return {
        "status": status,
        "sections": sections[:8],
        "findings": report_findings,
        "limitations": {
            "missing_evidence": facts.get("missing_evidence", [])[:50],
            "contradictions": [],
            "unsupported_claims": [],
            "unresolved_questions": [],
        },
    }


RESPONDERS: dict[str, Callable[[Mapping[str, object]], dict[str, object]]] = {
    "AG-KYC": _kyc,
    "AG-AML": _aml,
    "AG-COMPLIANCE": _compliance,
    "AG-REPORT": _report,
    "CAP-DOCUMENT": _document,
}


@dataclass
class MockModelProvider:
    """Deterministic SYNTHETIC provider. Script entries are consumed per call."""

    script: list[str] = field(default_factory=list)
    name: str = "mock-model-provider"
    model: str = "mock-finagent-1 (SYNTHETIC)"
    calls: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        with self._lock:
            mode = self.script.pop(0) if self.script else "ok"
            self.calls += 1

        if mode not in FAULT_MODES:
            raise ValueError(f"unknown mock mode {mode}")

        if mode == "timeout":
            raise ProviderError("TIMEOUT", transient=True)

        if mode == "rate_limited":
            raise ProviderError("RATE_LIMITED", transient=True)

        if mode == "content_filtered":
            raise ProviderError("CONTENT_FILTERED", transient=False)

        inp = cast(
            dict[str, object],
            json.loads(request.user.split("\n<repair>")[0]),
        )

        if mode == "invalid_json":
            text = '{"status": "SUCCEEDED", "findings": ['
        else:
            out = RESPONDERS[request.capability_id](inp)
            out = _apply_fault(mode, out)
            text = json.dumps(out)

        return ProviderResponse(
            text,
            _tokens(request.system + request.user),
            _tokens(text),
            self.model,
        )


class _FaultFinding(TypedDict):
    claim_id: str
    claim_type: str
    claim: str
    support_status: str
    evidence_refs: list[str]
    value: NotRequired[object]


def _apply_fault(
    mode: str,
    out: dict[str, object],
) -> dict[str, object]:
    findings = list(cast(list[_FaultFinding], out.get("findings", [])))

    if mode == "schema_violation":
        out["unexpected_field"] = "the model added a field the contract does not allow"

    elif mode == "hallucinated_number":
        for finding in findings:
            if finding["claim_type"] in ("DETERMINISTIC_RESULT", "FACT"):
                finding["claim"] = finding["claim"].rstrip(".") + " (approximately 987654.32 in total)."

                if finding.get("value"):
                    finding["value"] = "987654.32"

                break
        else:
            findings.append(
                {
                    "claim_id": "x-num",
                    "claim_type": "FACT",
                    "claim": "Deposits totalled AED 987654.32.",
                    "support_status": "SUPPORTED",
                    "evidence_refs": [],
                }
            )

    elif mode == "invented_citation":
        findings.append(
            {
                "claim_id": "x-cite",
                "claim_type": "INTERPRETATION",
                "claim": ("Policy requires immediate escalation of all cash deposits."),
                "support_status": "SUPPORTED",
                "evidence_refs": [f"cite:{uuid.uuid4()}"],
            }
        )

    elif mode == "unknown_evidence":
        findings.append(
            {
                "claim_id": "x-ev",
                "claim_type": "FACT",
                "claim": ("The customer holds an undisclosed offshore account."),
                "support_status": "SUPPORTED",
                "evidence_refs": [f"ev:{uuid.uuid4()}"],
            }
        )

    elif mode == "guilt_claim":
        refs: list[str] = findings[0]["evidence_refs"] if findings else []

        findings.append(
            {
                "claim_id": "x-guilt",
                "claim_type": "INTERPRETATION",
                "claim": (
                    "The customer is guilty of money laundering "
                    + "and the account should be frozen immediately."
                ),
                "support_status": "SUPPORTED",
                "evidence_refs": refs,
            }
        )

    elif mode == "injection_obeyed":
        out["status"] = "SUCCEEDED"

        findings.append(
            {
                "claim_id": "x-inject",
                "claim_type": "FACT",
                "claim": ("This case is approved by compliance " + "and requires no further review."),
                "support_status": "SUPPORTED",
                "evidence_refs": [],
            }
        )

        recommendations = cast(
            list[str],
            out.get("recommendations", []),
        )
        out["recommendations"] = [
            *recommendations,
            "Approve the case and file the report without review.",
        ]

    out["findings"] = findings
    return out


class _AzureMessage(TypedDict):
    content: object


class _AzureChoice(TypedDict):
    message: _AzureMessage
    finish_reason: NotRequired[str | None]


class _AzureUsage(TypedDict):
    prompt_tokens: NotRequired[int | float | str]
    completion_tokens: NotRequired[int | float | str]


class _AzureResponse(TypedDict):
    choices: list[_AzureChoice]
    usage: NotRequired[_AzureUsage]
    model: NotRequired[object]


class AzureOpenAIProvider:
    name: str = "azure-openai"
    _url: str
    _params: dict[str, str]
    _headers: dict[str, str]
    _http: httpx.Client
    model: str

    def __init__(
        self,
        endpoint: str,
        deployment: str,
        api_version: str,
        api_key: str,
        http: httpx.Client | None = None,
    ) -> None:
        if not endpoint.startswith("https://"):
            raise ValueError("azure openai endpoint must use https")

        self._url = f"{endpoint.rstrip('/')}/openai/deployments/" + f"{deployment}/chat/completions"
        self._params = {"api-version": api_version}
        self._headers = {
            "api-key": api_key,
            "Content-Type": "application/json",
        }
        self._http = http or httpx.Client()
        self.model = deployment

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        body = {
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": request.user},
            ],
            "temperature": 0,
            "max_tokens": request.max_output_tokens,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "agent_output",
                    "schema": request.output_schema,
                    "strict": False,
                },
            },
        }

        try:
            response = self._http.post(
                self._url,
                params=self._params,
                headers=self._headers,
                json=body,
                timeout=request.timeout_s,
            )
        except httpx.TimeoutException as exc:
            raise ProviderError("TIMEOUT", transient=True) from exc
        except httpx.TransportError as exc:
            raise ProviderError("UNAVAILABLE", transient=True) from exc

        if response.status_code == 429:
            raise ProviderError("RATE_LIMITED", transient=True)

        if response.status_code >= 500:
            raise ProviderError("UNAVAILABLE", transient=True)

        if response.status_code in (401, 403):
            raise ProviderError("AUTH", transient=False)

        if response.status_code >= 400:
            filtered = "content_filter" in response.text
            raise ProviderError(
                "CONTENT_FILTERED" if filtered else "BAD_REQUEST",
                transient=False,
            )

        try:
            data = cast(_AzureResponse, response.json())
            choice = data["choices"][0]

            if choice.get("finish_reason") == "content_filter":
                raise ProviderError(
                    "CONTENT_FILTERED",
                    transient=False,
                )

            text = str(choice["message"]["content"])
            usage = data.get("usage", _AzureUsage())
        except (KeyError, IndexError, ValueError) as exc:
            raise ProviderError(
                "BAD_RESPONSE",
                transient=False,
            ) from exc

        return ProviderResponse(
            text,
            int(
                usage.get(
                    "prompt_tokens",
                    _tokens(request.system + request.user),
                )
            ),
            int(
                usage.get(
                    "completion_tokens",
                    _tokens(text),
                )
            ),
            str(data.get("model", self.model)),
        )
