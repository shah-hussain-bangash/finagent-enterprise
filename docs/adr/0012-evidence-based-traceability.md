# ADR-0012: Evidence-based traceability

**Status:** Accepted (engineering) — engineering decision, not institutional approval.
**Date:** 2026-10-15

## Context
Prompt forbids claiming PASSED without evidence.

## Decision
Tests carry `@pytest.mark.req(...)`; junit XML is the only input for TEST PASSED / FAILED in generated traceability.

## Consequences
Status cannot drift from reality.
