"""Alembic environment. Runs as the MIGRATOR role (schema owner), never as the application role.

URL: FINAGENT_MIGRATION_DATABASE_URL, falling back to FINAGENT_DATABASE_URL for single-role local
setups. Neither is ever committed.
"""

from __future__ import annotations

import os

from alembic import context
from sqlalchemy import create_engine

from finagent.database.models import Base

target_metadata = Base.metadata


def run_migrations_online() -> None:
    url = os.environ.get("FINAGENT_MIGRATION_DATABASE_URL") or os.environ["FINAGENT_DATABASE_URL"]
    engine = create_engine(url)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
