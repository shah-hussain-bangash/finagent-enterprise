# ADR-0020: AML investigation graph, independent review and finalization gate

Status: Accepted (engineering) - 2026-10-16

## Decision
- `aml_investigation` (graph `aml-1`) replaces the P4 skeleton; the skeleton stays registered as
  `durability_probe` (local/test only) for recovery tests and slice 2.
- One durable run per investigation: evidence rounds (BLOCKING gaps -> WAITING_EVIDENCE + case
  BLOCKED), specialist analysis, draft, ledgered "draft ready" notification, then a review interrupt.
  Only deterministic services resume it: reviewer APPROVE / REQUEST_INFORMATION / REJECT(CLOSE) and the
  analyst's EVIDENCE_SUPPLIED / REANALYZE. RETURN_FOR_REWORK leaves the run waiting while the analyst
  edits and resubmits.
- Step budget counts model/tool steps only (12, MST:AG-002 PROPOSED); bookkeeping steps are exempt.
- Reports are immutable versions with claim-to-evidence links; edits after submission/approval and
  new/changed evidence invalidate open approvals (case -> REWORK).
- Decisions bind the exact report version and snapshot the reviewer saw; SoD excludes the submitter,
  report authors and the case analyst, including through delegations; MFA step-up; idempotent;
  append-only. REJECT effect is `approval_reject_effect` (DEC-11, default REWORK).
- Finalization revalidates everything at execution time; the single write runs only with the
  deployment setting AND tenant flag, through the effect ledger; otherwise it is recorded as
  "not performed by configuration". Uncertain outcomes keep the case FINALIZING.

## Consequences
Re-analysis loops beyond two rounds can exhaust the PROPOSED step budget (HUMAN_REVIEW, visible).
