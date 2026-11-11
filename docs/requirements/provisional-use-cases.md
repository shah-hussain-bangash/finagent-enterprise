# Provisional Use Cases (PUC) — NOT Document 3

> **PROVISIONAL.** The formal "Use Cases and Acceptance Criteria" document (Document 3) has not been
> supplied (RTM §2, DEC-12). These scenarios are derived only from documented test/acceptance
> statements so E2E and UAT tests have stable references. Replace via ADR when Document 3 arrives.

| PUC | Scenario (actor) | Derived from | Key requirements |
|---|---|---|---|
| PUC-01 | Normal alert → assigned case → sourced draft → review → confirmed completion (Analyst, Reviewer) | SRS T-01, BRD AC-01, MST UC-01/06 | FR-004..010, 012, 016, 026..029 |
| PUC-02 | Replay of same alert returns one case; changed payload conflicts (Integration) | SRS T-02, BRD AC-06 | FR-007, 008, 032, DIR-026/027 |
| PUC-03 | Missing document creates explicit gap; analyst requests evidence (Analyst) | BRD AC-02, MST UC-02 | FR-015, AER-007 |
| PUC-04 | Contradictory records retained and routed for resolution (Analyst) | BRD AC-03, SRS T-03 | FR-015, DIR-011 |
| PUC-05 | Other tenant/role cannot read, search, export or approve (attacker) | SRS T-04, BRD AC-04, MST UAT | FR-001/002, SPR-009..013 |
| PUC-06 | Rejection/rework preserves version history (Reviewer, Analyst) | BRD AC-05, SRS T-05 | FR-006, 027, 028 |
| PUC-07 | Self-approval, stale and expired approval denied (Analyst-as-reviewer) | SRS T-05 | FR-003, SPR-033 |
| PUC-08 | Worker crash / competing workers / stale worker / timeout-after-commit (System) | SRS T-06, MST UC-04 | FR-031..035, DIR-031 |
| PUC-09 | Injected instructions, fake citations, malformed output, forbidden tools blocked | SRS T-07 | FR-019..025, SPR-028..031 |
| PUC-10 | Policy/model/prompt change evaluated; unapproved promotion rejected; rollback keeps lineage | SRS T-08, BRD AC-07 | FR-017, 025, 040 |
| PUC-11 | Legal hold, deletion propagation, restored backup handling | SRS T-09 | FR-039, SPR-023..025 |
| PUC-12 | Failure alert, budget stop, metrics reconciliation, safe telemetry | SRS T-10, BRD AC-08 | FR-011, 037, 038 |
| PUC-13 | Service quality: load, accessibility, backup/restore | SRS T-11 | NFR-001..012, NQ-* |
| PUC-14 | Retrieve current policy with effective-date citation | MST UC-03 | FR-017..020 |
| PUC-15 | Analyst corrects incorrect AI claim | MST UC-05 | FR-022, 027 |
| PUC-16 | Revoke user access; no future retrieval | MST UC-07 | SPR-006, 011, DIR-033 |
| PUC-17 | Export authorized audit/report package | MST UC-08 | FR-030, FR-036 |
| PUC-18 | Citation opens exact cited policy revision, not current replacement | MST UAT | FR-016, 019 |
| PUC-19 | Ambiguous case-status write → Outcome Uncertain → manual reconciliation | DIR-029, SRS FR-029/033 | REC-003 |
