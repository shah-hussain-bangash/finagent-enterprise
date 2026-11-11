"""Typed graph state (prompt §7): references and validated enum values only - never copies of
sensitive data. Routing reads only fields written by deterministic code."""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict


class RunState(TypedDict, total=False):
    run_id: str
    case_id: str
    tenant_id: str
    graph_version: str
    completed: Annotated[list[str], operator.add]
    context_ref: dict[str, Any]
    notification_operation_id: str
    review_decision: str  # validated enum value from the deterministic resume service
    outcome: str
    # aml-1 graph (references and counters only)
    evidence_round: int
    review_round: int
    snapshot_id: str
    blocking: bool
    blocking_reasons: list[str]
    gather_summary: dict[str, Any]
    agent_outputs: dict[str, dict[str, Any]]  # capability -> {output_id, status, boundary}
    report_version_id: str
    approval_request_id: str | None
    evidence_decision: str
