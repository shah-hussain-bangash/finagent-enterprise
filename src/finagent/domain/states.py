"""Case and run state machines (SRS §3, FR-004, FR-005; ADR-0003).

Pure data + pure functions. Callers persist transitions with an optimistic record_version check;
these functions only decide whether a transition is legal. An LLM response is never a command.
"""

from __future__ import annotations

from enum import StrEnum


class CaseStatus(StrEnum):
    RECEIVED = "RECEIVED"
    ASSIGNED = "ASSIGNED"
    INVESTIGATING = "INVESTIGATING"
    BLOCKED = "BLOCKED"
    AWAITING_REVIEW = "AWAITING_REVIEW"
    REWORK = "REWORK"
    APPROVED = "APPROVED"
    FINALIZING = "FINALIZING"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


CASE_TERMINAL = frozenset({CaseStatus.COMPLETED, CaseStatus.CANCELLED})

_CASE_EDGES: dict[CaseStatus, frozenset[CaseStatus]] = {
    CaseStatus.RECEIVED: frozenset({CaseStatus.ASSIGNED}),
    CaseStatus.ASSIGNED: frozenset({CaseStatus.INVESTIGATING}),
    CaseStatus.INVESTIGATING: frozenset({CaseStatus.BLOCKED, CaseStatus.AWAITING_REVIEW}),
    CaseStatus.BLOCKED: frozenset({CaseStatus.INVESTIGATING}),
    CaseStatus.REWORK: frozenset({CaseStatus.AWAITING_REVIEW, CaseStatus.BLOCKED}),
    CaseStatus.AWAITING_REVIEW: frozenset({CaseStatus.REWORK, CaseStatus.BLOCKED, CaseStatus.APPROVED}),
    # APPROVED/FINALIZING -> REWORK: material change invalidates approval (FR-006, AER-016).
    CaseStatus.APPROVED: frozenset({CaseStatus.FINALIZING, CaseStatus.REWORK}),
    CaseStatus.FINALIZING: frozenset({CaseStatus.COMPLETED, CaseStatus.REWORK}),
    CaseStatus.COMPLETED: frozenset(),
    CaseStatus.CANCELLED: frozenset(),
}


class RunStatus(StrEnum):
    CREATED = "CREATED"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    WAITING_EVIDENCE = "WAITING_EVIDENCE"  # Master alias: WAITING_ANALYST
    WAITING_REVIEW = "WAITING_REVIEW"  # Master alias: WAITING_APPROVAL
    HUMAN_REVIEW = "HUMAN_REVIEW"
    RETRYING = "RETRYING"  # Master alias: RETRY_PENDING
    SUCCEEDED = "SUCCEEDED"  # Master alias: COMPLETED
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    OUTCOME_UNCERTAIN = "OUTCOME_UNCERTAIN"  # Master alias: UNCERTAIN


RUN_ALIASES: dict[str, RunStatus] = {
    "WAITING_ANALYST": RunStatus.WAITING_EVIDENCE,
    "WAITING_APPROVAL": RunStatus.WAITING_REVIEW,
    "RETRY_PENDING": RunStatus.RETRYING,
    "COMPLETED": RunStatus.SUCCEEDED,
    "UNCERTAIN": RunStatus.OUTCOME_UNCERTAIN,
}

RUN_TERMINAL = frozenset({RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED})

_PAUSED = frozenset({RunStatus.WAITING_EVIDENCE, RunStatus.WAITING_REVIEW, RunStatus.HUMAN_REVIEW})

_RUN_EDGES: dict[RunStatus, frozenset[RunStatus]] = {
    RunStatus.CREATED: frozenset({RunStatus.QUEUED}),
    RunStatus.QUEUED: frozenset({RunStatus.RUNNING}),
    RunStatus.RUNNING: frozenset(
        {RunStatus.RETRYING, RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.OUTCOME_UNCERTAIN} | _PAUSED
    ),
    RunStatus.RETRYING: frozenset({RunStatus.RUNNING, RunStatus.FAILED}),
    RunStatus.WAITING_EVIDENCE: frozenset({RunStatus.QUEUED, RunStatus.HUMAN_REVIEW}),
    RunStatus.WAITING_REVIEW: frozenset({RunStatus.QUEUED, RunStatus.HUMAN_REVIEW}),
    RunStatus.HUMAN_REVIEW: frozenset({RunStatus.QUEUED, RunStatus.FAILED}),
    # Uncertain external effects are resolved only by reconciliation, never blind retry (FR-033).
    RunStatus.OUTCOME_UNCERTAIN: frozenset({RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.HUMAN_REVIEW}),
    RunStatus.SUCCEEDED: frozenset(),
    RunStatus.FAILED: frozenset(),
    RunStatus.CANCELLED: frozenset(),
}


class InvalidTransitionError(Exception):
    def __init__(self, kind: str, current: str, target: str) -> None:
        super().__init__(f"{kind} transition {current} -> {target} is not permitted")
        self.kind, self.current, self.target = kind, current, target


def reopening_transition(current: CaseStatus) -> CaseStatus:
    """Explicit completed-episode reopening; generic terminal edits stay forbidden."""
    if current is not CaseStatus.COMPLETED:
        raise InvalidTransitionError("case reopening", current, CaseStatus.REWORK)
    return CaseStatus.REWORK


def case_transition(current: CaseStatus, target: CaseStatus) -> CaseStatus:
    if target is CaseStatus.CANCELLED and current not in CASE_TERMINAL:
        return target
    if target not in _CASE_EDGES[current]:
        raise InvalidTransitionError("case", current, target)
    return target


def run_transition(current: RunStatus, target: RunStatus) -> RunStatus:
    # Cancellation stops new scheduling; it is allowed from any non-terminal state (FR-035).
    # OUTCOME_UNCERTAIN cannot be cancelled away: the effect must be reconciled first.
    if target is RunStatus.CANCELLED and current not in RUN_TERMINAL | {RunStatus.OUTCOME_UNCERTAIN}:
        return target
    if target not in _RUN_EDGES[current]:
        raise InvalidTransitionError("run", current, target)
    return target


def allowed_case_targets(current: CaseStatus) -> frozenset[CaseStatus]:
    extra = frozenset() if current in CASE_TERMINAL else frozenset({CaseStatus.CANCELLED})
    return _CASE_EDGES[current] | extra
