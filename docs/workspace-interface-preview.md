# FinAgent workspace interface preview

This change adds an authenticated, additive `/workspace` route and a navigation link. It does not replace the existing investigation portal, sign-in, MFA, backend, or database. No dependency versions are changed.

## Scope

This is the UI-first phase requested before integration and workflow testing. All displayed records, relative deadlines, activity, agent durations, role choices, notifications, policy references, and metrics are explicitly illustrative. No portfolio API, document viewer API, model gateway, assignment API, or approval mutation is called by the preview. Links to existing portal pages are labeled separately and remain subject to their existing authorization.

| Module | Implemented interface and local interactions | Integration still required |
| --- | --- | --- |
| Dashboard | Role preview, priority cards, queue, task navigation, sample activity chart | Authorized real portfolio and notifications |
| Cases | Search, status filter, case selection, progress, report section navigation | Case metadata, actual workflow progress, real report versions |
| Documents | Document selector, HTML reading-pane illustration, field provenance, quarantine state | Authorized PDF rendering, source coordinates, selection reasons, correction history |
| Approvals | Report section comparison, decision/rationale draft, validation, MFA links | Real review binding, MFA readiness, authorized decision submission, draft persistence |
| Transactions | Search, direction filter, correctly scoped sample totals, source detail | Real ledger, server pagination, date/amount filters, interactive relationship graph |
| Investigation assistant | Two labeled scripted answer examples and sample evidence navigation | Grounded retrieval, authorized citations, model gateway, streaming and validation |
| Agent monitor | Execution timeline, sample durations/retries, human review boundary | Actual trace, provider, token/cost telemetry, operational recovery controls |
| Policies & evaluations | Policy switcher, illustrative references, evaluation scenario checklist | Applicable versions, real comparison, evaluation results and promotion gates |
| Supervisor | Sample workload, assignment/date draft, local confirmation | Authorized ownership/deadline changes, escalation and delivery status |
| Security | Existing verification/passkey/email links, honest disconnected status | Session inventory, access history and readiness APIs |

## Open the preview

Use a separate checkout/worktree for this branch. Install the existing locked frontend dependencies with `npm ci` in `apps/web`, then run `npx next dev -p 3015` (or another unused port). Use the project's established **local** server-side authentication configuration and a separate preview session; never put signing keys, API tokens, passwords, or TOTP secrets into client code. `/workspace` redirects unauthenticated visitors through the existing sign-in flow.

Do not rebuild or replace the currently running portal merely to inspect this interface. The original portal's authentication includes local customizations; confirm the preview checkout matches those customizations before any eventual deployment. Use existing operational backup/rollback procedures for a later release.

After signing in, visit `/workspace` or choose **Workspace preview** in the existing header. Switching the preview role changes sample presentation only and grants no permissions. Draft state exists only in the mounted client component: refreshing clears it. Real MFA and approvals open through existing server-authorized routes.

## Delivery boundary

Only TypeScript compilation and the production frontend build are delivery checks for this UI phase. User acceptance, role/access regression, backend integration, document security, MFA, approval, and case lifecycle testing remain the next phase. Compilation does not establish production readiness or acceptance. Keep this work as a draft PR until reviewed.

The hosted build/type checks succeeded during preparation. Browser visual inspection was attempted but blocked by this environment's Chromium process-singleton socket restriction; desktop/mobile visual acceptance still requires review. No backend acceptance or integration suite was run for this UI-only change.
