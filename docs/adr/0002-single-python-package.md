# ADR-0002: Single Python package mapped from the prompt's monorepo tree

**Status:** Accepted (engineering) — engineering decision, not institutional approval.
**Date:** 2026-10-15

## Context
The prompt's tree places `security/`, `database/`, `services/`, `agents/` etc. as top-level folders. As separate top-level Python packages these collide with third-party names and need sys.path manipulation.

## Decision
All backend code lives in one installable package `src/finagent/` whose subpackages keep the prompt's names and boundaries (`finagent.security.authorization`, `finagent.services.case_service`, ...). Non-Python folders (`infra/`, `monitoring/`, `docs/`, `apps/web`) stay at the root. `apps/api` and `apps/worker` are container entrypoints only. Rule: a service module may only write its own tables.

## Consequences
Simpler imports, one mypy/ruff config, one test run. Boundaries are enforced by review and an import-linter contract (to add in Phase 13).
