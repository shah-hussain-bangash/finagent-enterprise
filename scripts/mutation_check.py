"""Control mutation checks (CLAUDE.md "break it, confirm a test fails, restore it").

Each entry in tests/mutations.yaml names a source file, an exact snippet that implements a control,
a replacement that disables it, and the tests expected to catch it. For each mutation the file is
patched, the tests run, and the original is ALWAYS restored (even on Ctrl-C). A mutation that no
test catches ("SURVIVED") makes the script exit non-zero.

Usage: python scripts/mutation_check.py [mutation-id ...]
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def main(selected: list[str]) -> int:
    mutations = yaml.safe_load((ROOT / "tests/mutations.yaml").read_text(encoding="utf-8"))
    if selected:
        mutations = [m for m in mutations if m["id"] in selected]
    survived: list[str] = []
    for m in mutations:
        path = ROOT / m["file"]
        original = path.read_text(encoding="utf-8")
        if original.count(m["find"]) != 1:
            print(f"[ERROR]    {m['id']}: snippet not found exactly once in {m['file']}")
            survived.append(m["id"])
            continue
        try:
            path.write_text(original.replace(m["find"], m["replace"]), encoding="utf-8")
            proc = subprocess.run(  # noqa: S603 - fixed argv
                [sys.executable, "-m", "pytest", "-q", "-p", "no:warnings", "-x", *m["tests"]],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
        finally:
            path.write_text(original, encoding="utf-8")
        # pytest exit 1 = tests failed (the control is tested). 0 = survived. >=2 = the mutation
        # broke collection/imports, which proves nothing about the control -> reported as ERROR.
        caught = proc.returncode == 1
        summary = (proc.stdout.strip().splitlines() or ["(no output)"])[-1]
        status = "CAUGHT" if caught else ("SURVIVED" if proc.returncode == 0 else "ERROR")
        print(f"[{status:8}] {m['id']}: {m['control']} -> {summary}")
        if not caught:
            survived.append(m["id"])
    print(f"\n{len(mutations) - len(survived)}/{len(mutations)} mutations caught")
    return 1 if survived else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
