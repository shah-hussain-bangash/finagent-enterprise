"""LOCAL backup/restore helper using the compose `postgres` container (no host pg_dump needed).

    python scripts/backup_restore.py backup
    python scripts/backup_restore.py restore var/backups/finagent-<utc>.dump [--target finagent_restore_check]

Restore always goes into a NEW database (refuses the live `finagent` database), runs migrations and
verifies every tenant's audit hash chain. Credentials are the local compose ones; nothing is printed.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DOCKER = ["docker", "compose", "exec", "-T", "postgres"]


def _run(argv: list[str], **kw: Any) -> None:
    subprocess.run(argv, check=True, cwd=ROOT, **kw)  # noqa: S603 - fixed argv built in this file


def backup() -> Path:
    out = ROOT / "var" / "backups" / f"finagent-{datetime.now(UTC):%Y%m%dT%H%M%SZ}.dump"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("wb") as fh:
        _run([*DOCKER, "pg_dump", "-U", "finagent", "-Fc", "finagent"], stdout=fh)
    print(f"backup written: {out} ({out.stat().st_size} bytes)")
    return out


def restore(dump: Path, target: str) -> None:
    if target in ("finagent", "postgres", "finagent_test") or not target.replace("_", "").isalnum():
        raise SystemExit("refusing this target; choose a NEW database name (letters, digits, underscore)")
    psql = [*DOCKER, "psql", "-U", "finagent", "-d", "postgres", "-v", "ON_ERROR_STOP=1", "-c"]
    _run([*psql, f'DROP DATABASE IF EXISTS "{target}"'])
    _run([*psql, f'CREATE DATABASE "{target}" OWNER finagent'])
    with dump.open("rb") as fh:
        _run([*DOCKER, "pg_restore", "-U", "finagent", "-d", target, "--no-owner"], stdin=fh)
    port = os.environ.get("FINAGENT_PG_HOST_PORT", "5432")
    url = f"postgresql+psycopg://finagent:finagent@127.0.0.1:{port}/{target}"
    env = {**os.environ, "FINAGENT_MIGRATION_DATABASE_URL": url, "FINAGENT_DATABASE_URL": url}
    _run([sys.executable, "-m", "alembic", "upgrade", "head"], env=env)
    from finagent.database.models import Tenant
    from finagent.database.session import session_factory, transaction
    from finagent.services.audit_service.writer import verify_chain

    with transaction(session_factory(url)) as s:
        for tenant in s.query(Tenant).all():
            ok = verify_chain(s, tenant.id)
            print(f"audit chain {tenant.slug}: {'OK' if ok else 'BROKEN'}")
            if not ok:
                raise SystemExit(1)
    print(f"restored into {target}; re-apply holds/deletions recorded after the backup before use")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("action", choices=["backup", "restore"])
    p.add_argument("dump", nargs="?")
    p.add_argument("--target", default="finagent_restore_check")
    a = p.parse_args()
    if a.action == "backup":
        backup()
    elif not a.dump:
        raise SystemExit("restore needs a dump file")
    else:
        restore(Path(a.dump), a.target)


if __name__ == "__main__":
    main()
