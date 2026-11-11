# Local synthetic connector intake

This candidate adds a default-off local/test authentication adapter for the
existing POST /api/v1/alerts endpoint. It is separate from human sign-in.

Enable FINAGENT_LOCAL_CONNECTOR_AUTH_ENABLED=true on the API only, after isolated
verification, source adoption, migration 0013 and rebuilding the API image. The
flag is rejected outside local/test. A non-synthetic tenant is always refused.

Credential administration is a trusted schema-owner operation through
finagent.security.authentication.connector_credentials.issue_local_credential
and revoke_local_credential. These functions never provision identities or roles.
The caller must commit the transaction before using a newly issued token. Its raw
value must remain in memory or an appropriate private secret store; never place
it in chat, command-line arguments, URLs, logs or source files. Only its digest is
stored in the database. A local demonstration helper should issue a short-lived
credential, submit a uniquely identified synthetic alert, verify replay returns
the same case, and revoke the credential immediately afterward.

The existing IntegrationConnection establishes tenant, source_system and permitted
alert types. Do not accept these permissions from model output, email claims or
incoming body/header fields. A copied or renamed human token cannot become a
connector credential. The credential cannot authorize any other API route.

All normal intake behavior remains in effect: source/type validation, event vs.
business-key separation, replay deduplication, changed-payload quarantine,
case creation, least-loaded analyst assignment, audit and transactional outbox.
The integration must not force assignment to the currently signed-in analyst.
In the last local readiness check, the predicted assignee was Analyst2.

The existing analyst account cannot ingest alerts or reassign cases. Such access
is not added here. Reassignment and review still require their existing human
roles and qualified step-up. Downstream case-status writes remain disabled unless
separately authorized and configured. This adapter does not approve an investigation.

Production machine identity, MFA acceptance and institutional credential policy
remain separate unresolved work; see ADR-0031 and DEC-34.
