# Access Control (summary)

Authoritative matrix: `src/finagent/security/authorization/policy.py` (`ROLE_PERMISSIONS`), derived
from SRS §2. Administrative roles (Tenant Admin, Policy Admin, Auditor, Platform Operator) hold **no
case-content permission** (SPR §3, DIR-003). Analysts see actively assigned cases; reviewers see cases
with an active reviewer assignment; supervisors see their own tenant (DEC-14 local default).

## Step-up, delegation, break-glass, quotas (ADR-0013)
- **MFA step-up (SPR-005):** `approval:decide`, `case:assign`, `tenant:admin`, `policy:publish`,
  `export:create`, `operation:reconcile` and `breakglass:activate` need an MFA-satisfied token
  (`amr`). Missing MFA returns `401 MFA_REQUIRED`. Values are configurable
  (`FINAGENT_MFA_STEP_UP_PERMISSIONS`); production cannot remove the privileged baseline.
- **Delegation (FR-003):** Tenant Admin only; one delegable role (ANALYST/REVIEWER by default);
  closed window up to 30 days (PROPOSED); void when revoked, expired, or the delegator loses the
  role. Endpoints: `POST/GET /api/v1/admin/delegations`, `POST /api/v1/admin/delegations/{id}/revoke`.
- **Break-glass (SPR-008):** disabled by default. When enabled: eligible `BREAK_GLASS` principal +
  MFA + justification -> read-only tenant case access for up to 60 minutes (PROPOSED), alerted,
  every read audited, reviewed afterwards by another Tenant Admin. Endpoints under
  `/api/v1/admin/break-glass`.
- **Quotas (NQ-028):** per-principal and per-tenant token buckets -> `429` + `Retry-After`.

## Database roles (ADR-0014) — implemented
- **Migrator** (`FINAGENT_MIGRATION_DATABASE_URL`): owns the schema; runs Alembic only.
- **`finagent_app`** (`FINAGENT_DATABASE_URL`): `SELECT/INSERT/UPDATE` on application tables;
  `DELETE` only on `idempotency_records`; **`INSERT/SELECT` only on `audit_events`**; no DDL.
  The append-only trigger additionally blocks UPDATE/DELETE on `audit_events` for every role.
- Grants are re-applied by every migration (`finagent.database.grants`). Locally,
  `make db-app-role` enables a password login for `finagent_app`; deployed environments use a
  managed identity (Phase 14, not verified).
