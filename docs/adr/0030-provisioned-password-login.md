# Provisioned email/password sign-in alongside existing providers

Status: implemented in candidate; integration and live acceptance pending.

The user explicitly requested independent email/password sign-in on 2026-11-02.
Google/Microsoft sign-in remains the primary configured provider. Password login
is a separate opt-in feature, default off, and uses the same identity resolver,
tenant checks, case assignments and authorization policies.

The existing `/api/v1/auth/config` response retains its exact mode-only contract.
The optional password form discovers availability at `/api/v1/auth/password/config`;
failure of that additional lookup hides the password form without disabling the
primary provider's sign-in flow.

No public signup or email-based linking is introduced. A schema-owner console
binds a unique normalized login address to an existing active human principal.
The console never grants roles, and passwords are entered with getpass rather
than shell arguments. Reset/disable increments the credential version, making
previous password sessions invalid on their next API request.

Credentials use unique 128-bit salts and scrypt N=32768, r=8, p=3, 32-byte outputs,
one of the parameter combinations listed in the [OWASP Password Storage Cheat
Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html).
Provisioned passwords require 15-128 characters and are not trimmed or truncated.
No FIPS compliance or institution approval is claimed.

Opaque 256-bit password tokens have 30-minute absolute expiry; only SHA-256 token
digests are persisted in PostgreSQL. The existing encrypted Redis BFF store keeps
tokens server-side behind rotated httpOnly cookie handles. Logout revokes the
backend password token before deleting the BFF record. Password authentication
always yields MFA=false, so protected approvals/administration remain blocked.

Login throttles use shared Redis counters and fail closed on store failure.
Source addresses come from the API connection, not forwarded headers; a BFF may
therefore share its source quota. Account counters also lock after five failures
for 15 minutes. Unknown identities incur the same password hash derivation and
return the same public authentication failure. Public errors do not echo inputs.

Credential lookup is a narrowly used pre-tenant identity lookup, like the existing
principal resolver. Composite tenant/principal and tenant/credential foreign keys
prevent cross-tenant linkage. The application DB role can read credentials and
update attempt counters, but cannot create/reset/disable credentials. Such changes
are schema-owner console operations recorded in the append-only audit chain.

Migration 0012 adds two tables without altering case or evidence data. Enabling
requires both FINAGENT_PASSWORD_LOGIN_ENABLED=true on the API and
FINAGENT_WEB_PASSWORD_LOGIN_ENABLED=true on the web service, plus Redis. The
primary local synthetic issuer mode is excluded from this combined login feature.
Transport checks retain HTTPS and Redis TLS outside local/test. No MFA, public
registration, email verification, reset-email delivery, password-change screen,
external breached-password service, or production acceptance is claimed.
