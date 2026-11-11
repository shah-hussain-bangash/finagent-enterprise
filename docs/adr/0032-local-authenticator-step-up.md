# ADR 0032: Local synthetic password-session authenticator step-up

Date: 2026-11-05. Derived implementation decision; candidate, not institutional acceptance.

## Context

The synthetic case was submitted by analyst2 and assigned to reviewer1. The UI
correctly requires MFA for decisions. Provisioned password sessions explicitly
carry no MFA. Google login also supplies no independently qualified MFA evidence.
The decision service must remain unchanged and continue enforcing its existing
role, assignment, separation-of-duties and exact-version controls.

## Decision

Add an opt-in local/test-only authenticator verifier for opaque password sessions.
RFC 6238 HMAC-SHA1 uses a random 160-bit secret, six digits, 30-second steps and
one step of clock tolerance in each direction. Published RFC vectors test the
algorithm. An accepted counter cannot be reused, including across sessions;
factor row locks serialize concurrent attempts. Five invalid attempts lock the
factor for 15 minutes. An additional fail-closed Redis limit allows five attempts
per principal per minute across API instances and sessions.

Enrollment is performed through an owner-supervised private terminal and requires
an actual current authenticator code. It grants no session authority and cannot
silently replace a factor. The application role can read factors and update only
accepted-counter/attempt/lockout columns. It cannot insert/delete factors, change
their secret, change enabled state, or change their version.

The secret is AES-256-GCM encrypted with random nonces and tenant/principal AAD.
A separate base64 32-byte runtime key is mandatory. It must be generated privately,
backed up with the runtime's secret configuration, and excluded from artifacts.
No authenticator seed or current code may be shared in chat, logs or screenshots.

After verification, a grant binds the exact password-session ID, tenant, factor
and factor version. It lasts at most five minutes and never beyond password
session expiry. Every request still validates credential/session revocation,
active identity and current DB roles. Disabling/versioning a factor invalidates
grants; resetting a password invalidates sessions through the existing credential
version binding. New logins require their own step-up. Neither TOTP nor any request
body supplies a tenant, role, assignment or approval. Banking writes stay disabled.

The web Security screen accepts codes through a same-origin Next server action.
The browser receives no access token or encryption key. Verification failures use
403 rather than pretending the password session expired. Google/Entra provider
verification and the public primary-provider configuration contract are unchanged.

## Limits

This is local synthetic verification, not enterprise MFA acceptance, phishing-
resistant authentication, real Entra deployment evidence or certification. The
setting refuses startup outside local/test. Institution-approved factor types,
recovery, rotation, enrollment identity proofing, device policy and key custody
remain open under SEC-D02/DEC-35. No self-service reset, bypass flag, recovery code
or manually asserted MFA mechanism is introduced. Owner recovery tooling is not
implemented; an enrolled factor cannot be replaced by the enrollment console.

## Verification

Candidate tests cover RFC vectors, tenant/principal encryption binding, invalid
configuration, replay/concurrency, session isolation, expiry, reset/logout,
factor/principal/tenant revocation, Redis failure, app-role grants and the real
approval-route MFA boundary. Full isolated PostgreSQL/Redis and web verification
must pass before source adoption or runtime activation.

Reference: https://www.rfc-editor.org/rfc/rfc6238
