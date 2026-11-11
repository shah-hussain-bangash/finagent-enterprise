# ADR 0035: Local phone passkeys for password-session step-up

Date: 2026-11-09. Candidate implementation; not activated or institutionally accepted.

The user demonstrated a platform authenticator in Android Chrome and signed into
the actual private mobile BFF at the HTTPS device hostname. The deployed mobile
entry is separate from the canonical desktop web service. Google OAuth remains
on its existing desktop origin; the mobile entry is password-only. This does not
constitute production readiness, hardware qualification or a biometric guarantee.

Use the Duo Labs `webauthn` package, pinned to 3.0.1 with its new transitive
dependencies constrained. Server verification must check the exact configured
origin, RP ID/hash, one-use challenge, credential ID, signature, user presence and
user verification. Embedded/cross-origin ceremonies are rejected. No biometric
images, Google password, browser bearer token or client-asserted MFA flag is used.
The configured RP ID is `desktop-48ai2l6.tail9482f2.ts.net`; the expected origin is
exactly its HTTPS origin. Neither is obtained from request forwarding headers.
Changing the RP ID for a future cloud website requires new enrollment.

The feature is disabled by default and refuses non-local/test environments.
Enrollment is limited to owner-configured existing principal UUIDs. For the
initial local reviewer workflow only the assigned reviewer1 is intended. The
current password must be confirmed and email possession verified within the
current session in the preceding ten minutes. Email proof alone grants no MFA.
These are local enrollment defaults, not institutional identity-proofing policy.
No tenant, principal, role, assignment or approval is created by registration.

One passkey record is permitted per principal. HTTP cannot replace its public
key or reactivate a revoked record. Revocation requires a current grant from the
same passkey in the same password session. Recovery/replacement after loss or
revocation is a credential-administrator process; a self-service recovery/reset
flow is not implemented and must not be claimed. DEC-35/SEC-D02 remain open.

Migration 0016 adds three tenant-bound tables with composite foreign keys.
Challenges last at most two minutes and bind the tenant, exact password session,
password credential/version, purpose, RP ID and origin. Issuing a new challenge
consumes earlier outstanding challenges of the same session/purpose. Matching
invalid responses consume the challenge too. Wrong-session attempts do not consume
another session's challenge. Password-session, account, challenge and factor row
locks serialize consumption and enrollment. Mutations and audit records commit
together, including denied matched attempts. Raw responses, signatures, passwords
and biometric information are not written to audit events.

Registration creates no step-up grant. Authentication creates or refreshes a
five-minute grant bounded by the password-session expiry and bound to the active
factor version. Every API authentication revalidates current password credential,
principal and roles; revocation/version changes invalidate grants. Existing TOTP
is preserved and independently checked. A zero signature counter on a synced
passkey is permitted by the verifier; one-use session challenges prevent replay.
This does not establish hardware binding or phishing-resistant institutional MFA
acceptance for the existing password-and-email enrollment policy.

Verification applies only to the requesting phone browser session. It does not
elevate an independent desktop session. Reviewer decisions remain explicit human
actions with unchanged report/evidence binding and separation of duties.

The browser uses native WebAuthn and same-origin bounded JSON POSTs through the
server BFF; session tokens never enter the browser JavaScript. Candidate tests use
a virtual ES256 authenticator and real cryptographic verification. PostgreSQL,
Redis, browser/device enrollment and runtime acceptance must be verified before
activation. No test should be reported as passed merely because it was collected.

References:
- https://github.com/duo-labs/py_webauthn
- https://duo-labs.github.io/py_webauthn/registration.html
- https://duo-labs.github.io/py_webauthn/authentication.html
- https://www.w3.org/TR/webauthn-3/
