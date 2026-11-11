# ADR-0009: GitHub Actions as reference CI/CD

**Status:** Accepted (engineering) — engineering decision, not institutional approval.
**Date:** 2026-10-15

## Context
The prompt asks to choose one pipeline.

## Decision
GitHub Actions with a Postgres service container for integration tests; release-blocker marker job; Bandit, pip-audit, gitleaks. Azure DevOps equivalents can be derived later.

## Consequences
Pipeline runs in forks/PRs without Azure credentials.
