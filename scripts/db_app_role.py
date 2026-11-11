"""LOCAL ONLY: enable the `finagent_app` login so the API/worker run with production-like
privileges (no DDL; INSERT/SELECT only on audit_events). Run after `migrate`.

Needs FINAGENT_MIGRATION_DATABASE_URL (schema owner), FINAGENT_APP_DB_PASSWORD and
FINAGENT_ENVIRONMENT=local|test. Prints the application URL to put in FINAGENT_DATABASE_URL.
"""

from __future__ import annotations

import os

from finagent.database.grants import app_url, enable_local_app_login


def main() -> None:
    migrator = os.environ["FINAGENT_MIGRATION_DATABASE_URL"]
    password = os.environ["FINAGENT_APP_DB_PASSWORD"]
    enable_local_app_login(migrator, password, os.environ.get("FINAGENT_ENVIRONMENT", ""))
    print("finagent_app login enabled; FINAGENT_DATABASE_URL should be:")
    print(app_url(migrator, "<FINAGENT_APP_DB_PASSWORD>"))


if __name__ == "__main__":
    main()
