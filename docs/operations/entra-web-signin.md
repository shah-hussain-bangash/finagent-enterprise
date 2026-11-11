# Microsoft Entra web sign-in

Status: implementation prepared and isolated checks passed on 2026-10-21.
Full Windows project regression, real Entra authentication, Conditional Access/MFA acceptance
and deployment verification remain pending. All source requirements remain DRAFT.

## Behavior

Local development sign-in remains the default. Installing these files does not edit `.env`,
switch authentication modes, register an application, provision a user, migrate a database,
restart a service, or contact Microsoft.

Entra mode adds a server-side authorization-code flow with PKCE S256, random state and nonce,
an exact configured callback and a single tenant authority. `openid-client` validates the
ID token, including RS256 signature, issuer, audience, expiry and nonce. The callback also
requires the configured tenant ID and an `oid`. Microsoft Graph tokens and ID tokens are
not used as FinAgent API bearer tokens.

The API independently validates the access token and resolves the existing ACTIVE human
principal by `(issuer, oid)`. The callback identity endpoint additionally requires the
configured delegated API scope. Token roles, groups, email, tenant input fields and local
MFA form choices never provision Entra access. Unknown/suspended/removed users, suspended
tenants and integration identities cannot establish a web session.

The web and API modes must agree. An unavailable API or mode mismatch never falls back
to synthetic identities in Entra mode.

## Sessions and callback controls

- POST `/auth/entra/start` requires an Origin matching `FINAGENT_WEB_ORIGIN`.
- GET `/auth/entra/callback` consumes a five-minute login transaction atomically with Redis
  GETDEL. State, nonce and PKCE verifier are bound to that transaction and its browser cookie.
- Discovery, token and JWKS requests use the fixed Microsoft public-cloud authority.
  Other Entra clouds, B2C/External ID and `common`/`organizations` authorities are not supported.
- Browser cookies contain random opaque handles. Token/transaction records are encrypted
  with AES-256-GCM, bound to their Redis key and namespaced by Entra tenant and web client.
- Cookies are HttpOnly, SameSite=Lax and host-only with Path=/; HTTPS uses the `__Host-`
  prefix and Secure. HTTP cookies are allowed only for local/test loopback web origins.
  Lax permits the top-level GET redirect from Microsoft. Existing local-cookie behavior is retained.
- A successful callback rotates the application session and removes the prior presented session.
  Session lifetime is bounded by the access-token response, ID-token expiry and one hour.
- ID and refresh tokens are not retained. No offline_access scope or silent refresh is used.
  The user signs in again at expiry. API identity/role checks still run on every API request.
- Sign out revokes the Redis application session before clearing its cookie. A store failure
  is not reported as successful revocation. Sign out does not terminate Microsoft's SSO session
  or revoke provider-issued access tokens; immediate application access revocation remains
  available through the trusted FinAgent principal/tenant controls.
- No provider errors, callback codes, tokens, secrets or Redis connection strings are logged
  by this implementation. Callback responses use no-store and no-referrer. Configure ingress
  access logs to redact the callback query string as well.

Redis is a required private session store in Entra mode. There is no process-memory or legacy
development-cookie fallback when it is unavailable. Use Redis 6.2 or later (the local stack
uses Redis 7), appropriate access controls and capacity limits, and TLS outside local/test.
Losing these short-lived Redis records signs users out; sessions do not require database migration.
Provider and proxy/API header-size limits still apply to bearer tokens even though browser
cookies no longer carry tokens. Access-token records are bounded to 24 KiB.

## MFA boundary

For Entra access tokens, only an explicit `mfa` entry in the validated `amr` array satisfies
the existing MFA flag. A single `otp`, `rsa`, `fido` or other method is not automatically
treated as multiple-factor evidence. Missing or malformed evidence leaves MFA false.
The local synthetic test issuer retains its existing behavior.

Do not assume Entra v2 tokens will contain suitable MFA evidence. Privileged operations may
remain blocked with MFA_REQUIRED even after successful sign-in. Conditional Access, token
claim availability and any authentication-context integration need separate security-owner
design and live acceptance. Do not bypass the API check or copy MFA status from an ID token
or a browser field to make an operation pass.

## App registration and configuration

Use two single-tenant application registrations managed by the identity owner:

1. FinAgent API: expose a delegated scope, initially named `access_as_user`. Configure v2
   access-token issuance (`api.requestedAccessTokenVersion = 2`). The API audience is this
   application's client ID, distinct from the web client's ID.
2. FinAgent web: a confidential Web application with the exact redirect URI
   `<FINAGENT_WEB_ORIGIN>/auth/entra/callback`. Add the delegated FinAgent API permission
   and obtain any required consent. Use a managed secret lifecycle for the web credential.
   Do not enable implicit grants or public-client flows for this implementation.
3. Provision the intended FinAgent principal with the exact tenant-specific v2 issuer and
   Entra object ID using the existing reviewed identity-management process. Assign only its
   approved FinAgent roles. No automatic account creation or role import is implemented.

Keep credentials in the approved environment/secret store; never paste them into chat or commit them.

| Variable | Service | Meaning |
| --- | --- | --- |
| FINAGENT_AUTH_MODE | API | `entra` when deliberately enabling Entra |
| FINAGENT_ENTRA_ISSUER | API | `https://login.microsoftonline.com/<tenant-guid>/v2.0` |
| FINAGENT_ENTRA_JWKS_URL | API | `https://login.microsoftonline.com/<tenant-guid>/discovery/v2.0/keys` |
| FINAGENT_API_AUDIENCE | API | API registration client GUID |
| FINAGENT_ENTRA_REQUIRED_SCOPE | API | Delegated scope name only; default `access_as_user` |
| FINAGENT_WEB_AUTH_MODE | Web | `entra`; default remains `local` |
| FINAGENT_ENVIRONMENT | Web/API | Explicit existing environment; local/test permits local adapters |
| FINAGENT_ENTRA_TENANT_ID | Web | Tenant GUID |
| FINAGENT_ENTRA_WEB_CLIENT_ID | Web | Confidential web registration client GUID |
| FINAGENT_ENTRA_WEB_CLIENT_SECRET | Web | Web application's secret value, from secret store |
| FINAGENT_ENTRA_API_SCOPE | Web | Full delegated scope URI, e.g. `api://<api-client-guid>/access_as_user` |
| FINAGENT_WEB_ORIGIN | Web | Exact external origin, e.g. local `http://127.0.0.1:3001` |
| FINAGENT_WEB_AUTH_REDIS_URL | Web | Private Redis URL; must use rediss outside local/test |
| FINAGENT_WEB_SESSION_SECRET | Web | Existing strong session encryption secret; shared by web replicas |
| FINAGENT_API_URL | Web | API origin reachable from web; HTTPS required outside local/test |

The full scope's final name must match FINAGENT_ENTRA_REQUIRED_SCOPE. Do not use Graph scopes,
`.default`, multiple resource scopes, or a secret with a NEXT_PUBLIC_ prefix.

`compose.entra.example.yaml` is an optional local Compose override. Do not enable it until
registration, trusted principal mapping and settings are ready. It does not change worker
settings; the worker uses trusted internal identities rather than browser authentication.
Changing environment or secrets requires recreating the relevant containers; a plain
container restart does not load newly configured environment values.

## Validation before enabling Entra

From the project root in PowerShell:

```powershell
Push-Location -LiteralPath ".\apps\web"
try {
    npm ci --no-audit --no-fund
    if ($LASTEXITCODE -ne 0) { throw "npm ci failed." }
    npm run test:auth
    if ($LASTEXITCODE -ne 0) { throw "Authentication tests failed." }
} finally {
    Pop-Location
}

.\.venv\Scripts\python.exe scripts\tasks.py lint typecheck test-unit
.\.venv\Scripts\python.exe scripts\tasks.py deps-up test
.\.venv\Scripts\python.exe scripts\tasks.py web-check
.\.venv\Scripts\python.exe scripts\tasks.py test-browser
.\.venv\Scripts\python.exe scripts\tasks.py openapi
.\.venv\Scripts\python.exe scripts/gen_traceability.py
```

Run the commands individually and stop at a failed gate. The existing browser journey
exercises synthetic sign-in; it does not prove real Entra login. The full backend task
uses the disposable test database, not the development database.

An additional local Redis check uses random short-lived synthetic keys only:

```powershell
$env:FINAGENT_WEB_AUTH_REDIS_URL = "redis://127.0.0.1:56379/2"
Push-Location -LiteralPath ".\apps\web"
try {
    npm run test:auth:redis
    if ($LASTEXITCODE -ne 0) { throw "Redis authentication-store check failed." }
} finally {
    Pop-Location
    Remove-Item Env:FINAGENT_WEB_AUTH_REDIS_URL
}
```

Use that explicit URL only for the local development Redis. It does not contact Entra,
the API, a model or a bank connector, and does not flush a database.

## Evidence and remaining verification

Authoring checks passed: 32 offline Node tests, including actual Next request/response
handler calls with a mocked OIDC provider; 16 offline RSA token cases in an isolated
Python harness; TypeScript checking of the supplied/changed web modules; Ruff on changed
Python files; and encrypted session round-trip, atomic transaction consumption, collision
rejection and revocation against a temporary local Redis server.

The isolated Python harness substitutes the unavailable core error container only. It is
not a full project test run. The PostgreSQL integration suite adds nine cases for normal
authentication, principal/tenant inactivity, human-only sign-in and delegated scope admission;
those cases must run in the user's complete project. No live Entra account was authenticated.

After configuring a test registration, verify successful authorised sign-in, consent/cancel
handling, unknown and suspended identities, wrong tenant, session expiry, sign-out/replay,
API mode mismatch, Redis outage, role revocation and privileged MFA denial/acceptance with
the identity/security owners. Inspect HTTPS cookie attributes and confirm browser storage
and rendered responses expose no bearer, ID or refresh token. Validate ingress query-log
redaction, Redis access controls, callback host handling, capacity/rate limits and secret rotation.

Do not mark real Entra integration or production authentication accepted from offline tests alone.

## References

- [Microsoft OIDC protocol](https://learn.microsoft.com/en-us/entra/identity-platform/v2-protocols-oidc)
- [Microsoft authorization-code flow](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-auth-code-flow)
- [Microsoft access-token claims](https://learn.microsoft.com/en-us/entra/identity-platform/access-token-claims-reference)
- [openid-client](https://github.com/panva/openid-client)
- [Redis GETDEL](https://redis.io/docs/latest/commands/getdel/)
