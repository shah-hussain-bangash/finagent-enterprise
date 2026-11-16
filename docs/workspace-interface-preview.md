# FinAgent workspace interface preview

This is a separate UI-first preview, not a production feature release. It adds an authenticated `/workspace` interface and a portal navigation entry. The existing portal, authorization, MFA, database, and deployed services remain separate.

## Current preview

The workspace provides an advanced-tools dashboard and illustrative screens for risk indicators and assessment drafts, transaction relationships, a scripted assistant, bilingual evidence, document provenance, an AI evaluation lab, AI security testing, release controls, agent monitoring, policy comparisons, and supervisor operations. The preview uses synthetic examples. Role selection changes presentation only and grants no permissions.

No preview risk analysis, AI request, evaluation, security test, notification, approval, assignment, deployment, or rollback is submitted to a backend. The assistant has two fixed responses; source links lead to sample screens. Bilingual switching uses fixed text, not translation or retrieval. The document viewer and field comparison are placeholders. Drafts exist only in the current page state and clear on refresh. Existing portal links remain subject to their own authorization.

The dashboard and other screens may link to existing portal workflows. Visual and accessibility review is ongoing; the presence of a screen or a successful typecheck does not establish that a feature is complete, secure, or production-ready.

## Open the preview

Use a separate checkout/worktree for this branch. Install the existing locked frontend dependencies with `npm ci` in `apps/web`, then run `npx next dev -p 3015` (or another unused port). Use the project's established **local** server-side authentication configuration and a separate preview session; never put signing keys, API tokens, passwords, or TOTP secrets into client code. `/workspace` redirects unauthenticated visitors through the existing sign-in flow.

Do not rebuild or replace the currently running portal merely to inspect this interface. The original portal's authentication includes local customizations; confirm the preview checkout matches those customizations before any eventual deployment. Use existing operational backup/rollback procedures for a later release.

After signing in, visit `/workspace` or choose **Workspace preview** in the existing header. Switching the preview role changes sample presentation only and grants no permissions. Draft state exists only in the mounted client component: refreshing clears it. Real MFA and approvals open through existing server-authorized routes.

## Delivery boundary

TypeScript checking and an isolated frontend production build passed. Selected desktop and mobile interactions have been inspected, including mobile keyboard menu navigation; full desktop, mobile, keyboard, and screen-reader acceptance remains incomplete. Document security, MFA, approval, and case lifecycle testing remain future work. Compilation does not establish production readiness or acceptance.

The original hosted build passed during preparation. The current local preview has since been inspected on desktop and mobile, but this is not complete acceptance. The connected risk view has limited checks described below.


## Connected recorded indicators

The workspace risk preview links to `/workspace/recorded-risk`, a separate read-only server-rendered view. It uses the existing authenticated case-list and case-agent-output APIs and relies on their case and tenant authorization. The connected page displays recognized recorded `AG-AML` indicator claims and explicitly referenced deterministic calculations, their original claim type and support status, output status, snapshot and run identifiers, timestamps, and calculation references. Other workspace screens remain illustrative.

This view does not perform detection or modify a case. Recorded outputs may be historical or refer to older evidence snapshots. An empty list does not mean that a case is safe or that detection ran. The three recognized indicator claim IDs and calculation IDs reflect the existing synthetic indicator provider; other provider outputs may not use them. Values are displayed as recorded, not recalculated. Calculation references are displayed as text and link to the existing evidence page, not directly to a transaction. All interpretations require human review.

Four focused indicator and calculation-selection checks, TypeScript, and an isolated frontend build passed. The preview redirected an unauthenticated request to sign-in; a signed-in analyst loaded an accessible case and its recorded outputs. One isolated API authorization test passed for the assigned analyst and denied an unassigned analyst, another tenant, an operator without case access, and an unauthenticated request. Complete frontend authorization testing across roles, positive indicator examples, evidence-link behavior, and full workflow acceptance remain outstanding. A build does not establish production readiness.
