# ADR-0014: Database privilege separation (migrator vs application role)

**Status:** Accepted (engineering) — engineering decision, not institutional approval.
**Date:** 2026-10-15

## Context
Prompt §24 requires an append-only audit enforced in the database for application roles, and
`docs/security/access-control.md` designed a migrator/app split that was not implemented in the
starter. A trigger alone is weaker than a missing privilege: a role that owns the table can
disable the trigger.

## Decision
- Alembic runs as the **migrator** (schema owner): `FINAGENT_MIGRATION_DATABASE_URL`, falling
  back to `FINAGENT_DATABASE_URL` for single-role setups.
- The API and worker connect as **`finagent_app`**. Every migration ends with
  `finagent.database.grants.apply_app_grants()`, which re-derives the grants from scratch:
  no `CREATE` on the schema, `SELECT/INSERT/UPDATE` on application tables, `DELETE` only on an
  explicit allowlist (`idempotency_records`), `SELECT` only on `alembic_version`, and **no
  UPDATE/DELETE/TRUNCATE on append-only tables** (`audit_events`). The append-only trigger remains
  as a second layer for every role.
- Migrations create `finagent_app` as `NOLOGIN` only if it is absent; deployed environments
  pre-create it with managed-identity login. A password login is enabled only by
  `scripts/db_app_role.py` / `enable_local_app_login()`, which refuse outside local/test.
- The integration test suite runs the API as `finagent_app`, so every test also proves the grants
  are sufficient, and a release-blocker test proves UPDATE/DELETE/TRUNCATE on audit, DDL, trigger
  disabling and migration-table tampering are all denied.

## Consequences
Local behaviour now matches the intended production privilege model. Tests reset data with
`TRUNCATE` as the migrator. Azure PostgreSQL Entra authentication for `finagent_app` is
**not verified** (Phase 14 Terraform).
