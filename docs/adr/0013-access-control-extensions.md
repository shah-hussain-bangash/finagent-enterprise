# ADR-0013: MFA step-up, delegation, break-glass and request quotas

**Status:** Accepted (engineering) — engineering decision, not institutional approval.
**Date:** 2026-10-15

## Context
SPR-005 (MFA for privileged and reviewer operations), FR-003 (explicit, time-bounded, auditable
delegation), SPR-008 (break-glass) and NQ-028 (tenant/workload quotas) are mandatory, but the
institutional parameters are OPEN: the authentication policy (SEC-D02), the reviewer matrix and
delegation rules (DEC-06), break-glass scope (SEC-D03) and quota values (NFR-D05).

## Decision
1. **Step-up is a property of a permission, not of an endpoint.** `require()` checks the
   permission first (so a missing role returns 403 with no MFA oracle), then returns
   `401 MFA_REQUIRED` with `WWW-Authenticate: Bearer error="insufficient_user_authentication"`
   (RFC 9470 semantics) when the permission is in the step-up set and the token's `amr` shows
   no MFA. The step-up set lives in `Settings.mfa_step_up_permissions`; outside local/test it
   cannot drop the privileged baseline (decide, tenant admin, break-glass, reconcile).
2. **Delegation** is created only by a Tenant Administrator (MFA), names one delegable role
   (default ANALYST/REVIEWER; administrative, audit, emergency and integration roles are never
   delegable), and has a closed window of at most `delegation_max_days`. The resolver honours it
   only while the window is open, it is not revoked, and the delegator is still active and
   still holds the role **directly** (no transitive delegation). Row scoping then covers the
   delegator's assignments; audit details carry the delegation id. Separation-of-duties checks
   compare principal ids, so delegation never lets someone review their own submission.
3. **Break-glass** is disabled by default (`break_glass_enabled=false`). When enabled, an
   eligible principal (`BREAK_GLASS` role, MFA) activates a grant with a written justification
   for at most `break_glass_max_minutes`. The resolver adds the resolver-only `EMERGENCY_READ`
   role (tenant-wide `case:read` only) while the grant is active, the feature is enabled and
   the session is MFA-satisfied. Database role rows can never grant `EMERGENCY_READ`. Activation
   emits an outbox security event and a `security_alert` log line; every read under a grant is
   audited with the grant id; a different Tenant Administrator records the post-event review.
   It never carries approval, submission, update, assignment or export permissions.
4. **Quotas** are token buckets per principal and per tenant, charged on every authenticated
   request (`charge()` lets expensive operations charge more). The local default is an
   in-process limiter; the Redis adapter (atomic Lua, tenant-prefixed keys, TTL) is used when
   several replicas run. When Redis is unreachable the limiter degrades to the in-process
   bucket: rate limiting is an availability control and authorization never depends on it.

## Consequences
All four controls work and are tested locally with conservative defaults; none of the values is
institutionally approved. Live Entra `amr`/`acr` claims and a shared Redis in a deployed
environment are **not verified**.
