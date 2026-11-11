# Security Policy

FinAgent Enterprise is pre-release software using **synthetic data only**. No penetration test,
certification or regulatory approval has been performed or obtained.

## Reporting a vulnerability
Report privately to the repository owners (security contact: OPEN - to be named under SEC-D10).
Do not open public issues for vulnerabilities. Include affected commit, reproduction steps and impact.

## Release-blocking classes (prompt §31, SPR §13)
Unauthorized disclosure or action, cross-tenant access, approval bypass, duplicate protected
external effect, required audit gap, accepted unsupported material finding, critical exploitable
vulnerability. Any one blocks release regardless of other results.

## Controls in this codebase (status in docs/implementation-status.md)
Deny-by-default authorization; tenant from server-side membership only; composite tenant foreign
keys; concealing 404s; append-only hash-chained audit enforced by a database trigger; fail-closed
configuration (local auth cannot start outside local/test); redacted structured logs; request size
limits; CI SAST (Bandit), dependency audit (pip-audit, npm audit) and secret scanning (gitleaks).

## Secrets
Never commit secrets. `.env` is for local development only and is git-ignored. Deployed
environments use Azure Key Vault with managed identity (Phase 14).
