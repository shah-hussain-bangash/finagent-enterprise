# 12 — Testing and Evidence Register

**Status:** initial evidence register, 19 November 2026. Entries describe the observed run and its exact revision; they do not establish production readiness. Review the latest CI run and verify access to its artifacts before reusing any claim.

## Recorded automated evidence

**More recent run:** [GitHub Actions CI #13](https://github.com/zenithdevelopers7-source/finagent-enterprise/actions/runs/44524434535) passed all five jobs for commit [`eb302fc18ff48d5d7729a2361aba512ba974d751`](https://github.com/zenithdevelopers7-source/finagent-enterprise/commit/eb302fc18ff48d5d7729a2361aba512ba974d751). The release-blocker selection reported 343 passed and 392 deselected; the full-suite invocation reported 724 passed and 11 deselected. The mutation runner reported 82/82 defined mutations caught. These results are specific to that synthetic CI run: deselected tests were not run in the corresponding invocation, and mutation coverage is limited to the defined changes. Later commits are not covered by this record.

**Run:** [GitHub Actions CI #7](https://github.com/zenithdevelopers7-source/finagent-enterprise/actions/runs/44469329182)  
**Commit:** [`7ccb3c63108c973a0ba8012dbf05ccf6385708f0`](https://github.com/zenithdevelopers7-source/finagent-enterprise/commit/7ccb3c63108c973a0ba8012dbf05ccf6385708f0)  
**Environment:** GitHub-hosted CI with the workflow's configured synthetic test services.  
**Overall:** quality, security, web, integration and image jobs reported success. Later changes—including this document—are not covered by that run.

| Evidence | Observed result | What it supports | Limit |
|---|---|---|---|
| Quality job | Passed | Python lint/type checks, unit checks and API/event-contract drift steps reported success | Not production acceptance |
| Security job | Passed | Configured security task and secret-scan step reported success | Not an independent penetration test or proof that no secret exists |
| Web job | Passed | Configured dependency audit, type check and optimized build reported success | Does not prove complete usability, accessibility or browser coverage |
| Release-blocker suite | 343 passed, 391 deselected | Selected release-blocker tests passed in this CI run | Deselected tests were outside this invocation; do not count them as passed here |
| Full suite | 723 passed, 11 deselected | The workflow's configured full-suite invocation completed successfully | The 11 deselected tests were not run in that invocation; other environments remain unverified |
| Mutation check | 82 of 82 defined mutations caught | Selected tests detected each deliberate control change in that run | Does not cover every possible security failure or certify the implementation |
| Image job | Passed | Configured container image builds completed | Job did not push, sign or deploy images |

CI uploaded test evidence as a workflow artifact. Artifact availability, retention and public accessibility can change; inspect the run rather than assuming an artifact is permanently available. The generated [coverage summary](../traceability/coverage-summary.md) is a separate historical checkpoint and must not be treated as the result of a later run without regeneration and inspection.

## Local workflow threshold observation

On 19 November 2026, the operator reported **1 passed** for `test_first_failure_after_review_resume_uses_total_claim_count` against the disposable `finagent_test` database. The test and related UI wording were uncommitted working-tree changes on integration commit `88ce729`; they are not covered by the CI run above. The test exercises one successful claim, a review resumption, and a first injected failure with a threshold of two. In that scenario the resumed claim reaches the threshold and the run fails rather than retrying.

This characterizes current behavior, not an approved retry policy or evidence for every failure type. A decision is still needed on whether review resumptions should consume a failure-retry budget, followed by implementation and regression tests if that policy changes. The reported local result is not an independently retained CI artifact.

## Representative control traces

The [case study](../07-case-study/08-CASE-STUDY.md) connects tenant filtering, AI authority boundaries and review separation to source, mutation definitions and the recorded run. In mutation testing, a test failure after deliberately weakening the code is the expected result; the mutation runner determines whether that change was caught.

## Evidence still to collect

| Area | Outstanding evidence |
|---|---|
| UI and accessibility | Full role-based journeys, screen-reader and keyboard review, mobile/tablet/desktop coverage and accessibility findings |
| Scale and performance | Approved workload and realistic volumes, latency, capacity and failure measurements |
| AI | Independent labeled evaluation, relevant languages and model/provider versions, retrieval and citation quality, and negative cases |
| Security | Independent threat review, penetration testing, remediation and target-environment verification |
| Integrations | Approved real source contracts, identity and provider behavior, permissions and failure/recovery tests |
| Operations | Monitoring, incident response, backup/restore and recovery exercises with named owners |
| Product and governance | Institutional requirements, reviewer matrix, privacy/legal decisions, acceptance criteria and sign-off |

Use the [testing and evidence plan](07-TESTING-AND-EVIDENCE-PLAN.md) to add future records with the exact commit, environment, result, skips, artifacts, reviewer and limitations. Do not overwrite or hand-edit generated traceability as a substitute for executing a test.
