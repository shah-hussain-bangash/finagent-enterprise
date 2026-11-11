"""Print the OpenAPI document without a database or real secrets (used by `make openapi`)."""

from __future__ import annotations

import json

from finagent.api.main import create_app
from finagent.core.config import Settings

settings = Settings(
    environment="test",
    auth_mode="local",  # type: ignore[arg-type]
    database_url="postgresql+psycopg://unused@localhost/unused",  # type: ignore[arg-type]
    dev_jwt_secret="openapi-export-only-not-a-secret-0123456789",
)  # type: ignore[arg-type]
print(json.dumps(create_app(settings).openapi(), indent=2, sort_keys=True))
