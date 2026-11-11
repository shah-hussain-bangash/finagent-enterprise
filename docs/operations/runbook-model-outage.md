# Runbook: model provider outage, budget breach, invalid output

**Degraded mode (NQ-017):** case reads, evidence review, document review, report editing, submission
and reviewer decisions keep working without the model. Only new capability steps stop. Nothing is
fabricated: a run waits (RETRYING) or moves to HUMAN_REVIEW with the reason.

1. **Transient errors** (timeout, 429, 5xx) are retried by the gateway at most `model_max_retries`
   (2) times per call, then the run retries with backoff up to `run_max_attempts`; afterwards the run
   goes to HUMAN_REVIEW "model unavailable". When the provider is healthy, retry the run.
2. **Permanent errors** (content filter, auth, bad request) are never retried: check the deployment
   name, key rotation in Key Vault and the provider's content-filter configuration.
3. **Budget breach** (`BUDGET_DENIED` in `model_requests`): per-capability, per-run or tenant-monthly
   limits stopped billable work. Confirm with the budget owner (DEC-07) before raising
   `run_token_budget` / `tenant_monthly_cost_budget`.
4. **Invalid output** (`INVALID_OUTPUT`): one schema repair is attempted; a second failure escalates.
   Repeated invalid output after a model/prompt change -> roll back the configuration
   (`POST /api/v1/admin/config-versions/{previous}/rollback`) and re-run the evaluation.
