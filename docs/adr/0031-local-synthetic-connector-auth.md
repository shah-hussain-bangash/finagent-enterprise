# Local synthetic connector authentication alongside human sign-in

Status: implemented in candidate; real PostgreSQL and runtime acceptance pending.

The existing Google validator intentionally accepts only human credentials. Local
synthetic alert intake therefore needs a separate explicitly enabled adapter. This
does not replace the configured human provider or the production Entra path.

The adapter accepts 256-bit random opaque bearer credentials only for exactly
POST /api/v1/alerts. Credentials contain no tenant, source, role, scope or MFA claim.
SHA-256 digests alone are persisted. Each row binds to an existing integration
principal with a composite tenant/principal foreign key. The normal resolver reads
current tenant/principal status, current integration role and current active source
registration on every request. Existing ingestion checks still constrain source,
alert family, body fields, deduplication, audit and assignment.

Issuance requires an active synthetic tenant, an active registered integration,
and a schema-owner connection. The application role has SELECT-only access to this
credential table. There is no public endpoint for credential issuance or role
provisioning. Issuance and revocation append audit events in the same transaction.
Credential lifetimes are 60-3600 seconds, default 900. Disabling the adapter blocks
all such credentials immediately; an owner may still revoke them while disabled.
Production deployment must not use this adapter: both Settings and token validation
reject non-local/test environments. The feature defaults off.

Password, Google and Entra human authentication retain their existing contracts.
Connector credentials cannot access identity/session, case reading, workflows,
reports, approvals, exports or administration. They always yield MFA=false and
cannot satisfy supervisor reassignment or independent-review controls. No analyst
role is expanded, and the normal least-loaded assignment rule remains unchanged.

Migration 0013 only adds connector_credentials and its restricted grants. No
identity, case, completed report or role is changed by installing the source.

DEC-34 retains institutional machine-identity/provider selection, credential
lifecycle, transport, operational ownership and production acceptance as OPEN.
No live bank, institution-approved machine identity or production OAuth validation
is claimed by this local synthetic adapter.
