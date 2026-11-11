# Acceptance journey (provisional - Document 3 is missing, DEC-12)

The formal Use Cases & Acceptance Criteria document was not supplied. These are **provisional**
acceptance scenarios derived from the BRD, SRS, DIR, AER and SPR; they are not signed off.

Automated in `tests/e2e/test_acceptance_journey.py` (API + PostgreSQL as `finagent_app` + real worker
loop + SYNTHETIC mock bank over HTTP + isolated parser + mock model behind the model gateway):

| # | Step | Evidence in the test |
|---|---|---|
| 1 | Connector authenticates and ingests an alert | `201` with case id |
| 2 | Replay creates no duplicate case | same key -> same case; new event id, same payload -> `200`, same case |
| 3 | Changed payload replay rejected | `409 ALERT_PAYLOAD_CONFLICT` |
| 4 | Assigned to an authorized analyst | case `ASSIGNED`, least-loaded analyst (DEC-13) |
| 5 | Case-scoped evidence retrieved | evidence via the tool gateway only; tool invocations audited |
| 6 | Documents processed with provenance | 3 KYC documents `PARSED`; passport fields carry page 1 |
| 7 | Immutable evidence snapshot | snapshot `CONFLICTED` (DOB differs, both values kept); later `COMPLETE` |
| 8 | Applicable authorized policy retrieved | AG-COMPLIANCE lineage: `ANSWERED`, approved policy versions |
| 9 | Specialists run through model/tool boundaries | KYC, AML, Document, Compliance, Report outputs recorded with lineage |
| 10 | Calculations, claims, citations validated | no boundary violations; deterministic values from `calc:` refs |
| 11 | Versioned report generated and edited | AI draft v2 valid; analyst edit v3 with diff; stale base `409` |
| 12 | Frozen review snapshot submitted | approval request binds v3 + snapshot; open conflict blocked an earlier submit (`422`) |
| 13 | Self-approval and unauthorized review denied | submitter via delegation `403` (SoD); other analyst `403`; tenant B `404`; superseded version `409` |
| 14 | Independent reviewer decision | `RETURN_FOR_REWORK` with MFA, idempotent replay identical |
| 15 | Approval invalidated by a material change | edit after approval -> `INVALIDATED`, case `REWORK`, finalization refused, no write |
| 16 | Rework and resubmission | v4/v5 resubmitted and approved |
| 17 | Finalization under the configured policy | write flag on (setting + tenant flag) -> one downstream write `CONFIRMED`, case `COMPLETED` |
| 18 | Audit history and visible status | hash chain verifies; key actions present; timeline ends `FINALIZING -> COMPLETED` |
| 19 | Other tenant cannot access resources | identical `404`s for case, report, evidence, documents, approval, run, outputs, export |
| 20 | Worker interruption does not duplicate effects | worker killed between write and ledger update; second worker probes, confirms; exactly 1 write |

Additional branch coverage: `tests/integration/test_aml_workflow.py` (blocked evidence, boundary
escalation, uncertain write + reconciliation, request information, reject effects, crash replay of the
draft notification). Real browser: `tests/browser/test_ui_journey.py` (analyst and reviewer complete a
case through the web UI in Chromium).

Run: `python scripts/tasks.py test` (all) or
`python -m pytest tests/e2e -q` with `FINAGENT_TEST_DATABASE_URL` and `FINAGENT_APP_DB_PASSWORD` set.
