"""Shared test configuration.

* `@pytest.mark.req("FR-008", ...)` records requirement IDs into the junit XML (ADR-0012).
  Unknown IDs fail collection, so tests cannot point at requirements that do not exist (ADR-0011).
* Integration tests need FINAGENT_TEST_DATABASE_URL (a disposable PostgreSQL database); they are
  skipped - and therefore reported as NOT RUN, never as passed - when it is absent.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
KNOWN_IDS = {
    r["id"] for r in json.loads((ROOT / "docs/requirements/requirements.json").read_text(encoding="utf-8"))
}
DEV_SECRET = "test-only-dev-secret-not-for-any-real-use-0123456789"
TEST_DB = os.environ.get("FINAGENT_TEST_DATABASE_URL")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    for item in items:
        for marker in item.iter_markers("req"):
            unknown = [i for i in marker.args if i not in KNOWN_IDS]
            if unknown:
                raise pytest.UsageError(f"{item.nodeid}: unknown requirement IDs {unknown}")
        if item.get_closest_marker("integration") and not TEST_DB:
            item.add_marker(pytest.mark.skip(reason="FINAGENT_TEST_DATABASE_URL not set - NOT RUN"))


@pytest.fixture(autouse=True)
def _record_requirements(request: pytest.FixtureRequest, record_property: object) -> None:
    ids = sorted({i for m in request.node.iter_markers("req") for i in m.args})
    if ids:
        record_property("requirements", ",".join(ids))  # type: ignore[operator]
    if request.node.get_closest_marker("release_blocker"):
        record_property("release_blocker", "true")  # type: ignore[operator]


# Roles are cluster-wide: reuse the developer's local password when set so running the tests does
# not break the dev database's finagent_app login on the same PostgreSQL instance.
APP_ROLE_TEST_PASSWORD = os.environ.get("FINAGENT_APP_DB_PASSWORD", "test-only-app-role-password-0001")


@pytest.fixture(scope="session")
def migrated_db() -> str:
    """Rebuild the disposable test schema, then migrate it to head."""
    assert TEST_DB

    from sqlalchemy import create_engine, text

    from finagent.database.grants import enable_local_app_login

    engine = create_engine(TEST_DB)

    with engine.begin() as conn:
        database = conn.execute(text("SELECT current_database()")).scalar_one()

        if database != "finagent_test":
            raise RuntimeError(f"REFUSING TO RESET DATABASE {database!r}")

        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))

    engine.dispose()

    env = {
        **os.environ,
        "FINAGENT_MIGRATION_DATABASE_URL": TEST_DB,
        "FINAGENT_DATABASE_URL": TEST_DB,
    }

    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
    )

    enable_local_app_login(TEST_DB, APP_ROLE_TEST_PASSWORD, "test")
    return TEST_DB


@pytest.fixture
def app_db_url(db_url: str) -> str:
    """The API under test connects as `finagent_app` - production-like privileges (prompt Â§24)."""
    from finagent.database.grants import app_url

    return app_url(db_url, APP_ROLE_TEST_PASSWORD)


@pytest.fixture
def db_url(migrated_db: str) -> Iterator[str]:
    from sqlalchemy import text

    from finagent.database.models import Base
    from finagent.database.seeds import seed
    from finagent.database.session import get_engine, session_factory, transaction

    tables = ", ".join(t.name for t in reversed(Base.metadata.sorted_tables))
    with get_engine(migrated_db).begin() as conn:
        conn.execute(text(f"TRUNCATE {tables} CASCADE"))
    with transaction(session_factory(migrated_db)) as s:
        seed(s)
    yield migrated_db


@pytest.fixture(scope="session")
def blob_root(tmp_path_factory: pytest.TempPathFactory) -> str:
    return str(tmp_path_factory.mktemp("blobs"))


@pytest.fixture
def make_settings(app_db_url: str, blob_root: str):  # type: ignore[no-untyped-def]
    from finagent.core.config import Settings

    def make(**overrides: object) -> Settings:
        overrides.setdefault("blob_local_root", blob_root)
        return Settings(  # type: ignore[arg-type]
            environment="test",
            auth_mode="local",
            database_url=app_db_url,
            dev_jwt_secret=DEV_SECRET,
            **overrides,
        )

    return make


@pytest.fixture
def settings(make_settings):  # type: ignore[no-untyped-def]
    return make_settings()


@pytest.fixture
def make_client(make_settings):  # type: ignore[no-untyped-def]
    """Build a client with setting overrides, e.g. make_client(break_glass_enabled=True)."""
    from fastapi.testclient import TestClient

    from finagent.api.main import create_app

    clients: list[TestClient] = []

    def make(**overrides: object) -> TestClient:
        c = TestClient(create_app(make_settings(**overrides)), raise_server_exceptions=False)
        c.__enter__()
        clients.append(c)
        return c

    yield make
    for c in clients:
        c.__exit__(None, None, None)


@pytest.fixture
def make_worker(make_settings):  # type: ignore[no-untyped-def]
    """Build an in-process worker (app DB role, in-memory notifier, PostgreSQL queue)."""
    from finagent.integrations.notifications.port import LocalNotificationAdapter
    from finagent.worker.service import Worker

    def make(*, notifier=None, faults=None, worker_id=None, model_provider=None, **overrides):  # type: ignore[no-untyped-def]
        kwargs = {"notifier": notifier or LocalNotificationAdapter(), "model_provider": model_provider}
        if faults is not None:
            kwargs["faults"] = faults
        if worker_id is not None:
            kwargs["worker_id"] = worker_id
        return Worker(make_settings(**overrides), **kwargs)

    return make


@pytest.fixture(scope="session")
def mock_bank_url() -> Iterator[str]:
    """The SYNTHETIC mock bank on a real local port (real HTTP, so timeouts behave like timeouts)."""
    import socket
    import threading
    import time

    import uvicorn
    from mockbank.app import app

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)


class MockBank:
    def __init__(self, url: str) -> None:
        import httpx

        self.url, self._http = url, httpx.Client(base_url=url, timeout=10)

    def fault(
        self, path_prefix: str, kind: str, value: str = "", times: int = 1, bank: str = "bank-a"
    ) -> None:
        r = self._http.post(
            "/_mock/faults",
            json={"bank": bank, "path_prefix": path_prefix, "kind": kind, "value": value, "times": times},
        )
        r.raise_for_status()

    def revoke(self, customer_ref: str, bank: str = "bank-a") -> None:
        self._http.post(
            "/_mock/revoke", params={"bank": bank, "customer_ref": customer_ref}
        ).raise_for_status()

    def state(self) -> dict:  # type: ignore[type-arg]
        return self._http.get("/_mock/state").json()  # type: ignore[no-any-return]

    def calls(self, path_contains: str, bank: str = "bank-a") -> int:
        return sum(1 for b, p in self.state()["calls"] if b == bank and path_contains in p)


@pytest.fixture
def bank(mock_bank_url):  # type: ignore[no-untyped-def]
    from finagent.integrations.factory import reset_breakers

    mb = MockBank(mock_bank_url)
    mb._http.post("/_mock/reset").raise_for_status()
    reset_breakers()
    return mb


@pytest.fixture
def client(settings):  # type: ignore[no-untyped-def]
    from fastapi.testclient import TestClient

    from finagent.api.main import create_app

    with TestClient(create_app(settings), raise_server_exceptions=False) as c:
        yield c


@pytest.fixture
def token():  # type: ignore[no-untyped-def]
    from finagent.security.authentication.tokens import LocalDevTokenValidator

    issuer = LocalDevTokenValidator(DEV_SECRET, "finagent-api")

    def make(subject: str, **kw: object) -> dict[str, str]:
        return {"Authorization": f"Bearer {issuer.issue(subject, **kw)}"}  # type: ignore[arg-type]

    return make
