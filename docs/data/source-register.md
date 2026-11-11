# Source Register (DIR-001, DIR-004, DIR-005, DIR-021)

**Status: SYNTHETIC / local only.** Every row below describes the synthetic mock bank
(`apps/mock-bank`). The real source products, endpoints, owners and access approvals are an OPEN
decision (DATA-D01, DEC-04). No connector may be enabled against a real system until its row is
completed and approved by the named data owner (DIR-004).

| Source (connector) | Owner | Purpose | Delivery | Operations (allowed) | Field projection | Classification | Credentials scope | Contract version | Pagination / ordering | Rate limit | Freshness | Correction / deletion signal | Status |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Alert source (TM) | OPEN (AML Ops) | Create cases from TM alerts | Push (`POST /api/v1/alerts`) | ingest only | `AlertEnvelope` v1.0 (extra fields rejected) | RESTRICTED | Integration identity per tenant + source (`integration_connections`) | 1.0 | n/a (single events; dedup on source_system + source_alert_id) | API quotas (NQ-028) | event time + receipt time recorded | Changed payload → 409 + reconciliation (DIR-025) | SYNTHETIC |
| Customer / KYC profile | OPEN (Data owner) | Customer facts for AG-KYC | On demand | `GET /v1/customers/{ref}`, `/documents` | `CustomerProfile`, `DocumentMeta` (`integrations/sources.py`) | CONFIDENTIAL | Tenant API key from Key Vault (`connector_credentials`) | 1.0 | n/a | 1200/min client-side (PROPOSED) | `as_of`; STALE beyond 24 h (DATA-D04) | revocation → 403 CONSENT_REVOKED (not retried) | SYNTHETIC |
| Transactions (ledger read model) | OPEN | Window evidence for AG-AML | On demand | `GET /v1/customers/{ref}/transactions` | `Transaction` (Decimal amount + ISO currency, aware time, date-only value date) | CONFIDENTIAL | as above | 1.0 | Cursor; `[from, to)`; ordered by booking_time, id; completeness proof (DIR-012) | as above | oldest page `as_of` | duplicates removed and counted | SYNTHETIC |
| Document repository | OPEN | KYC documents (P6 parsing) | On demand | `GET /v1/documents/{id}/content` | bytes, ≤ 20 MiB (PROPOSED) | CONFIDENTIAL | as above | 1.0 | n/a | as above | document version | new version supersedes | SYNTHETIC |
| Policy repository | OPEN (Compliance) | Approved policy corpus (P7 RAG) | On demand / sync | `GET /v1/policies` | `PolicyDocument` | INTERNAL / RESTRICTED | as above | 1.0 | n/a | as above | effective_from / effective_to | superseded version kept as history | SYNTHETIC |
| Sanctions screening summary | OPEN (Compliance) | Read-only status for context | On demand | `GET /v1/sanctions/summary/{ref}` | `SanctionsSummary` | RESTRICTED | as above | 1.0 | n/a | as above | `screened_at`, list version | n/a | SYNTHETIC (no sanctions decisions are made) |
| Case-status system | OPEN (DATA-D06) | The single R1 business write | On demand, keyed | `PUT /v1/cases/{ref}/status` + `GET /v1/case-status-operations/{key}` | status enum + reason code only | INTERNAL | as above | 1.0 | n/a | no retry (ledger probes) | n/a | ambiguous → OUTCOME_UNCERTAIN | SYNTHETIC; **OFF** by default (deployment setting + tenant flag) |
| Notifications | OPEN (channel decision, §63) | Escalation / workflow notices (references only) | Push, keyed | `POST /v1/notifications`, `GET /v1/notifications/{key}` | role, template, resource ref | INTERNAL | as above | 1.0 | n/a | retries bounded (5) | n/a | n/a | SYNTHETIC |

Timeouts (5 s), retry classes (TIMEOUT / UNAVAILABLE / RATE_LIMITED only, max 3 attempts, jittered
backoff), circuit breaker (5 failures, 30 s), and credential rotation (Key Vault) are configured in
`Settings` and are PROPOSED values pending DATA-D05.
