# ADR-0005: Canonical REST paths follow SRS §12

**Status:** Accepted (engineering), conflict C-04 — engineering decision, not institutional approval.
**Date:** 2026-10-15

## Context
SRS and Master name the same endpoints differently.

## Decision
Use SRS paths (`/knowledge/search`, `/audit-events`, `/documents`). Do not ship duplicate aliases. Add endpoints the SRS requires that the v1 prompt omitted (PATCH case, cancel, reassignment, report submit/withdraw).

## Consequences
Smaller, consistent surface; OpenAPI committed and drift-checked in CI.
