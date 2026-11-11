# ADR-0006: PostgreSQL is the system of record; composite tenant keys

**Status:** Accepted (engineering) — engineering decision, not institutional approval.
**Date:** 2026-10-15

## Context
MST ARCH/DB require tenant integrity; prompt asks for constraints over application checks.

## Decision
Every tenant-scoped table has `UNIQUE(tenant_id, id)` and child tables reference `(tenant_id, parent_id)` via composite foreign keys, making cross-tenant relationships impossible at the DB level. Alert dedup key `(tenant_id, source_system, source_alert_id)`. Audit table protected by a trigger that rejects UPDATE/DELETE. RLS deferred (needs an audited session-variable design).

## Consequences
Isolation is enforced twice (query scoping + schema). Redis is cache only.
