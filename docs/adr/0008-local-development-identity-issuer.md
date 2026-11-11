# ADR-0008: Local development identity issuer

**Status:** Accepted (engineering) — engineering decision, not institutional approval.
**Date:** 2026-10-15

## Context
The stack must run without Entra ID, but dev auth must never activate in production (prompt §16).

## Decision
HS256 tokens signed with a dev secret, issued by `scripts/dev_token.py`. Settings validation refuses `auth_mode=local` unless `environment in {local,test}`; tokens carry only subject + issuer — tenant and roles are always resolved from the database.

## Consequences
No live credentials needed; production misconfiguration fails at startup (tested).
