"""Common agent contract (prompt §10; AER-003, AER-005, AER-026).

Input and output are validated with these models. A finding is one claim with a type, a support
status and references; references use explicit prefixes so validators can resolve them:

  ev:<evidence_item_id>      an item of the bound evidence snapshot
  calc:<calc_id>             a deterministic calculation result
  cite:<chunk_id>            a policy passage returned by this run's retrieval
  gap:<code>                 a known evidence gap
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

ClaimType = Literal["FACT", "DETERMINISTIC_RESULT", "INTERPRETATION", "GAP"]
SupportStatus = Literal["SUPPORTED", "PARTIAL", "UNSUPPORTED", "NOT_APPLICABLE"]
OutputStatus = Literal["SUCCEEDED", "PARTIAL", "INSUFFICIENT_EVIDENCE", "ESCALATED", "FAILED"]
Ref = Annotated[str, StringConstraints(pattern=r"^(ev|calc|cite|gap):[A-Za-z0-9_.:-]{1,120}$")]
ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1200)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ExecutionContextRef(_Strict):
    run_id: str | None
    step_id: str
    case_id: str
    tenant_ref: str  # opaque reference only; authority never comes from here (AER-003)
    capability_id: str
    capability_version: str
    correlation_id: str


class EvidenceRef(_Strict):
    ref: Ref
    source_type: str
    source_ref: str
    version_no: int
    content_hash: str


class EvidenceBundle(_Strict):
    snapshot_id: str
    completeness_status: str
    evidence_refs: list[EvidenceRef]
    known_gaps: list[dict[str, Any]]


class Limits(_Strict):
    max_steps: int
    token_budget: int
    cost_budget: str  # Decimal as string
    deadline_s: float
    cancellation_requested: bool = False


class AgentInput(_Strict):
    """What a capability may see: references + minimized, permitted facts. Never credentials."""

    execution_context: ExecutionContextRef
    task: dict[str, Any]
    evidence: EvidenceBundle
    facts: dict[str, Any]  # minimized per capability (prompt §11 context minimization)
    policy_configuration: dict[str, Any]
    limits: Limits


class Finding(_Strict):
    claim_id: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_.-]{1,64}$")]
    claim_type: ClaimType
    claim: ShortText
    support_status: SupportStatus
    evidence_refs: list[Ref] = Field(default_factory=list, max_length=20)
    value: Annotated[str, StringConstraints(max_length=64)] | None = None  # DETERMINISTIC_RESULT only


class Limitations(_Strict):
    missing_evidence: list[ShortText] = Field(default_factory=list, max_length=50)
    contradictions: list[ShortText] = Field(default_factory=list, max_length=50)
    unsupported_claims: list[ShortText] = Field(default_factory=list, max_length=50)
    unresolved_questions: list[ShortText] = Field(default_factory=list, max_length=50)


class AgentOutputModel(_Strict):
    status: OutputStatus
    findings: list[Finding] = Field(max_length=60)
    limitations: Limitations
    recommendations: list[ShortText] = Field(default_factory=list, max_length=20)  # reviewer-facing only


class ReportSection(_Strict):
    section_id: Literal[
        "alert_context",
        "evidence_scope",
        "relevant_activity",
        "calculations",
        "kyc_review",
        "policy_considerations",
        "gaps_and_limitations",
        "analyst_questions",
    ]
    title: ShortText
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=6000)]
    claim_ids: list[str] = Field(default_factory=list, max_length=60)


class ReportDraftModel(_Strict):
    status: OutputStatus
    sections: list[ReportSection] = Field(min_length=1, max_length=8)
    findings: list[Finding] = Field(max_length=80)
    limitations: Limitations
