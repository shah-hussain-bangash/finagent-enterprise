"""LOCAL/TEST ONLY: enable the `finagent_app` login (production-like privileges) after migrations.

Usage: python -m finagent.database.app_role   (needs FINAGENT_MIGRATION_DATABASE_URL,
FINAGENT_APP_DB_PASSWORD and FINAGENT_ENVIRONMENT=local|test). Deployed environments use managed
identities and never call this (ADR-0014). Never prints the password.
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
