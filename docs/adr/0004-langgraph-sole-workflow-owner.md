# ADR-0004: LangGraph is the sole AI workflow owner; no Temporal in R1

**Status:** Accepted (engineering) — engineering decision, not institutional approval.
**Date:** 2026-10-15

## Context
Blueprint lists Temporal as optional; the prompt forbids a competing workflow owner.

## Decision
LangGraph owns AI state transitions with a PostgreSQL checkpointer in our schema. Durable job delivery uses the transactional outbox + queue + fenced leases owned by `finagent.worker`. Revisit only via a new ADR.

## Consequences
One retry path per operation; fewer moving parts for the pilot.
