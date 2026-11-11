"""Database privilege separation (prompt §24; SPR-035; docs/security/access-control.md).

* The migrator role (whoever runs Alembic) owns the schema.
* `finagent_app` - the role the API and worker connect as - may read and write application tables
  but has NO UPDATE/DELETE/TRUNCATE on append-only tables, NO DDL, and DELETE only where a table's
  lifecycle needs it. The append-only trigger on audit_events still applies to every role.

Every migration calls `apply_app_grants()` last, so new tables are never left ungranted or
over-granted. In deployed environments the role is pre-created by infrastructure (Entra/managed
identity login); migrations only create it (NOLOGIN) when it is absent, e.g. on a laptop.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from sqlalchemy import create_engine, make_url

APP_ROLE = "finagent_app"
APPEND_ONLY_TABLES: tuple[str, ...] = ("audit_events",)
# Immutable once written (P6+): reviewed content and decisions can only be superseded by NEW rows.
# A trigger (migration 0005) rejects UPDATE/DELETE for every role; the grants are defence in depth.
IMMUTABLE_TABLES: tuple[str, ...] = (
    "evidence_snapshots",
    "evidence_snapshot_items",
    "document_extractions",
    "report_versions",
    "report_claims",
    "approval_decisions",
    "model_requests",
    "agent_outputs",
    "evaluation_runs",
    "capability_incident_events",
    "connector_observations",
)
# Tables whose rows the application legitimately deletes (expiry/cleanup). Everything else: no DELETE.
DELETABLE_TABLES: tuple[str, ...] = (
    "idempotency_records",
    "queue_messages",
    "document_extraction_payloads",
    "document_field_payloads",
    "evidence_item_payloads",
)

_CREATE_ROLE_IF_ABSENT = """
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'finagent_app') THEN
        CREATE ROLE finagent_app NOLOGIN;
    END IF;
END
$$;
"""
if "'" + APP_ROLE + "'" not in _CREATE_ROLE_IF_ABSENT:  # keep the literal in step with APP_ROLE
    raise RuntimeError("APP_ROLE and _CREATE_ROLE_IF_ABSENT disagree")


def statements() -> list[str]:
    stmts = [
        _CREATE_ROLE_IF_ABSENT,
        f"REVOKE CREATE ON SCHEMA public FROM {APP_ROLE}",
        f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}",
        f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {APP_ROLE}",
        f"GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO {APP_ROLE}",
        f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE}",
        f"REVOKE ALL ON alembic_version FROM {APP_ROLE}",
        f"GRANT SELECT ON alembic_version TO {APP_ROLE}",
    ]
    # Earlier migrations run this too, before later tables exist: only touch tables that exist.
    stmts += [_if_exists(table, f"GRANT DELETE ON {table} TO {APP_ROLE}") for table in DELETABLE_TABLES]
    stmts += [f"REVOKE UPDATE, DELETE, TRUNCATE ON {table} FROM {APP_ROLE}" for table in APPEND_ONLY_TABLES]
    stmts += [
        _if_exists(table, f"REVOKE UPDATE, DELETE, TRUNCATE ON {table} FROM {APP_ROLE}")
        for table in IMMUTABLE_TABLES
    ]
    # Password provisioning/reset is a schema-owner operation, not an API permission.
    stmts += [
        _if_exists("password_credentials", f"REVOKE ALL ON password_credentials FROM {APP_ROLE}"),
        _if_exists(
            "password_credentials",
            f"GRANT SELECT, UPDATE (failed_attempts, locked_until) ON password_credentials TO {APP_ROLE}",
        ),
    ]
    # Only the credential administrator can issue, rotate or revoke connector credentials.
    stmts += [
        _if_exists("connector_credentials", f"REVOKE ALL ON connector_credentials FROM {APP_ROLE}"),
        _if_exists("connector_credentials", f"GRANT SELECT ON connector_credentials TO {APP_ROLE}"),
    ]
    stmts += [
        _if_exists("totp_factors", f"REVOKE ALL ON totp_factors FROM {APP_ROLE}"),
        _if_exists(
            "totp_factors",
            "GRANT SELECT, UPDATE (last_counter, failed_attempts, locked_until) "
            f"ON totp_factors TO {APP_ROLE}",
        ),
    ]
    stmts += [
        _if_exists("passkey_factors", f"REVOKE ALL ON passkey_factors FROM {APP_ROLE}"),
        _if_exists(
            "passkey_factors",
            f"GRANT SELECT, INSERT, UPDATE (sign_count, enabled, version) ON passkey_factors TO {APP_ROLE}",
        ),
    ]
    return stmts


def _if_exists(table: str, statement: str) -> str:
    return (
        f"DO $$ BEGIN IF to_regclass('public.{table}') IS NOT NULL THEN "
        f"EXECUTE '{statement}'; END IF; END $$;"
    )


def apply_app_grants(execute: Callable[[str], object]) -> None:
    for stmt in statements():
        execute(stmt)


_LOCAL_PASSWORD = re.compile(r"^[A-Za-z0-9_-]{16,128}$")


def enable_local_app_login(migrator_url: str, password: str, environment: str) -> None:
    """LOCAL/TEST ONLY: give finagent_app a password login so the app runs with production-like
    privileges on a laptop. Deployed environments use managed identities, never this."""
    if environment not in {"local", "test"}:
        raise RuntimeError("password logins for the app role are only allowed in local/test")
    if not _LOCAL_PASSWORD.fullmatch(password):
        raise ValueError("local app role password must be 16-128 chars of [A-Za-z0-9_-]")
    engine = create_engine(migrator_url)
    try:
        with engine.begin() as conn:
            # DDL cannot take bind parameters; the regex above makes inlining safe.
            conn.exec_driver_sql(f"ALTER ROLE {APP_ROLE} WITH LOGIN PASSWORD '{password}'")
    finally:
        engine.dispose()


def app_url(migrator_url: str, password: str) -> str:
    """Same host/database as `migrator_url`, authenticated as the application role."""
    return make_url(migrator_url).set(username=APP_ROLE, password=password).render_as_string(False)
