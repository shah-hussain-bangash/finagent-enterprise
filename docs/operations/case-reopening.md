# Reopening completed cases

Backend verification: 279 backend tests passed, including 13 reopening integration tests. UI extension: prepared; installed build and browser verification pending.

## Command

`POST /api/v1/cases/{case_id}/reopen`

Send a JSON body containing `reason` (3–300 non-whitespace-bounded characters after trimming) and the case version in `If-Match` or `record_version`. If both versions are supplied they must agree. A successful response is 200 with a new ETag, case status REWORK, the new case record version and the new draft report version identifiers.

The operation reuses CASE_ASSIGN, held by AML supervisors, and its existing configured MFA step-up policy. Tenant and case scoping remain enforced. Other-tenant and absent cases are concealed consistently. Analyst and reviewer roles alone cannot reopen a case.

Generic PATCH still refuses changes to completed/cancelled cases. The normal state machine retains its terminal-state rules; only the dedicated reopening service invokes the explicit COMPLETED → REWORK exception. Cancelled cases cannot use this operation.

## Preconditions

- The case version matches the caller's version.
- The case is COMPLETED and has a prior completion history record.
- There is no nonterminal workflow run.
- There is no PENDING, SENT or OUTCOME_UNCERTAIN external operation for the case.
- The report head is FINALIZED.
- Its current immutable version has a matching CONSUMED approval and completed finalization (COMPLETED or WRITE_NOT_PERFORMED).
- No PENDING or APPROVED review request remains open.

Inconsistent legacy data is refused rather than repaired implicitly.

## History and renewed review

Reopening creates a new immutable report version with unchanged content, snapshot binding, validation record and copied claim rows. Its origin uses the existing ANALYST_EDIT enum to represent a human-requested reopening copy; its author label and edit note explicitly state that no new analysis occurred. Lineage identifies the previous report version, completion history, consumed approval and finalization, the requesting supervisor, timestamp and reason.

The report head becomes DRAFT and the case becomes REWORK. The copied validation record describes the copied content; it is not new analysis, a new evidence snapshot or a new approval. Existing snapshot-drift, content and submission guards continue to apply.

Prior report versions, claims, approvals, decisions, finalizations, workflow records, status history and stored exports remain intact. No previously committed external effect is undone. The consumed approval remains bound only to its original report version and cannot approve the new copy. New work requires a new report submission and independent approval. The existing workflow start API accepts REWORK and creates a fresh run; old terminal runs remain terminal.

Case/report updates, copied rows, status history, CASE_REOPENED and REPORT_VERSION_CREATED audit records, and the existing status/report outbox events commit in one transaction. The case row lock and version check allow at most one concurrent reopening based on the same version. A failed audit rolls everything back.

Retries using the old case version receive a conflict. Reload the case before deciding whether any additional action is required. This command does not add a separate idempotency-key contract.

## Scope and verification

The backend command adds no migration, external write or automatic workflow start. The web UI extension adds a supervisor reopening form. Authorization and version preconditions remain server-side.

Regression tests cover the explicit domain exception, preservation of historical rows, a second investigation/review/completion cycle, role restrictions and tenant concealment, stale and missing preconditions, required reasons, concurrent requests, MFA, transactional rollback and inconsistent/unresolved states. Run the project task runner to supply the test database and Redis configuration. Direct pytest execution without that environment can skip integration tests.

Independent dataset labeling, institutional approval, live integrations and production acceptance are outside this change.

## Supervisor reopening in the web UI

The case overview shows reopening controls only for completed cases whose authenticated case response has `can_reopen=true`. This additive API hint is calculated from the trusted principal's CASE_ASSIGN permission. It does not replace authorization on POST. No role is inferred from an email address, subject string or form field.

If the principal still needs configured MFA step-up, the page shows a sign-in notice instead of a submission form. If investigation status cannot be loaded, or a run is active/unresolved, the form is withheld. The backend also checks unresolved external operations and completion consistency when submitting.

The form requires a reason of 3–300 characters after trimming. It submits the record version displayed to the supervisor. The server action validates the identifier and version shape and forwards the original version through If-Match; it never silently reloads a newer version before submission. An outdated tab therefore receives VERSION_CONFLICT rather than creating another reopening.

The bearer token remains in the existing server-side session. The UI uses the existing server-action CSRF handling and pending submit button. A success flash appears only after API confirmation, and the case overview, report, audit and case queue paths are invalidated. Failed API requests display the existing error flash. Reasons are not copied into redirect query parameters.

Reopening does not automatically start a workflow. The assigned analyst can start a new investigation using the existing action. The extension does not change permissions for that action.

The browser journey now covers analyst/reviewer form hiding, the supervisor MFA notice, required and whitespace-only reasons, successful reopening, a stale second tab, retained completion history, unchanged historical report content and a new draft head. A separate API integration test checks permission/MFA hints against real authenticated identities and confirms that reopening availability disappears after the operation.

After installation, run lint/typecheck/unit checks, the full backend regression, the web typecheck/build task and the browser task. Regenerate traceability after obtaining fresh browser results. Prepared checks are not evidence that the installed UI has passed until those tasks succeed.
