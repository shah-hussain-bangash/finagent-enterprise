# ADR-0016: Typed connectors and the tool gateway

**Status:** Accepted (engineering) — engineering decision, not institutional approval.
**Date:** 2026-10-16

## Context
Prompt §12–13, FR-012/024, DIR-005..012/021/028/038, SPR-013/016/029, AER-025 and MST:INT-001..003
require read-only financial connectors with resilience and a gateway that authorizes every tool
call independently of the model. The real source systems are OPEN (DATA-D01).

## Decision
1. **Synthetic mock bank** (`apps/mock-bank`, FastAPI, deterministic data, test-only fault
   controls) stands in for every bank system locally; it is the only "mock" outside Azure.
2. **Fixed-destination HTTP client.** The base URL comes from configuration (https outside
   local/test); callers pass a code-defined path template and identifier-pattern parameters that are
   URL-encoded, so no argument can change host, scheme or path. Redirects are not followed; TLS
   verification is never disabled. Responses must declare a supported `schema_version` and an
   aware `as_of`, otherwise they are rejected (DIR-038).
3. **Classified errors and resilience.** Only TIMEOUT / UNAVAILABLE / RATE_LIMITED are retried
   (bounded, jittered, honouring Retry-After). 401/403/revoked/404/409/schema errors are permanent.
   Circuit breakers are per (tenant, source).
4. **Typed projection.** Pydantic models with `extra="ignore"` are the field allowlist; amounts are
   `Decimal` + currency; times stay timezone-aware with the original offset; date-only fields stay
   dates; withheld/unknown are explicit states.
5. **Completeness proof.** The transaction connector follows the cursor to the end, de-duplicates
   by source id, and labels a window COMPLETE only when the cursor ended within the page budget and
   the unique count equals the source's total (DIR-012).
6. **Tool gateway.** Frozen registry; the case scope (tenant, subjects, window) is re-derived from
   the database on every call; checks run in a fixed order and every decision is persisted with an
   argument hash and audited. Read tools only via `invoke`; the single write
   (`update_case_status`) only via `invoke_case_status_write`, requiring the finalization
   capability, the requester's `approval:decide` on the case, the deployment setting AND the tenant
   flag, and running through the external-operation ledger (ADR-0015). Excluded actions have no
   tool definition at all.

## Consequences
Contract and negative tests run against real HTTP locally. LIVE INTEGRATION with any bank system
is **not verified**; values (timeouts, retry counts, freshness 24 h, page sizes) are PROPOSED.
