"""Experimental token callback for new Azure PostgreSQL connections.

This component is not wired into an engine or enabled in any deployment.
"""

from __future__ import annotations

from typing import Any, Protocol

POSTGRES_SCOPE = "https://ossrdbms-aad.database.windows.net/.default"


class TokenCredential(Protocol):
    def get_token(self, *scopes: str, **kwargs: Any) -> Any: ...


class PostgresTokenCallback:
    """Insert a freshly requested token when SQLAlchemy creates a DBAPI connection."""

    def __init__(self, credential: TokenCredential) -> None:
        self._credential = credential

    def __call__(self, dialect: Any, connection_record: Any, cargs: Any, cparams: dict[str, Any]) -> None:
        try:
            token = self._credential.get_token(POSTGRES_SCOPE).token
            if not isinstance(token, str) or not token:
                raise ValueError("empty token")
        except Exception:
            # Credential errors may contain sensitive details; do not forward them into logs.
            raise RuntimeError("Could not obtain a PostgreSQL access token") from None
        cparams["password"] = token
