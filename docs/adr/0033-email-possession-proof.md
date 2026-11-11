# ADR 0033: Gmail email possession proof is separate from reviewer MFA

Date: 2026-11-07. Derived local implementation; candidate, not production acceptance.

## Decision

The user requested Gmail verification codes and Android biometric verification.
Implement email possession proof first. It must not satisfy approval MFA, reset
passwords, create accounts, grant roles, change tenant membership or reassign cases.
NIST SP 800-63B excludes email as an out-of-band authenticator; no NIST-conformance
claim is made for this application. A separate verified passkey flow will supply
device/user verification for protected operations. Face-only support is not promised.

Only an authenticated, active, provisioned password user can request a code. The
destination comes from the current database credential, never a request address.
Each random six-digit code has a five-minute window, at most five failed attempts,
one-use consumption and a keyed HMAC digest bound to its tenant, challenge, exact
password session, credential/version, email digest and fixed purpose. Resend is
serialized on the credential, limited to once per minute and cancels prior unused
codes across sessions. Redis limits are shared across instances and fail closed.
Existing session/credential/tenant/principal revocation checks still apply.

Delivery uses Gmail's fixed SMTP endpoint over implicit TLS on port 465, with
system CA/hostname verification and a dedicated app credential held as a runtime
secret. Never use the Google account's normal password. The sender is configured
privately. No code, recipient, SMTP exception text, app credential or HMAC key may
enter logs or URLs. Codes are held only in request memory and the email itself;
database digests cannot be brute-forced without the separate keyed-hash secret.

The pending challenge and audit commit before SMTP. SMTP acceptance is explicitly
not proof of inbox receipt. Delivery failure cancels the challenge and is reported
as unavailable. If SMTP acceptance or the second commit is ambiguous, no code is
considered usable until its SENT state is committed. No raw code is retained for
background retry, no automatic email retry is attempted, and the caller can request
a new code after the resend window. This bounded synchronous delivery is a local
prototype limitation, not an enterprise transactional-mail delivery guarantee.

Successful proof is scoped to the current sign-in session and recent ten-minute
confirmation window. API responses explicitly report grants_mfa=false. No identity
resolver or approval authorization change is included. Default-off configuration
refuses activation outside local/test. Migration 0015 is additive.

## Verification and remaining work

Tests cover transport verification, HMAC binding, invalid configuration,
single-use/concurrent use, cross-session/cross-tenant denial, expiry, revocation,
delivery failure, resend invalidation, rate limits and the real approval MFA gate.
Integration uses real PostgreSQL/Redis with only SMTP replaced by a synthetic sink.
Actual Gmail delivery and owner-held secret configuration remain unverified until
the user privately configures an eligible sender after isolated checks pass.

Reference: https://pages.nist.gov/800-63-4/sp800-63b.html
Gmail setup: https://support.google.com/accounts/answer/185833
