# Prompt Gap & Conflict Review (v1 → v2)

Reviewed: the v1 build prompt against all supplied sources (Problem Statement, Blueprint, BRD-001,
SRS-002, DIR-004, AER-005, SPR-006, NFR-007, RTM-008, Master v1.0, architecture diagram).
Result: v1 was strong on intent but **contradicted the formal requirements in 12 places** and
**omitted ~40 documented requirements**. v2 (`docs/prompts/MASTER_BUILD_PROMPT_v2.md`) fixes both.

## A. Source baseline observations

| # | Finding | Impact | Action |
|---|---|---|---|
| A-1 | Two RTM uploads are byte-identical (same SHA-256). | None | One copy kept in `source/`. |
| A-2 | Document 3 (Use Cases & Acceptance Criteria) is absent; RTM §2 confirms it is pending. | No formal UC IDs | `provisional-use-cases.md` (PUC-*) created and labelled provisional. |
| A-3 | RTM counts 245 requirements; extraction from the source PDFs reproduces exactly 245 (BR 15, FR 40, UI 2, NFR 12, DIR 39, AER 37, SPR 50, NQ 50). | Inventory verified | `requirements.json` generated, not hand-typed. |
| A-4 | Every source is a draft; no requirement is approved; no named owners. | Nothing may be marked APPROVED | Status model enforced in traceability generator. |
| A-5 | SPR-006 notes some official legal pages could not be retrieved directly. | Legal applicability unverified | Applicability register marks all regimes PENDING. |

## B. Conflicts between v1 prompt and sources

| ID | Topic | v1 prompt said | Source says | v2 resolution |
|---|---|---|---|---|
| C-01 | Case states | OPEN, INVESTIGATING, AWAITING_REVIEW, RETURNED_FOR_REWORK, APPROVED, CLOSED, CANCELLED | SRS §3 / BRD §4: Received, Assigned, Investigating, **Blocked**, Awaiting Review, Rework, Approved, **Finalizing**, **Completed**, Cancelled | Adopt SRS (DOCUMENTED). v1 lacked Blocked and Finalizing, so "approved but external write unconfirmed" could not be represented (violates DIR §7, BRD completion boundary). |
| C-02 | Run states | CREATED…UNCERTAIN (Master names) | SRS FR-005: Queued, Running, Waiting for Evidence, Waiting for Review, Retrying, Succeeded, Failed, Cancelled, Outcome Uncertain | SRS names canonical, Master names documented as aliases (ADR-0003). |
| C-03 | Reviewer decisions | approve / reject / rework / request info | SRS FR-028: Approve, Return for Rework, Request Information (no Reject). BRD: rejection "returns to Rework or closes … according to policy" | REJECT kept behind `approval.reject_effect` (default REWORK). OPEN DEC-11. |
| C-04 | API paths | `/knowledge/query`, `/audit/events`, `/documents/uploads` (Master) | SRS §12: `/knowledge/search`, `/audit-events`, `/documents`; plus `PATCH /cases/{id}`, `/workflows/{id}/cancel` which v1 omitted | SRS paths canonical (ADR-0005); missing endpoints added. |
| C-05 | Alert dedup key | `UNIQUE(tenant_id, external_alert_id)` | DIR-026 / FR-008: tenant + **source_system** + source_alert_id | Corrected; two monitoring systems can legitimately reuse an alert number. |
| C-06 | Eval dataset | ≥100 scenarios | AER-005 §10: 200 families, 100/50/50 split, 6 slices | Target 200; 100 is interim. |
| C-07 | Requirement IDs | "preserve BR-*/FR-*" | Blueprint, Master and BRD/SRS reuse BR/FR/UI IDs with different meanings | Namespacing `MST:`, `BP:` (v2 §55). Without this, traceability is silently wrong. |
| C-08 | Temporal | forbidden unless ADR | Blueprint/Master: optional | ADR-0004: not used in R1. Consistent with both. |
| C-09 | Local vector search | unspecified | Blueprint: pgvector optional | ADR-0007: deterministic local adapter; pgvector reserved. |
| C-10 | Actors | 8 actors | Diagram adds "Compliance Officer" | OPEN DEC-10: map to Reviewer or Policy Administrator. |
| C-11 | Channels/analytics | silent | Diagram: GraphQL, Mobile, Data Lake, Spark, Power BI, Teams, SIEM | Explicitly out of R1 (v2 §63). |
| C-12 | Master DDL | (n/a) | Master `cases.status CHECK` lists only 4 states | Superseded by C-01 in migration. |

## C. Documented requirements v1 omitted (now in v2 §57 and elsewhere)

Grouped; each cites its source so it can be verified.

**Case & workflow:** optimistic concurrency / stale-version conflict (FR-004, UI-002) · approval
invalidation on change and history on reopen (FR-006, AER-016, DIR-018) · supervisor reassignment
with reason (FR-009) · escalation/due-time queue, once-per-rule (FR-011) · cancellation endpoint
without implied rollback (FR-035) · case cannot be Completed while external write is uncertain
(DIR §7, BRD §3) · analyst draft editing, submit/withdraw, frozen review snapshot (FR-027).

**Data & integration:** intake quarantine with owner/resolution (DIR-010, DIR-025) · source
register (DIR-001) · Decimal money, per-currency, no float, conversion stored separately (DIR-008)
· missing/unknown/withheld/N-A/extraction-failed distinctions (DIR-009) · timezone handling,
date-only fields (DIR-007) · transaction completeness proof (DIR-012) · calculation rules incl.
the DIR-013 AED/USD fixture · identity resolution without name merge (DIR-011) · watermarks and
backfills (DIR-030, DIR-039) · corrected records create new evidence versions (DIR §5) · delivery
event_id distinct from business key (DIR-022) · consumer inbox dedup.

**Agents & evaluation:** KYC checklist item states (AER-006) · one schema-repair attempt
(AER §8) · tool arguments may not widen scope (AER-025) · no chain-of-thought as audit artifact
(AER §2, SPR-037) · AER's 200-family dataset and slices · N/A when zero claims (AER-032) · 3× repeat
runs, worst-per-family (AER §13) · LLM-judge triage only (AER-034) · two-reviewer labelling
(AER-031; scaffold only — OPEN) · locked set never tuned on (AER-030).

**Security & privacy:** MFA/step-up (SPR-005) · access reviews (SPR-006) · break-glass (SPR-008) ·
short-lived download links, export bounds (SPR-012) · SSRF/path traversal/parameterization
(SPR-013) · egress allowlists (SPR-016) · upload magic bytes, parser limits, 413 (SPR-017,
DIR-014) · rights-request and confidentiality routing (SPR-020, SPR-037) · tipping-off boundary ·
restore re-applies holds/deletions (SPR-025) · audit failure fails the action, tamper evidence
(SPR-035/036) · production data never in dev/eval (SPR-045).

**NFR & operations:** NQ §2 load profile · pagination 50/100 (NQ §3) · estimated vs reconciled
cost, tenant/monthly budgets (NQ-047/048) · "Not evaluated" instead of pass when thresholds
unset (DIR-024) · browser/zoom/viewport matrix (NQ-042) · availability measured by journeys, not
liveness (NQ-016) · runbooks for budget exhaustion, revocation, legal hold.

**UI:** Document Review screen and Execution/Recovery monitor (SRS §11) · escalation queue ·
Draft vs Approved marking in UI and exports (FR-030).

## D. Practical gaps in v1 as an agent instruction

| Gap | Why it matters | v2 fix |
|---|---|---|
| No session protocol | The scope cannot fit one Claude Code session; agents lose state and redo or skip work | §60: phase-sized sessions, session log, stop conditions |
| No environment-honesty rule | Agents tend to "complete" Terraform/Azure steps they cannot run | §61 |
| Traceability hand-maintained | Hand-typed PASSED statuses drift and become fabricated | §62: generated from junit |
| Monorepo layout implies many top-level Python packages (`security`, `database`) | Import collisions and sys.path hacks | ADR-0002 single `src/finagent` package |
| No starter/handoff contract | Agent may regenerate existing work | §59 |
| Model names implied by diagram | Hard-coded deployments break governance | §2: deployments are OPEN config |
| No proposed-defaults table | Agents invent numbers | §64 lists sourced defaults, status PROPOSED |
