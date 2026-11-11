# ADR-0003: State models follow the SRS; Master names are aliases

**Status:** Accepted (engineering), source conflict C-01/C-02 — engineering decision, not institutional approval.
**Date:** 2026-10-15

## Context
SRS §3/FR-005 (formal series) and the Master/v1 prompt define different case and run states. The v1 case model lacked BLOCKED, FINALIZING and COMPLETED.

## Decision
Case: RECEIVED, ASSIGNED, INVESTIGATING, BLOCKED, AWAITING_REVIEW, REWORK, APPROVED, FINALIZING, COMPLETED, CANCELLED. Run: CREATED, QUEUED, RUNNING, WAITING_EVIDENCE, WAITING_REVIEW, HUMAN_REVIEW, RETRYING, SUCCEEDED, FAILED, CANCELLED, OUTCOME_UNCERTAIN. Transition tables are pure data in `finagent.domain` and unit-tested exhaustively. Added APPROVED/FINALIZING→REWORK for approval invalidation (FR-006).

## Consequences
An unconfirmed external write keeps the case in FINALIZING (never COMPLETED), satisfying DIR §7 and BRD §3.
