"""Build a source ZIP for hand-over: no secrets, no virtualenvs, no node_modules, caches or runtime data.

    python scripts/package_source.py [output.zip]

Excludes .env (and every .env.* except .env.example), .venv, node_modules, .next, caches, var/ (blobs,
backups, notifications), reports/ (regenerate with tasks.py test), *.pyc and Terraform state. Prints a
summary and refuses to finish if an excluded pattern slipped in.
"""

from __future__ import annotations

import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_DIRS = {
    ".venv",
    "node_modules",
    ".next",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "var",
    "reports",
    ".terraform",
    ".git",
    "dist",
    "build",
}
EXCLUDED_SUFFIXES = {".pyc", ".pyo", ".tfstate", ".log", ".dump"}


def included(path: Path) -> bool:
    rel = path.relative_to(ROOT)
    if any(part in EXCLUDED_DIRS or part.endswith(".egg-info") for part in rel.parts):
        return False
    if path.name.startswith(".env") and path.name != ".env.example":
        return False
    return path.suffix not in EXCLUDED_SUFFIXES and ".tfstate" not in path.name


def main() -> None:
    out = (
        Path(sys.argv[1])
        if len(sys.argv) > 1
        else ROOT / "dist" / f"finagent-enterprise-src-{datetime.now(UTC):%Y%m%d}.zip"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    files = sorted(p for p in ROOT.rglob("*") if p.is_file() and included(p) and p.resolve() != out.resolve())
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in files:
            zf.write(p, Path("finagent-enterprise") / p.relative_to(ROOT))
    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
    leaked = [
        n
        for n in names
        if ("/.env" in n and not n.endswith(".env.example")) or "/node_modules/" in n or "/.venv/" in n
    ]
    if leaked:
        raise SystemExit(f"refusing: excluded content in archive: {leaked[:5]}")
    print(f"wrote {out} with {len(names)} files ({out.stat().st_size // 1024} KiB)")


if __name__ == "__main__":
    main()
