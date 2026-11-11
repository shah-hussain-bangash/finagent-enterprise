# ADR-0022: Evaluation gates, configuration promotion, retention and legal hold

Status: Accepted (engineering) - 2026-10-16

## Decision
- Versioned SYNTHETIC datasets (JSON, packaged) and PROPOSED thresholds (`eval-thresholds-0.1-PROPOSED`,
  status PROPOSED_NOT_APPROVED). Evaluation runs are immutable and bound to the configuration hash.
  Gate values: PASS_PROPOSED / FAIL_PROPOSED / INCOMPLETE (e.g. caller cannot run retrieval checks).
- Promotion requires a PASS_PROPOSED run of the same hash; one PROMOTED version per tenant; rollback
  re-promotes an earlier evaluated version with lineage.
- Retention rules exist per record class with status NOT_SET and no period (DEC-05/SEC-D05). Retention
  evaluation is a dry run; deletion execution is intentionally not implemented in R1. Legal holds are
  placed/released with reasons (MFA) and exclude cases from eligibility.

## Consequences
A PASS_PROPOSED gate is not an acceptance decision. Real deletion/propagation needs approved periods.
