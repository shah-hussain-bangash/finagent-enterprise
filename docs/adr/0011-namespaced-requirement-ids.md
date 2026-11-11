# ADR-0011: Namespaced requirement IDs

**Status:** Accepted (engineering), conflict C-07 — engineering decision, not institutional approval.
**Date:** 2026-10-15

## Context
Blueprint, Master and BRD/SRS reuse BR/FR/UI IDs with different meanings.

## Decision
Unprefixed IDs = formal series. Master = `MST:`, Blueprint = `BP:`. The generator rejects unknown IDs in test markers.

## Consequences
Traceability cannot silently point at the wrong requirement.
