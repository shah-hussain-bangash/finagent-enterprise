"""Cross-platform equivalent of the Makefile for hosts without GNU make (e.g. Windows).

Usage: python scripts/tasks.py <target> [<target> ...]   (same target names as the Makefile)
Reads `.env` into the environment first (existing environment variables win), so
`FINAGENT_DATABASE_URL` / `FINAGENT_TEST_DATABASE_URL` / compose host ports can be set there.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable
DEFAULT_DB = "postgresql+psycopg://finagent:finagent@127.0.0.1:5432/finagent"
DEFAULT_TEST_DB = "postgresql+psycopg://finagent:finagent@127.0.0.1:5432/finagent_test"


def load_env() -> None:
    env_file = ROOT / ".env"
    if env_file.exists():
        for raw in env_file.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    os.environ.setdefault("FINAGENT_DATABASE_URL", DEFAULT_DB)
    os.environ.setdefault("FINAGENT_TEST_DATABASE_URL", DEFAULT_TEST_DB)


def run(*argv: str, cwd: Path = ROOT) -> None:
    print("+", " ".join(argv), flush=True)
    code = subprocess.run(list(argv), cwd=cwd, check=False).returncode  # noqa: S603 - fixed argv
    if code:
        raise SystemExit(code)


def npm(*args: str) -> None:
    exe = shutil.which("npm") or shutil.which("npm.cmd")
    if exe is None:
        raise SystemExit("npm not found")
    run(exe, *args, cwd=ROOT / "apps/web")


def web_check() -> None:
    npm("run", "typecheck")
    npm("run", "build")


def setup() -> None:
    run(PY, "-m", "pip", "install", "-e", ".[dev]")
    if not (ROOT / ".env").exists():
        shutil.copy(ROOT / ".env.example", ROOT / ".env")


def test() -> None:
    (ROOT / "reports").mkdir(exist_ok=True)
    run(PY, "-m", "pytest", "--junitxml=reports/junit.xml")
    run(PY, "scripts/gen_traceability.py")


def lint() -> None:
    run(PY, "-m", "ruff", "check", "src", "tests", "scripts", "apps/worker", "apps/mock-bank")
    run(PY, "-m", "ruff", "format", "--check", "src", "tests", "scripts", "apps/worker", "apps/mock-bank")


def fmt() -> None:
    run(PY, "-m", "ruff", "format", "src", "tests", "scripts", "apps/worker", "apps/mock-bank")
    run(PY, "-m", "ruff", "check", "--fix", "src", "tests", "scripts", "apps/worker", "apps/mock-bank")


def security() -> None:
    run(PY, "-m", "bandit", "-q", "-r", "src")
    run(PY, "-m", "pip_audit", "--progress-spinner", "off", "--skip-editable")


def openapi() -> None:
    out = subprocess.run(  # noqa: S603 - fixed argv
        [PY, "scripts/export_openapi.py"], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout
    (ROOT / "docs/api/openapi.json").write_text(out, encoding="utf-8", newline="\n")


TARGETS: dict[str, Callable[[], None]] = {
    "setup": setup,
    "deps-up": lambda: run("docker", "compose", "up", "-d", "postgres", "redis"),
    "migrate": lambda: run(PY, "-m", "alembic", "upgrade", "head"),
    "db-app-role": lambda: run(PY, "scripts/db_app_role.py"),
    "seed": lambda: run(PY, "-m", "finagent.database.seeds"),
    "test": test,
    "test-unit": lambda: run(PY, "-m", "pytest", "tests/unit"),
    "test-integration": lambda: run(PY, "-m", "pytest", "tests/integration"),
    "test-security": lambda: run(PY, "-m", "pytest", "tests/security"),
    "test-recovery": lambda: run(PY, "-m", "pytest", "tests/recovery"),
    "test-release-blockers": lambda: run(PY, "-m", "pytest", "-m", "release_blocker"),
    "test-browser": lambda: run(
        PY, "-m", "pytest", "-m", "browser", "tests/browser", "--junitxml=reports/junit-browser.xml"
    ),
    "mutation-check": lambda: run(PY, "scripts/mutation_check.py"),
    "lint": lint,
    "format": fmt,
    "typecheck": lambda: run(PY, "-m", "mypy"),
    "security": security,
    "openapi": openapi,
    "web-check": web_check,
    "down": lambda: run("docker", "compose", "down"),
}


def main(argv: list[str]) -> int:
    if not argv or any(a not in TARGETS for a in argv):
        print("targets:", ", ".join(TARGETS), file=sys.stderr)
        return 2
    load_env()
    for target in argv:
        TARGETS[target]()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
