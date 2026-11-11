# Runbook: worker failure, stuck runs, dead letters and recovery

## Worker crash or restart
Nothing to do in most cases. Runs are leased with a fencing token; when a worker dies its lease
expires (`lease_ttl_seconds`, 30 s PROPOSED) and another worker resumes from the last committed
checkpoint. Committed steps are never repeated; externally visible effects go through the ledger
(probe before resend). Verified by `tests/recovery/*` and the E2E crash step.

Check: `GET /api/v1/reporting/overview` (operator) - `runs_by_status`; `docker compose ps` locally.

## Run in HUMAN_REVIEW
Read the reason on the Execution and recovery screen (`/workflows/{id}`):
| Reason | Action |
|---|---|
| `step budget exhausted` / `model budget exhausted` | Decide whether the case needs more analysis; budgets are PROPOSED (AI-D05). Retry only after the budget owner agrees. |
| `escalated to a human: authority boundary crossed` | A capability output asserted guilt/approval/account action. Do NOT retry blindly; report to model governance (AER-037), keep the evidence, start a fresh run after review. |
| `requester no longer authorized` | Reassign the case (supervisor), then the new analyst starts a run. |
| `CONFIGURATION_NOT_PROMOTED` | Run an evaluation and promote the configuration (Tenant Admin) or roll back. |
| `checkpoint graph ... != deployed` | The graph changed under an in-flight run. Cancel and restart the run; committed effects are kept. |
| `model unavailable (...)` | See runbook-model-outage.md. |

Retry: `POST /api/v1/workflows/{id}/resume {"action": "RETRY", "reason": "..."}`.

## Dead letters
Poison events land in `dead_letters` after `consumer_max_deliveries` (5 PROPOSED). Inspect the reason
code; fix the producer or data; replay is a deliberate operator action (no auto-replay).

## Documents stuck in PARSING
A crashed parser leaves the version PARSING; after `stale_after` (10 min) the next worker reclaims it
(attempt counter fences the stale worker). After `document_max_attempts` it stays PARSE_FAILED with
the source preserved; the analyst uploads a corrected version.
