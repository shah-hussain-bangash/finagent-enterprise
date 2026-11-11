# ADR-0021: Web UI as a backend-for-frontend with server actions

Status: Accepted (engineering) - 2026-10-16

## Decision
Next.js App Router renders every screen on the server. The API bearer token lives only in an
AES-256-GCM sealed, httpOnly, SameSite=Strict cookie; browser JavaScript never sees it. Mutations are
server actions (Next.js origin check = CSRF protection) that redirect with the API's real result or
error code - no optimistic success. Local sign-in uses `/api/v1/dev/identities` + `/dev/session`, which
exist ONLY in local/test with the local issuer; Entra ID OIDC sign-in for the UI is not built
(LIVE NOT VERIFIED / missing).

## Consequences
The UI never decides authorization; every screen shows the API's 403/404 states. Uploads pass through
the web server (server-action body limit 21 MB; the API enforces 20 MiB).
