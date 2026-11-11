"""Release-1 tool registry (prompt §12; MST:INT-001..003). Code-defined and frozen: there is no
runtime tool discovery or registration.

R1 tools are READ_ONLY except `update_case_status`, the single approved business write, which is
never in any agent's allowlist and runs only through the finalization path behind two flags.
Excluded financial/regulatory actions (money movement, freeze, credit, risk score, sanctions
decision, filing, role changes, arbitrary HTTP) have NO tool definition at all, so a request for
them is "unknown tool" -> denied and audited (SPR-029).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from finagent.security.authorization.policy import Permission

REF = r"^[A-Za-z0-9._:-]{1,128}$"


class SideEffect(StrEnum):
    READ_ONLY = "READ_ONLY"
    CASE_STATUS_WRITE = "CASE_STATUS_WRITE"


class _Args(BaseModel):
    # extra="forbid": a model cannot smuggle a URL, host, tenant, role or amount into a call.
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class CustomerArgs(_Args):
    customer_ref: str = Field(pattern=REF)


class TransactionArgs(_Args):
    customer_ref: str = Field(pattern=REF)
    window_start: AwareDatetime | None = None  # defaults to the case window; may only narrow it
    window_end: AwareDatetime | None = None


class DocumentArgs(_Args):
    customer_ref: str = Field(pattern=REF)
    document_id: str = Field(pattern=REF)


class CaseStatusArgs(_Args):
    status: Literal["UNDER_REVIEW_COMPLETE", "RETURNED_FOR_REWORK", "CLOSED_NO_FURTHER_ACTION_BY_PLATFORM"]
    reason_code: str = Field(pattern=r"^[A-Z0-9_]{3,64}$")


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    version: str
    owner: str
    description: str
    input_model: type[_Args]
    required_permissions: frozenset[Permission]
    data_classes: frozenset[str]
    timeout_s: float
    max_attempts: int
    idempotency: Literal["NATURAL", "KEYED"]
    side_effect: SideEffect
    enabled: bool
    subject_field: str | None  # argument that must stay inside the case's subjects
    handler: str  # name of the bound handler in the gateway (no callables from config)


def _read(name: str, description: str, model: type[_Args], data_classes: set[str]) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        version="1",
        owner="platform-engineering",
        description=description,
        input_model=model,
        required_permissions=frozenset({Permission.CASE_READ}),
        data_classes=frozenset(data_classes),
        timeout_s=5.0,
        max_attempts=3,
        idempotency="NATURAL",
        side_effect=SideEffect.READ_ONLY,
        enabled=True,
        subject_field="customer_ref",
        handler=name,
    )


TOOLS: MappingProxyType[str, ToolDefinition] = MappingProxyType(
    {
        t.name: t
        for t in (
            _read(
                "get_customer_profile",
                "Customer master/KYC profile (projected fields)",
                CustomerArgs,
                {"CONFIDENTIAL"},
            ),
            _read(
                "list_kyc_documents", "KYC document metadata for the customer", CustomerArgs, {"CONFIDENTIAL"}
            ),
            _read(
                "get_transactions",
                "Transactions in the case window with a completeness proof",
                TransactionArgs,
                {"CONFIDENTIAL"},
            ),
            _read(
                "fetch_kyc_document",
                "Content of one KYC document that the source lists for this customer",
                DocumentArgs,
                {"CONFIDENTIAL"},
            ),
            _read(
                "get_sanctions_summary",
                "Read-only screening summary (no decisions)",
                CustomerArgs,
                {"RESTRICTED"},
            ),
            ToolDefinition(
                name="update_case_status",
                version="1",
                owner="aml-operations",
                description="Write the authorized downstream case status (DATA-D06); finalization only",
                input_model=CaseStatusArgs,
                required_permissions=frozenset({Permission.APPROVAL_DECIDE}),
                data_classes=frozenset(),
                timeout_s=10.0,
                max_attempts=1,
                idempotency="KEYED",
                side_effect=SideEffect.CASE_STATUS_WRITE,
                enabled=True,
                subject_field=None,
                handler="update_case_status",
            ),
        )
    }
)

Handler = Callable[..., dict[str, Any]]


def window_within(start: datetime | None, end: datetime | None, lo: datetime, hi: datetime) -> bool:
    s, e = start or lo, end or hi
    return lo <= s < e <= hi
