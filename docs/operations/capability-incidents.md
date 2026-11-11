# Capability incident response

Status: implemented response controls; full project regression and operational acceptance pending.
All source requirements remain DRAFT. This document does not approve production use.

## Scope

An authenticated, directly assigned TENANT_ADMIN or PLATFORM_OPERATOR with MFA declares a
critical incident. The declaration commits a tenant-scoped emergency stop automatically.
The operator supplies an affected capability, a confirmed owner role, a brief metadata reason
and at least one existing tenant-owned evidence reference. A model cannot declare or restore an
incident through its output. Ordinary rejected claims, expected adversarial evaluation faults,
provider timeouts and budget denials do not automatically become critical-incident declarations.
This change implements response after classification; it is not a universal incident detector.

Known targets are the frozen agent capability IDs plus SVC-EVIDENCE and SVC-FINALIZATION.
Stops are independent of configuration promotion, rollback and feature flags. Restoring an
incident only removes this stop; it cannot enable a capability disabled in the frozen registry.

## Enforcement

- The central model gateway denies affected capabilities before provider work. Denial creates
  a zero-token FAILED usage row with CRITICAL_INCIDENT_ACTIVE.
- The tool gateway denies reads and writes for the affected execution capability and records
  the denial. Existing authorization, subject, window, feature-flag and permission checks remain.
- Finalization is stopped for its own incident or an incident affecting AG-KYC, AG-AML,
  AG-COMPLIANCE, AG-REPORT, CAP-DOCUMENT or SVC-EVIDENCE. This includes local completion when
  downstream writes are disabled, as well as the downstream write path.
- Evaluations cannot bypass an existing stop. The API returns a semantic refusal rather than
  silently running the stopped capability. Expected scripted faults do not create stops.

PostgreSQL shared admission locks cover an entire admitted gateway call or finalization.
Declaration takes the matching exclusive tenant lock, waits for admitted work to drain, then
commits the stop. Work admitted before that cutoff may complete. A stop does not cancel an
already-sent request or undo a committed effect. Model calls, tools and finalization acquire the
same tenant admission lock, so an unrelated admitted call can also delay declaration.
Admission holds a database connection across the external call; size the connection pool and
worker concurrency for this additional connection, bounded provider timeouts and draining.
Database/admission failures refuse new work; there is no fail-open fallback.

## Evidence and history

The declaration, evidence references and metadata fingerprints are written in immutable
capability_incident_events alongside the audited incident state. Agent output content and
validation hashes are preserved without copying customer content into incident metadata.
Supported evidence references are agent_output:UUID, model_request:UUID and tool_invocation:UUID.
References must belong to the declaration tenant; absent and foreign references are concealed.
Evidence may identify a different source capability when investigating a downstream dependency.

The migration installs an immutability trigger and removes application UPDATE/DELETE/TRUNCATE
privileges on event rows. Existing reports, approvals, completions, model usage, external-operation
ledgers and exports are retained. Configuration rollback does not remove incident history or stops.

## API

All paths below start with /api/v1/admin/capability-incidents.

| Method | Suffix | Purpose |
| --- | --- | --- |
| GET | empty | List the latest 100 incident metadata records in the caller's tenant |
| POST | empty | Declare a critical incident and commit the stop |
| GET | /{incident_id} | Read metadata and the earliest 100 immutable lifecycle events |
| POST | /{incident_id}/investigate | Record findings, remediation and verification references |
| POST | /{incident_id}/restore | Restore after investigation and notification |
| POST | /{incident_id}/retry-notification | Explicitly retry a GAVE_UP notification |

Declaration body fields: capability_id, owner_role, reason, evidence_refs.
Owner roles are AML_SUPERVISOR, TENANT_ADMIN or PLATFORM_OPERATOR. Confirm the actual role
assignment and delivery routing operationally; the registry's OPEN owner placeholder is not a
confirmed human assignment. Use brief metadata and ticket references in reasons and findings.

Investigation fields: expected_version, findings, remediation_ref, verification_ref.
Restoration and notification retry fields: expected_version, reason.
Stale versions are refused. Investigation leaves the capability stopped. Only a directly assigned
TENANT_ADMIN with MFA can restore, and only after an immutable investigation record exists and
owner notification has status SENT. Findings and verification references are recorded assertions;
the service does not independently certify the referenced remediation or regression results.
This workflow does not add an independent two-person restoration approval.

## Notifications

The existing worker polls durable incident notification rows at its escalation interval.
Messages use the existing NotificationPort, the declared recipient role, template
capability.incident, an opaque incident reference and a stable tenant-qualified idempotency key.
No new queue event type or case-content payload is introduced.

Classified transient failures receive at most five automatic attempts. Non-transient failures or
exhausted attempts become GAVE_UP and remain visible. An MFA-authenticated operator can explicitly
requeue a GAVE_UP notification with a fresh version and reason after routing is repaired. Total
attempt counts and immutable failure events are retained; each requeue permits one extra attempt.
Restoration remains blocked until notification succeeds.

SENT means the configured adapter's send call acknowledged success. An in-memory/file adapter
only demonstrates local delivery. It does not prove live delivery or that a human read the alert.
The live bank connector must support the new template, owner-role routing and idempotency contract.
No external notification is sent by the installer.

## Installation and verification

The guarded installer defaults to preview, checks reviewed source syntax trees before all writes,
backs up modified files and adds migration 0007. It does not execute a migration or change cases.
Apply database migrations through the existing migrator process before running the updated
application against a development/deployed database. The full backend test task migrates its
separate disposable test database using the existing fixture.

Run Ruff, project mypy and unit tests, then the full backend suite via scripts/tasks.py deps-up test.
Direct integration pytest needs FINAGENT_TEST_DATABASE_URL; skipped tests are not passes.
Regenerate OpenAPI and traceability after successful regression. Existing browser regression
checks UI compatibility; this change adds API/worker controls rather than an incident UI.

Added coverage: 12 input/authorization unit cases and 14 PostgreSQL integration cases covering
model/tool/finalization admission, tenant isolation, evidence concealment, immutable event grants
and triggers, audit rollback, notification retry limits, restoration sequencing, concurrent
admission draining and evaluation refusal. PostgreSQL and full application results must be recorded
from the actual project run. Local isolated service checks do not establish those results.
