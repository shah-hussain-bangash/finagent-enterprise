# Local CI lint check

**Recorded:** 21 November 2026. **Revision checked:** `28803dc` on `fix/infra-validation`.
This is a local check, not a hosted CI result or a production approval.

The commands below match the lint task in `scripts/tasks.py` and the Makefile. With Ruff 0.17.0,
the lint check passed and the format check reported **292 files already formatted**.

```text
ruff check src tests scripts apps/worker apps/mock-bank
ruff format --check src tests scripts apps/worker apps/mock-bank
```

An earlier, broader `ruff check .` reported 83 findings in files outside this CI lint scope,
including `packages` and `typings`. That broader command is not the project's configured lint
task; its findings have not been resolved. The commands above do not certify those files.

The CI quality job also runs type checking and tests. A subsequent local attempt with a compatible
Python 3.12 runtime checked 191 source files but reported four missing `webauthn` imports because
that package was unavailable in the accessible environment. A focused check of the two new
database identity modules passed after correcting two type errors. Project-wide type checking
and hosted CI are **not verified**. Raw command output was not retained separately.
