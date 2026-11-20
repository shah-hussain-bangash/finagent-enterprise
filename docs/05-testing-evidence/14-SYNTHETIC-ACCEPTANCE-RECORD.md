# Synthetic case acceptance record

**Status: synthetic browser journey passed locally; formal acceptance remains open.** This is not a staging or production approval. Formal acceptance criteria and accountable institutional owners remain open under [DEC-12](../decisions/open-decisions.md).

**Latest local run, 20 November 2026:** the operator ran `tests/browser/test_ui_journey.py` with the browser marker using the isolated `finagent-acceptance-baseline` working tree and disposable `finagent_test` database. The reported result was **1 passed in 43.10 seconds**. The tested web source files matched the corresponding files in `finagent-ready` by SHA-256. The working-tree browser test was subsequently reformatted in its failure diagnostic to satisfy lint; rerun it to establish evidence for that exact file. Both trees contain uncommitted changes based on `2f44700`. This run covers the synthetic analyst, reviewer and supervisor journey implemented in that test, including completion and reopening checks. It is not evidence of a committed release, a live-bank integration, full accessibility assessment, or production readiness.

**Additional local web checks, 20 November 2026:** the working tree passed 59 interface rendering and behavior tests, 88 authentication tests (`npm run test:auth`), and 18 passkey proxy tests (`npm run test:passkeys`). The authentication and passkey suites were run through their dedicated scripts because they prepare their own test dependencies. These local results are separate from the browser journey and the hosted CI baseline.

The workflow changes were subsequently committed locally as `4cf4d4d`. The browser journey was run on the corresponding uncommitted source before the test diagnostic formatting change described above; it has not been rerun against the committed revision. The CI baseline in the portfolio status record predates this commit.

**First local attempt, 20 November 2026:** the operator ran the browser test against an isolated copy at commit `2f44700`; it failed at the initial unauthenticated redirect assertion (1 failed). An independently inspected local web response contained a streamed sign-in redirect, and a browser reached the sign-in page. The test was updated locally to wait for that redirect and explicitly select synthetic test authentication. The updated full journey has not yet been rerun; the cause and full outcome remain unconfirmed until it passes.

**Later local attempt, 20 November 2026:** sign-in succeeded and the synthetic workflow was recorded as `WAITING_REVIEW`, but the case page stayed on “Working…” and the expected confirmation did not appear. Browser diagnostics observed an HTTP 303 response for the action. The redirect completion problem remains under investigation; the browser acceptance journey failed and independent review and completion were not established by this run.

**Earlier isolated investigation:** the production web build displayed the confirmation with a minimal synthetic mock API, including a `WAITING_REVIEW` run. This did not establish a root cause or fix; the full browser journey had failed at that point.

Further testing reproduced the stalled confirmation using a recorded synthetic case response. An earlier candidate display change failed the full browser journey and was removed. Subsequent changes to action confirmations and test synchronization were included in the later passing local run above; the underlying cause of the original redirect behavior remains unconfirmed.

## Purpose and scope

Demonstrate one synthetic `TM_STRUCTURING` case from assignment through evidence review, draft and independent review. Separately verify denied access, an interrupted operation and its recovery, and the audit trail. Use an isolated test environment and disposable database; these tests can reset their configured schema. Never target a shared or live database.

## Evidence to fill in after execution

| Field | Actual recorded value |
|---|---|
| Revision and branch | NOT RECORDED: full Git commit, working-tree status and build revision |
| Environment and operator | NOT RECORDED: isolated environment, date/time and operator |
| Web build | NOT RECORDED: exact source revision, build command and result |
| Test dependencies | NOT RECORDED: isolated database and Redis identity, browser and fixture versions; never include credentials |
| Synthetic identities and case | NOT RECORDED: role labels, fixture and case IDs; synthetic data only |
| CI / local run | NOT RECORDED: command, result, passed / failed / skipped / deselected counts and artifact link |
| Recovery and audit | NOT RECORDED: injected fault, expected and observed states, effect count and audit verification |
| Review and exceptions | NOT RECORDED: reviewer, findings, evidence locations and outstanding limitations |

## Traceable acceptance checks

| Scenario | Existing executable evidence | Record for this baseline |
|---|---|---|
| Analyst and independent reviewer complete a case in Chromium | [`test_ui_journey.py`](../../tests/browser/test_ui_journey.py) signs in as distinct roles, starts work, reviews evidence, submits a draft and records a review decision | PASS locally, 20 November 2026: 1 passed in 43.10 seconds, isolated working tree with uncommitted changes based on `2f44700`. The full release evidence and browser version still need recording. |
| Unauthorized and cross-tenant review or evidence access | [`test_acceptance_journey.py`](../../tests/e2e/test_acceptance_journey.py) checks self-approval denial, other-role denial and cross-tenant resource denial | NOT RUN as part of this acceptance record. Note the exact expected and observed response without including credentials. |
| Interrupted external operation and recovery | The same API-level acceptance test injects a crash around the synthetic downstream status operation, resumes with a second worker, probes and checks a single confirmed operation | NOT RUN as part of this acceptance record. This is a synthetic mock write, not evidence of live-bank recovery. |
| Case transitions and audit integrity | The same API-level test checks key audit actions, audit-chain verification and the completed timeline | NOT RUN as part of this acceptance record. Preserve the artifact and exact revision. |
| Uncertain external outcome | [`test_aml_workflow.py`](../../tests/integration/test_aml_workflow.py) includes an uncertain-write and reconciliation scenario | NOT RUN as part of this acceptance record. Record hold and reconciliation results separately. |
| Accessibility and role-specific UI | Browser test includes some role and MFA controls; the [UI work tracker](../production-ui-work-tracker.md) and [evidence plan](07-TESTING-AND-EVIDENCE-PLAN.md) cover broader assessment | NOT RUN. Automated browser checks do not replace screen-reader, keyboard, contrast and device review. |

## Execution precautions

The default pytest configuration deselects browser tests. The browser journey needs a production web build made from the **same revision**, Chromium, a running synthetic mock bank, and isolated test services. The fixture starts the API, web process and worker; see the test and project setup before running it. The documented `python scripts/tasks.py test-browser` runs the browser suite; an individual test can be selected with `python -m pytest -m browser tests/browser/test_ui_journey.py -q --tb=short` once those prerequisites and the disposable test database are configured. Record any failed, skipped or deselected checks as such.

The API test and browser test are separate demonstrations. Do not claim a single uninterrupted staging run combines their observations. For a release packet, add real outputs and artifact links, the approved image digest and configuration where applicable, and sign-off only when actual owners have reviewed them.
