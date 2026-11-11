# Runbook: uncertain external operation (OUTCOME_UNCERTAIN)

**Trigger:** alert `FinAgentUncertainExternalOperation`, a run in `OUTCOME_UNCERTAIN`, or a case
stuck in `FINALIZING` (escalation rule `finalization-stuck`, 60 min PROPOSED).

**Why it happens:** the downstream case-status write (the only R1 business write) was sent, but the
platform could not prove whether it was applied (timeout after send, transport error, probe UNKNOWN).
The platform never blindly re-sends and never shows the case as Completed (FR-033, DIR-029).

## Steps (Platform Operator, MFA required)
1. Open **Operations** in the web UI (or `GET /api/v1/operations?status=OUTCOME_UNCERTAIN`). Note the
   operation id, case id and `operation_key` reference (the idempotency key sent to the case system).
2. In the **target case system**, look up the operation by that idempotency key.
3. Record the verified outcome:
   - Applied: `POST /api/v1/operations/{id}/reconcile` `{"resolution": "APPLIED", "reason": "..."}`
   - Not applied: `{"resolution": "NOT_APPLIED", "reason": "..."}`
   The run moves to `HUMAN_REVIEW`; nothing is sent automatically.
4. Retry the run (Execution and recovery screen, or `POST /api/v1/workflows/{run}/resume`
   `{"action": "RETRY", "reason": "..."}`). With APPLIED the ledger returns the reconciled operation
   (no second write) and the case completes; with NOT_APPLIED the write is sent once more with the
   same idempotency key.
5. Confirm the case shows `COMPLETED` and the audit trail contains `EXTERNAL_OPERATION_RECONCILED`
   and `CASE_FINALIZED`.

**Never:** edit `external_operations` rows by hand, cancel an uncertain run (refused by design), or
switch the write flag to "fix" a case. If the target system cannot be checked, leave it uncertain and
escalate to the case-system owner (DATA-D06).
