# ADR-0015: Durable workflow runtime (leases, outbox/inbox, checkpoints, effect ledger)

**Status:** Accepted (engineering) — engineering decision, not institutional approval.
**Date:** 2026-10-16

## Context
Prompt §7, §20 and §21 and FR-031/033/034/035, DIR-020/029/031, NQ-019..023 and AER-021 require
work to survive a worker crash without losing or repeating protected effects. ADR-0004 makes
LangGraph the only workflow owner.

## Decision
1. **The database is the source of truth.** A run is a `workflow_runs` row. The queue only lowers
   latency: the worker also scans for queued, retry-due and orphaned (RUNNING with no live lease)
   runs, so a lost or duplicated message delays work but never loses or repeats it.
2. **Fenced leases.** A guarded `INSERT … ON CONFLICT DO UPDATE … WHERE expired OR free RETURNING`
   claims a run and increments a monotonic fencing token. Lease rows are never deleted. Every
   protected commit (step start/finish, checkpoint `put`/`put_writes`, ledger transitions, run
   status) first runs `assert_current`, which locks the lease row and checks owner + token + expiry
   using database time. A heartbeat thread renews the lease; a killed worker's lease simply expires.
3. **LangGraph checkpointer in our schema** (`checkpoints`, `checkpoint_blobs`, `checkpoint_writes`).
   One saver serves one run of one tenant. Payloads are AES-256-GCM encrypted, with associated data
   `tenant:run:namespace:kind`, so rows copied across tenants/runs do not decrypt. Writes are fenced.
   Each checkpoint records the graph version and fencing token.
4. **Completed-step references.** Nodes do work only through `RunContext.run_step`, which stores
   one row per `step_key`. On replay, a SUCCEEDED step returns its stored output reference and is not
   redone. Cancellation is checked at both step boundaries: output that finishes after a cancel is
   `DISCARDED`, and a cancel arriving after the last boundary is honoured before parking the run at
   an interrupt.
5. **External-effect ledger.** `external_operations(tenant, type, key)` records intent (SENT)
   before the effect, sends the key as the provider's idempotency key, and asks the provider
   (`probe`) before any retry. An outcome that cannot be proven becomes `OUTCOME_UNCERTAIN`, which
   stops the run until an operator reconciles it (`POST /api/v1/operations/{id}/reconcile`, MFA). A
   reconciled run goes to `HUMAN_REVIEW`; it is never resumed automatically. We do not claim
   exactly-once execution.
6. **Outbox/inbox.** At-least-once relay with signed envelopes; consumer dedup through `inbox_events`
   in the handler's transaction; a dead-letter store for poison messages (ADR-adjacent
   `docs/api/events.md`).
7. **Resume validation.** Before any node runs: terminal/paused runs are skipped; pending
   cancellation is honoured; the graph version must equal the deployed one (else `HUMAN_REVIEW`
   with `GRAPH_VERSION_MISMATCH`); and the requester's current rights are re-resolved from the
   database (else `HUMAN_REVIEW` with `REQUESTER_NOT_AUTHORIZED`). Resume input comes only from
   deterministic services and is validated as an enum.
8. **Retries** apply only to unclassified/transient exceptions, bounded by `max_attempts`, with
   exponential backoff (`RETRYING`, `next_attempt_at`). Permission, validation and rejected-effect
   errors fail permanently. The step budget (12, MST:AG-002) moves a run to `HUMAN_REVIEW`.

## Consequences
Slice 2 is demonstrated with real processes (`scripts/demo_slice2.py`): a worker is hard-killed
mid-step, a second worker resumes with token 2, and there is exactly one notification, one ledger
send and one inbox delivery. The Phase-4 graph is a skeleton without agents; P9 replaces it under a
new graph version. Azure Service Bus, Key Vault keys and multi-node timing are **not verified**.
