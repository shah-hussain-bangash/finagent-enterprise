# Google web sign-in

Status as of 2026-10-23: local Google sign-in and runtime checks completed.

Real Google account verification, controlled local PLATFORM_OPERATOR
provisioning and all seven manual browser checks completed successfully.
The web authentication suite passed 69 tests, and the backend Google token
suite passed 24 tests. Both commands returned exit code 0.

Web TypeScript checking and the Next.js 15.5.28 production build also passed
on Windows with Node.js 24.21.0. These are host validation results.
See the dated evidence table in `docs/implementation-status.md`.

The automated authentication suites use synthetic credentials and mocked
provider/key responses. Real Google sign-in was verified separately.
Google MFA-protected actions remain blocked.

Subsequent verification: the full backend regression is recorded below.
The referenced conversation also records the separate PostgreSQL identity and
revocation check: seven scenarios passed, test and isolated runner exit codes 0,
and temporary PostgreSQL container cleanup confirmed.

Local Google runtime follow-up completed on 2026-10-23. A controlled
real-browser server-session-expiry test shortened the identified Google Redis
session TTL to 15 seconds; after expiry, the protected application redirected
to `/signin?expired=1` and displayed the expected expired-session message.
This verifies server-side expiry enforcement but is not a natural-duration
approximately one-hour expiry observation.

Docker `api`, `web` and `worker` images were rebuilt from the Google Compose
stack and recreated successfully. Post-rebuild API Docker health, web runtime,
worker runtime, real Google login, Operations access, PLATFORM_OPERATOR case
restriction and application sign-out all passed. Real Microsoft Entra sign-in,
natural-duration session-expiry observation, qualified Google MFA step-up and
production acceptance remain separate pending work.
Earlier test results retain their historical dates and scope.

The existing local and Entra modes remain available. No provider is enabled by installation.
All source requirements remain DRAFT; passing tests do not approve a production release.

## Google runtime verification - 2026-10-23

The controlled post-sign-in expiry and rebuilt-container checks passed:

- controlled real-browser server-session expiry: PASSED;
- redirect to `/signin?expired=1`: PASSED;
- expired-session message: PASSED;
- Docker image rebuild for `api`, `web`, and `worker`: PASSED;
- rebuilt API runtime and configured `/health/live` Docker healthcheck: PASSED;
- rebuilt web runtime: PASSED;
- rebuilt worker runtime with restart count 0: PASSED;
- real Google login after rebuild: PASSED;
- Operations dashboard access for `PLATFORM_OPERATOR`: PASSED;
- case-content restriction for `PLATFORM_OPERATOR`: PASSED;
- application sign-out: PASSED.

The expiry test deliberately shortened the identified Redis session TTL to
15 seconds. It verifies the application's real-browser behavior after
server-side expiry; it does not represent a natural approximately one-hour
session-duration observation.
## Registration and credentials

Use a dedicated Google OAuth **Web application** client for this FinAgent web/API pair.
For the local test origin, register exactly:

`http://127.0.0.1:3001/auth/google/callback`

Authorized JavaScript origins may remain empty: this is a server-side authorization-code flow.
Keep the Google app External / Testing and add the intended test accounts under Audience.
Do not enable the AI-powered-agent option for this human sign-in client.
Replace any secret exposed in chat or screenshots and disable/delete the exposed value.
Store the replacement only on the server. Never commit credentials or upload the credential JSON.

## Authentication and authorization

POST `/auth/google/start` requires the configured Origin and API/web mode agreement.
The code flow requests only `openid email profile`, with state, nonce and S256 PKCE.
A random HttpOnly, SameSite=Lax transaction cookie is consumed once through Redis GETDEL.
The callback is fixed; arbitrary return destinations are ignored. Provider errors are redacted.
Discovery is fixed to `https://accounts.google.com`; authorization, token and JWKS endpoints
are checked against Google's known exact endpoints. ID token signatures are verified with RS256.

The web callback sends the verified **ID credential**, not the Google API access token, to
FinAgent's existing `/api/v1/auth/session` admission endpoint. The backend independently verifies
signature, exact Google issuer, exact dedicated web-client audience, authorised party when present,
subject, issue/expiry times and bounded lifetime. Configure the same client ID on API and web.
This is the first-party backend ID-credential pattern; it is not delegated Google API authorization.
Do not reuse this web client for unrelated applications.

The trusted identity is `(https://accounts.google.com, sub)`. The stable Google `sub` is not an email.
Existing database mappings, active tenant/principal checks, assigned roles and case scope remain the
only authority. No automatic registration, email-based linking, tenant selection from token claims,
role import, or automatic promotion to an administrator is implemented. Integration principals are
rejected in Google mode. Unknown identities cannot create a web session.
An administrator must approve the intended tenant, human principal and roles and provision a mapping
through the project's controlled identity-management process before the account can enter FinAgent.
Adding an email to Google's test-user list does not provision that FinAgent mapping.
Do not overwrite a synthetic identity's issuer/subject to enable a real account in the development DB.

Google login always yields `mfa_satisfied=False` in this implementation, even when an ID token contains
an `amr` claim. No qualified Google step-up contract has been approved. Existing MFA-protected actions
remain blocked. Do not remove MFA permissions to make a Google test pass.

## Sessions

The browser receives only an opaque random handle. The ID credential is AES-256-GCM encrypted in
Redis, bound to its full storage key, for at most its expiry or one hour. Google access and refresh
tokens are discarded. No `offline_access`, automatic refresh or browser token storage is used.
Reauthentication is required at expiry. Reauthentication rotates and revokes the previous presented
application handle. Logout revokes the server-side record; it does not sign out of Google itself.
The ID credential remains a bearer credential until its expiry; application logout is not provider
credential revocation. Database suspension is rechecked on every API request.

Google uses a separate Redis namespace and cookies (`fa_google_flow`, `fa_google_session`). HTTPS
adds the `__Host-` prefix, Secure and host-only root-path cookies. Entra namespace and cookie names
remain compatible. Redis/store failure denies sign-in; no in-memory or synthetic fallback exists.
Production requires HTTPS for web/API and TLS Redis, along with all existing deployed settings.
Do not log authorization codes, credentials, tokens, callback query strings or raw provider errors.
Review ingress/proxy access-log query redaction before any deployed use.

## Local configuration after regression checks

Installation changes code, tests and this guide only. It does not edit `.env`, change running services,
provision users, make Google requests, migrate databases or perform business actions.
No dependency changes or database migrations are introduced.

For host processes set, privately:

```dotenv
FINAGENT_AUTH_MODE=google
FINAGENT_GOOGLE_WEB_CLIENT_ID=YOUR_WEB_CLIENT_ID.apps.googleusercontent.com
FINAGENT_WEB_AUTH_MODE=google
FINAGENT_GOOGLE_WEB_CLIENT_SECRET=YOUR_PRIVATE_REPLACEMENT_SECRET
FINAGENT_WEB_ORIGIN=http://127.0.0.1:3001
FINAGENT_WEB_AUTH_REDIS_URL=redis://127.0.0.1:56379/2
FINAGENT_API_URL=http://127.0.0.1:8001
```

Keep `FINAGENT_ENVIRONMENT=local` for this local setup and retain the existing strong
`FINAGENT_WEB_SESSION_SECRET`. Never substitute example values into an actual deployment.
For Docker, `compose.google.example.yaml` is an optional explicit override. It uses internal
`http://api:8000` and `redis://redis:6379/2`; browser callbacks still use `127.0.0.1:3001`.
Do not use the Entra and Google overrides together. Add the Google client variables privately to
`.env` without changing the base mode until the configuration and identity mapping are reviewed.

```powershell
docker compose -f compose.yaml -f compose.google.example.yaml --profile app up -d --build --no-deps api worker web
```

Use the actual base Compose filename if your repository uses a different one. Worker receives the
same Google auth mode/client setting, but does not run the web login callback. Compose may retain
containers' previous environment when an override is omitted: to return to local, check `.env`
settings and recreate API/worker/web with the original base Compose configuration.

## Verification

### Full backend regression - 2026-10-22

The completed full backend regression ran against an isolated temporary
PostgreSQL 16 instance. Evidence: the completed runner output reported in the
referenced "Prompt Engineering For Codex" conversation; tests were not rerun
for this documentation update.

| Result | Value |
|---|---|
| Passed | 406 |
| Skipped | 2 |
| Deselected | 1 |
| Passing subtests | 15 |
| Pytest exit code | 0 |
| Isolated runner exit code | 0 |
| Temporary PostgreSQL container | Removed successfully |

Recorded skips:

1. `tests/unit/test_access_policy.py:117`: `FINAGENT_TEST_REDIS_URL`
   was not set.
2. `tests/unit/test_parser_network_isolation.py:32`: Linux seccomp
   runtime test is not applicable to the Windows host.

Skipped and deselected tests are not counted as passes. This backend result
does not establish full browser-session expiry or rebuilt-container behavior.

### Verification commands and scope

New tests cover real offline RSA signatures, exact audience/issuer/subject validation, false MFA,
PKCE/nonce/state, callback replay, ID/access-token separation, API admission failure, discovery
endpoint restrictions, cookie names and mode mismatch. Existing Entra web tests continue to run.
The Redis test also checks that identical handles cannot cross Google/Entra namespaces.
Seven PostgreSQL integration scenarios cover active/unknown identities, wrong audience, suspended/
removed principals, suspended tenants and integration rejection, with revocation on the next request.

Run from the project root in PowerShell, checking each exit code before continuing:

```powershell
Push-Location apps/web
npm run test:auth
# Optional: test:auth:redis needs a local test Redis URL set in the current process.
Pop-Location
.\.venv\Scripts\python.exe scripts/tasks.py lint typecheck test-unit
.\.venv\Scripts\python.exe scripts/tasks.py deps-up test
.\.venv\Scripts\python.exe scripts/tasks.py web-check
.\.venv\Scripts\python.exe scripts/tasks.py test-browser
.\.venv\Scripts\python.exe scripts/tasks.py openapi
.\.venv\Scripts\python.exe scripts/gen_traceability.py
```

These project checks run with the normal local/test fixtures before enabling Google. The existing
browser journey exercises local identities, not a real Google account. Real Google verification
additionally requires private server configuration, trusted principal mapping, rebuilt containers,
login/logout/expiry checks and confirmation that MFA-protected actions remain blocked.
Production security review, provider policy acceptance, secrets lifecycle, account recovery, MFA
step-up, log redaction and deployment acceptance remain separate work. Google setup does not verify
Microsoft Entra live sign-in or any other identity provider.

References:
- https://developers.google.com/identity/gsi/web/guides/verify-google-id-token
- https://developers.google.com/identity/protocols/oauth2/web-server
- https://developers.google.com/identity/openid-connect/openid-connect

## Runtime configuration follow-up - 2026-10-26

The local Google authentication flow was revalidated after a Docker web
runtime configuration mismatch was found.

The API correctly reported Google mode, but the web container initially did
not receive `FINAGENT_WEB_AUTH_MODE`, `FINAGENT_ENVIRONMENT`,
`FINAGENT_WEB_ORIGIN`, `FINAGENT_WEB_AUTH_REDIS_URL`,
`FINAGENT_GOOGLE_WEB_CLIENT_ID` or
`FINAGENT_GOOGLE_WEB_CLIENT_SECRET`.

The Compose web service now passes those settings explicitly. The recreated
web container verified:

- `FINAGENT_WEB_AUTH_MODE=google`
- `FINAGENT_ENVIRONMENT=local`
- `FINAGENT_WEB_ORIGIN=http://127.0.0.1:3001`
- Redis-backed web authentication configuration present
- Google client ID configured
- Google client secret configured
- API authentication mode `google`
- `/signin` HTTP 200
- Google sign-in UI available

This is a local runtime verification. It does not constitute production
identity-provider acceptance, qualified MFA acceptance or Microsoft Entra
acceptance.
