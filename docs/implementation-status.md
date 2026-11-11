# Implementation Status

The resume point for every build session (prompt Â§60).

Requirement-level status is generated in:
- [Coverage summary](traceability/coverage-summary.md)
- [Requirements to code](traceability/requirements-to-code.md)
- [Requirements to tests](traceability/requirements-to-tests.md)

Do not hand-edit those generated files.

All 300 source requirements remain DRAFT. Passing tests do not constitute institutional approval or production acceptance.

## Current source checkpoint - 2026-10-31: verified quality repair

The tested source checkpoint was adopted from the isolated PostgreSQL 16 candidate.
This is source adoption only. The active host virtual environment was not updated,
application services were not restarted, and runtime images were not rebuilt.

- Full backend: **434 passed, 1 skipped, 1 deselected; 15 subtests passed**.
- Separate identity regression: 16 passed. Promotion readiness: 8 passed.
- The native Linux seccomp pytest case was skipped on Windows; the current-source
  syscall-denial probe passed separately in the patched Linux parser image.
- The browser journey was deselected and was not rerun for this checkpoint.
- Repository lint and formatting passed (230 files); typing passed for 166 source files.
- Bandit passed with reviewed per-call subprocess exceptions and explicit shell=False.
- The isolated environment used pypdf 6.21.4 and pip 26.2.2; its dependency audit
  reported no known vulnerabilities. The application is checked as source, not audited via PyPI.
- Evidence-content reads now explicitly handle nullable retained parent content without
  rewriting immutable parent rows. OCR command-argument handling has regression coverage.
- Readiness coverage and evaluation/identity test contracts were reconciled with current behavior.
- Four standalone check suites passed (67 checks); DB/identity remain mocked in those suites.
- Temporary PostgreSQL and Redis containers and the candidate parser image were removed.
- No migration, destructive retention, main database reset, model call, AI configuration
  promotion or cloud deployment was performed by the source-adoption step.

Evidence:
`reports/quality-repair-pg16-20261031T054532980181/resume-20261031T072347733382/`.
The complete backend JUnit evidence is preserved as `reports/junit.xml` and generated
traceability reflects that run. Historical browser evidence was not merged into this refresh.
All 300 source requirements remain draft; generated test statuses are not institutional approval.

Next: update the development runtime to the verified dependency/image checkpoint,
then verify health, sign-in, operations and evaluation behavior in rebuilt services.
Independent evaluation data/labels, live enterprise-provider verification, accessibility,
performance, disaster recovery and production acceptance remain pending.

## Phase status

| Phase | Status | Notes |
|---|---|---|
| P0 Baseline | **DONE** | Inventory of 245 + 55 MST IDs, gap review, ADRs, open-decisions register and provisional use cases |
| P1 Foundation | **DONE** | Package, tooling, config, errors, logging, API, Alembic, Compose, Dockerfiles and CI definitions |
| P2 Security & tenancy | **DONE (local)** | MFA step-up, delegation, break-glass disabled by default, quotas and DB privilege separation. Entra API and web authentication implemented and locally tested; real Entra and MFA acceptance remain pending |
| P3 Case domain | **DONE (local)** | Guarded transitions, PATCH/If-Match, reassignment, escalations and supervisor reopening with preserved completion history |
| P4 Workflow foundation | **DONE (local)** | Runs, fenced leases, encrypted checkpoints, outbox/inbox/DLQ, effect ledger and worker |
| P5 Connectors & tool gateway | **DONE (local)** | Mock bank, resilient connectors, frozen tool registry and single guarded write. Live bank integrations remain unverified |
| P6 Documents & evidence (Phase A) | **DONE (local)** | Upload/versions/download links, signature checks, scanner port, quarantine, isolated parser with time, memory, network and process controls; Windows hosts use isolated Linux containers. PDF/DOCX/CSV parsing, typed extraction with provenance, corrections, versioned evidence, conflicts and immutable snapshots (ADR-0017). Live ClamAV and OCR provider remain unavailable |
| P7 Policy knowledge & RAG (Phase B) | **DONE (local)** | Lifecycle controls, section chunking, safe reindex, permission/date/applicability filtering before ranking, BM25 and Azure AI Search adapter, abstention, conflicting guidance and citation validation (ADR-0018). Azure AI Search live verification remains pending |
| P8 Model gateway & agents (Phase C) | **DONE (local)** | Versioned registry with AG-RISK marked FUTURE, model gateway budgets/retries/one repair/usage rows, mock and Azure OpenAI providers, deterministic calculations and validation, KYC/AML/Compliance/Document/Report capabilities (ADR-0019). Azure OpenAI live verification remains pending |
| P9 Investigation workflow (Phase D) | **DONE (local)** | `aml-1` graph: evidence rounds, blocking gaps, analysis, draft, ledgered notification, review interrupt and finalization; durable, fenced and cancellable. Skeleton retained as `durability_probe` (ADR-0020) |
| P10 Reports & approval (Phase E) | **DONE (local)** | Versioned reports and claims, edits with stale protection, submit/withdraw, reviewer assignment, four decisions, separation of duties including delegation conflicts, MFA, invalidation, finalization gate and exports with Draft watermark |
| P11 Functional UI (Phase F) | **DONE (local)** | Functional screens, local sign-in, BFF sessions, server actions with real results, loading/empty/error/expired states (ADR-0021). Supervisor reopening UI and operations dashboard implemented. Entra web sign-in with opaque Redis sessions implemented and locally tested. Real Entra login, MFA acceptance and WCAG audit remain pending |
| P12 Evaluation, privacy, ops (Phase G) | **DONE (local, PROPOSED thresholds; independent review pending)** | SYNTHETIC dataset and runner, adversarial/injection/budget/RAG checks, dataset controls, case-slice reporting, promotion gate and rollback, enforced promoted configuration, retention dry run and legal holds, scoped operational reporting, capability-incident response and runbooks. Independent validation/release datasets and qualified label review remain pending |
| P13 Deployment (Phase H) | **PARTIAL** | API/worker/web/mock-bank images, dedicated parser image, Compose with migration and health checks, Windows scripts, CI definitions, Helm and Terraform definitions, backup/restore script and runbooks. Helm and Terraform remain NOT VALIDATED / NOT APPLIED |
| UAT, penetration test, certification, live integrations | **NOT STARTED / EXTERNAL** | Require institutional decisions, credentials and environments |

## Latest verification - 2026-10-22: local Google sign-in

Local Google sign-in checkpoint complete within the tested scope below.
Evidence: user-supplied Windows terminal output, manual browser confirmations
and supporting screenshots. Host Node.js version: 24.21.0.

| Check | Recorded result |
|---|---|
| Real Google account verification | Passed; verifier exit code 0 |
| Controlled principal provisioning | Approved synthetic-bank-a / PLATFORM_OPERATOR mapping created; exit code 0 |
| Manual browser checks | All seven completed |
| Web authentication regression: `npm run test:auth` | 69 passed; 0 failed, cancelled, skipped or todo; exit code 0 |
| Backend Google token validation: `python -m pytest -q --tb=short tests/unit/test_google_tokens.py` | 24 passed; exit code 0 |
| Web TypeScript check: `npm run typecheck` | Passed; exit code 0 |
| Windows production web build: `npm run build` | Next.js 15.5.28 build passed; exit code 0 |

The seven manual checks covered initial sign-in, dashboard reload after
15 seconds of inactivity, the Operations dashboard, case-access restriction,
sign-out, a fresh protected-page visit after sign-out, and Google sign-in again.

Operations rendered with tenant-metadata scope. Its separate Uncertain
operations section remained blocked by the MFA requirement. The case queue
remained forbidden for PLATFORM_OPERATOR.

Web authentication tests use mocked provider responses. Backend Google token
tests use generated RSA keys and signed synthetic credentials with mocked key
lookup. Real Google sign-in was verified separately through the browser.

The authentication test runner includes the diagnostic helper and resolves its
imports in the isolated test directory. The host build included the Google
start and callback routes. It did not rebuild the running Docker image.

The 15-second browser idle check does not establish full session-expiry
behavior. Backend token-expiry tests do not establish browser-session expiry.

Subsequent verification: the full backend regression is recorded below.
The referenced conversation also records the separate PostgreSQL identity and
revocation check: seven scenarios passed, test and isolated runner exit codes 0,
and temporary PostgreSQL container cleanup confirmed.

Local Google runtime follow-up completed on 2026-10-23: controlled
real-browser server-session expiry enforcement and rebuilt Docker
`api`/`web`/`worker` runtime validation passed. Natural-duration session-expiry
observation, qualified Google MFA step-up, real Microsoft Entra verification,
and production/deployment acceptance remain separate pending work.
Earlier test results retain their historical dates and scope.

### Full backend regression - 2026-10-22

The completed full backend regression ran against an isolated temporary
PostgreSQL 16 instance. Evidence: the completed runner output reported in the
referenced "Prompt Engineering For Codex" conversation; tests were not rerun
for this documentation update.

| Result | Value |
|---|---|
| Passed | 406 |
| Skipped | 2 |
| Deselected | 1 |
| Passing subtests | 15 |
| Pytest exit code | 0 |
| Isolated runner exit code | 0 |
| Temporary PostgreSQL container | Removed successfully |

Recorded skips:

1. `tests/unit/test_access_policy.py:117`: `FINAGENT_TEST_REDIS_URL`
   was not set.
2. `tests/unit/test_parser_network_isolation.py:32`: Linux seccomp
   runtime test is not applicable to the Windows host.

Skipped and deselected tests are not counted as passes. This backend result
does not establish full browser-session expiry or rebuilt-container behavior.

## Latest verification - 2026-10-26: P13.1 source-of-truth reconciliation

The authoritative implementation status was reconciled against the current
source tree and the latest locally verified runtime state.

| Item | Verified state |
|---|---|
| Database migration head | `0011` |
| Sensitive legacy columns | Scrubbed by irreversible migration `0011`; sensitive content retained only in purgeable payload tables |
| Document extraction payloads | Purgeable and locally verified |
| Document field payloads | Purgeable and locally verified |
| Evidence item payloads | Payload-first storage implemented; feature-gated destructive `evidence_and_snapshots` purge implemented and locally verified |
| `document_content` retention | Feature-gated destructive execution implemented and locally verified |
| Legal holds | Verified to prevent destructive retention |
| Retention retries | Verified idempotent |
| Full backend regression after document-content retention | 420 passed, 2 skipped, 1 deselected; 15 subtests passed |
| Google API authentication mode | `google` |
| Google web authentication mode | `google` |
| Google web runtime configuration | Compose wiring restored and locally verified |
| FinAgent sign-in | `http://127.0.0.1:3001/signin` returns the Google sign-in flow |
| API runtime DB principal | `finagent_app` |
| API health | HTTP 200 |
| Worker runtime | `worker_started` verified |
| Local OCR | Tesseract OCR implemented and verified |
| Production OCR acceptance | Pending |
| Main database destructive retention execution | NOT performed during this reconciliation |

P13.1 documentation reconciliation is complete within the locally verified
scope. P13.2 subsequently implemented and verified destructive retention for
the separate `evidence_and_snapshots` retention class while preserving
immutable evidence identity, hashes, provenance and snapshot lineage.

Remaining production acceptance work is unchanged: real Microsoft Entra
verification, qualified MFA acceptance, natural-duration session-expiry
observation, live Azure/service integrations, infrastructure deployment,
CI/CD execution evidence, performance/security/accessibility testing, DR,
UAT and institutional approval.

## Latest verification - 2026-10-26: P13.2 evidence and snapshot retention

P13.2 destructive retention for the `evidence_and_snapshots`
record class is implemented and locally verified.

The implementation intentionally separates purgeable sensitive evidence
content from immutable evidence and snapshot lineage.

| Verification item | Result |
|---|---|
| Retention record class | `evidence_and_snapshots` |
| Sensitive content deleted | `evidence_item_payloads` only |
| `EvidenceItem` parent rows | Preserved |
| Evidence content hashes | Preserved |
| Evidence source/version metadata | Preserved |
| `EvidenceSnapshot` rows | Preserved |
| `EvidenceSnapshotItem` lineage | Preserved |
| Legal holds | Eligible held cases excluded from destructive execution |
| Feature default | Destructive execution remains disabled by default |
| Retry behavior | Idempotent; second execution deletes nothing further |
| Application DB privileges | `evidence_item_payloads` is explicitly deletable; immutable snapshot tables remain protected |
| Targeted P13.2 integration test | 1 passed |
| Governance integration regression | 7 passed |
| Full backend regression | 421 passed, 2 skipped, 1 deselected; 15 subtests passed |
| Full backend failures/errors | 0 |
| Alembic revision | `0011` |
| Destructive test database | `finagent_test` only |
| Main `finagent` database | Not used for P13.2 destructive testing |

Expected full-suite skips remain:

1. `tests/unit/test_access_policy.py` Redis-dependent test when
   `FINAGENT_TEST_REDIS_URL` is not configured.
2. `tests/unit/test_parser_network_isolation.py` Linux seccomp runtime
   verification on Windows; Windows transport isolation is tested separately.

P13.2 is complete within the locally verified Release-1 scope.

The next engineering step is **P13.3 - Evaluation Datasets**:
inventory the existing synthetic evaluation datasets, determine the
coverage against Release-1 AML/KYC capabilities and requirements,
identify missing positive/negative/adversarial cases, and add
versioned reproducible datasets and acceptance evidence without
inventing institutional labels or production thresholds.

## Historical verified results â€” session 22

Verified on 2026-10-21 using Windows, Python 3.12.10 and the local PostgreSQL/Redis stack.

| Gate | Command | Result |
|---|---|---|
| Web dependency installation | `npm ci --no-audit --no-fund` in `apps/web` | Passed |
| Authentication protocol and handler tests | `npm run test:auth` | **32 passed**, no failures or skips; provider responses are mocked |
| Ruff lint and formatting | `tasks.py lint` | Passed; 213 Python files already formatted |
| Strict Python typing | `tasks.py typecheck` | Passed; no issues in 157 source files |
| Unit suite | `tasks.py test-unit` | **179 passed, 1 skipped** |
| Full backend suite | `tasks.py deps-up test` | **376 passed, 1 skipped, 1 browser test deselected** |
| Entra API identity integration tests | Included in full backend suite | **9 passed** |
| Entra RSA token unit tests | Included in unit and backend suites | **16 passed** |
| Web type checking and production build | `tasks.py web-check` | `tsc --noEmit` and `next build` passed; Entra start and callback routes included |
| Browser regression | `tasks.py test-browser` | **1 passed in 31.26 seconds**; existing synthetic sign-in journey |
| Local Redis authentication store | `npm run test:auth:redis` | Passed encrypted record round-trip, one-use flow consumption, collision rejection and session revocation |
| OpenAPI | `tasks.py openapi` | Regenerated successfully |
| Traceability | `scripts/gen_traceability.py` | Regenerated successfully: 122 TEST PASSED, 60 TEST PASSED (partial scope), 118 DRAFT REQUIREMENT |
| Real Microsoft Entra login | Not performed | **Pending** |
| Real Entra MFA / Conditional Access acceptance | Not performed | **Pending** |

The skipped test is the Linux-only seccomp runtime test. Windows-host parser transport and the rebuilt Linux worker were verified separately during session 21.

The Redis check used random, short-lived synthetic records. It made no Entra, API, model, connector or business request.

Authentication protocol tests, RSA tests, local Redis checks and the synthetic browser journey do not establish successful authentication against a real Entra tenant.

The session 22 changes have not yet been verified in rebuilt application containers.

## Historical verified results â€” session 3

Verified on 2026-10-16 using Windows 11, Python 3.12.10, Node 24, PostgreSQL 16 and Redis 7 in Docker.

These results describe session 3 and are not current evidence for every later change.

| Gate | Command | Result |
|---|---|---|
| Baseline reproduction, session 2 state | `tasks.py lint typecheck test` | 133/133 passed; lint and mypy clean |
| Lint and format | `tasks.py lint` | Passed |
| Strict typing | `tasks.py typecheck` | No issues in 144 source files |
| SAST / dependency audit | `tasks.py security` | Bandit reported no issues; three justified `# nosec` annotations. pip-audit reported no known vulnerabilities at that time |
| Full suite | `tasks.py test` | **198 passed, 0 failed, 0 skipped**; one browser test deselected by design. Evidence: `docs/testing/evidence/junit-2026-10-16-session3-full.xml` |
| E2E acceptance journey | Included in `test` | Passed the 20-step journey in `tests/e2e/test_acceptance_journey.py` |
| Release blockers | `tasks.py test-release-blockers` | **103 passed** |
| Browser journey | `tasks.py test-browser` | **1 passed**; analyst and reviewer completed a case through the UI |
| Mutation checks | `tasks.py mutation-check` | See historical mutation result below |
| Web | `tasks.py web-check` | Type checking and production build passed |
| Migrations | Test fixture downgrade/upgrade and scratch DB drift check | Passed |
| Images | `docker compose --profile app build` | API, worker, web, mock-bank and migrate images built; Python dependencies installed from `requirements.lock` |
| Compose stack | App profile startup and smoke script | Passed: migration completed, API healthy, sign-in returned 200, alert ingested and worker reached WAITING_REVIEW with three parsed documents and a draft report. App containers were stopped afterwards |
| Base image scan | IDE Docker Scout | One high vulnerability reported in `python:3.12-slim` at that time; remediation requires fresh scan evidence |
| Traceability | Generated from full and browser JUnit evidence | 122 TEST PASSED, 60 TEST PASSED (partial scope), 118 DRAFT REQUIREMENT, of 300 |

### Historical mutation result

**82/82 controls caught:** 48 from P2â€“P5, 12 from P6 and 22 from P7â€“P12.

The full run caught 80 controls. Two were corrected and rerun individually:

- `step-budget` reported ERROR because the budgeted line changed when bookkeeping steps became exempt. The registry snippet was updated and the control was caught.
- `citation-access-recheck` initially survived because revocation also deactivated chunks, so the test did not isolate approval-status validation. An ACTIVE chunk belonging to an unapproved draft version was added to the test, after which the control was caught.

These are historical results, not evidence that mutation checks were rerun after every subsequent feature.

### Defects found and fixed in session 3

13. **UTF-8 corruption from a PowerShell edit:** reversed `Â§` becoming `Ã‚Â§` in `models.py` and `errors.py`; subsequent edits use UTF-8-safe tools.
14. **Repeated re-fetch appeared material:** moved source as-of time from transaction completeness content into evidence metadata.
15. **Audit outcome overflow:** moved the long `INSUFFICIENT_EVIDENCE` outcome into details.
16. **Generic ARRAY lacked the PostgreSQL overlap operator:** switched the role-ACL column to PostgreSQL ARRAY.
17. **Stale-decision invalidation would roll back:** committed invalidation before returning the 409 response.
18. **Migration 0005 imported a growing live table list:** froze migration-specific table lists.
19. **Mock document numbers used process-randomised `hash()`:** removed unstable document identifiers.
20. **Crash test selected a point after commit:** replaced it with a write-applied/ledger-not-updated crash probe.
21. **Browser test races:** corrected redirect timing and React SSR comment-node handling in tests.
22. **Over-claimed traceability:** removed evaluation tags that incorrectly implied independent set separation and two-reviewer labelling.
23. **Mutation survivor:** strengthened the `citation-access-recheck` regression as described above.

## Implemented but awaiting live-service verification

- Microsoft Entra ID API token validation and web sign-in with opaque Redis sessions are implemented and locally tested. Real tenant authentication, trusted principal mappings, MFA/Conditional Access acceptance and deployment verification remain pending (DEC-33).
- Azure OpenAI provider exists; no approved live deployment has been verified (DEC-07, AI-D03).
- Azure AI Search retriever and index publishing exist; live verification remains pending.
- Azure Blob storage adapter exists; live verification remains pending.
- ClamAV `clamd` scanner has been tested against a fake clamd socket only.
- Azure Service Bus queue adapter has been tested with a fake client only.
- Bank connectors have been verified against the SYNTHETIC mock bank; real core-banking/case-system verification remains pending.
- Capability-incident notifications have local implementation and regression evidence; live routing and confirmed owner assignments remain pending.
- Connector observations and the operational dashboard have local verification; real connector availability and production operational targets remain unverified.

## Blocked by external credentials or institutional decisions

- Pilot scope, reviewer matrix, SLAs, budgets, thresholds, retention periods, jurisdiction and legal basis: see `docs/decisions/open-decisions.md` for DEC-01..33, DATA-D*, AI-D*, SEC-D* and NFR-D*.
- Independent evaluation dataset ownership, two-reviewer labelling, adjudication and approved evaluation thresholds.
- Test Entra tenant/app registrations, consent, trusted principal mappings and security-owner acceptance of MFA behavior.
- Deployment to Azure, including authorization for cloud costs, image signing/SBOM attestation, Terraform plan/apply, Helm installation and staging exercises (MST:OPS-002).
- Penetration testing, WCAG 2.2 AA audit, UAT and performance/availability measurement. Operational targets remain PROPOSED until approved.

## Still missing or partial

- OCR provider integration; the current explicit result is `OCR_UNAVAILABLE`.
- Retention deletion execution and propagation; only dry-run behavior is implemented until retention periods are approved.
- Operations dashboard, connector observations, elapsed completion/review metrics and quality proxies are implemented and locally verified. Live connector validation, active human handling time, monetary tool fees, independently labelled reviewer corrections and policy freshness remain pending (FR-037/038, AER-035).
- Independent validation and locked release datasets, qualified label review and complete evaluation coverage remain pending. Dataset controls and case-slice reporting are implemented (AER-030/032).
- Live incident-notification routing, confirmed owner assignments and broader automatic incident detection remain pending. Authorized incident declarations automatically stop affected capability and finalization paths (AER-037).
- Parser network/process isolation and required memory enforcement are locally verified through Windows-host Docker parsing and the rebuilt Linux worker. Broader Linux filesystem isolation and production validation remain pending.
- Entra web sign-in is implemented and locally tested. Test app registrations, trusted principal mappings, real Entra sign-in and MFA acceptance remain pending.
- Terraform network topology, AKS and OpenAI resource deployment remain pending.

## Not verified â€” do not treat as accepted

- Every live integration listed above.
- Entra production authentication, real MFA evidence and Conditional Access behavior.
- Session 22 changes running in rebuilt application containers.
- Container images under Helm or in Azure.
- Current base-image vulnerability status; historical scan results require fresh verification.
- GitHub Actions execution; workflow definitions alone are not execution evidence. gitleaks has not been run locally.
- Multi-node lease timing and multi-replica Redis rate limiting.
- Production load, availability, security certification and institutional acceptance.

## Handoff notes

- Host: Windows. Use `python scripts/tasks.py <target>` for project tasks.
- Local ports: PostgreSQL 55432, Redis 56379, API 8001, web 3001 and mock bank 8100. Another project uses ports 3000/5432/6379/8000.
- The local application stack runs in Docker. Check existing containers and port ownership before using the host-process startup script.
- Direct integration-test execution needs `FINAGENT_TEST_DATABASE_URL`, `FINAGENT_APP_DB_PASSWORD` and `FINAGENT_TEST_REDIS_URL`. The project task runner loads `.env`.
- Browser tests require Chromium and a current web build.
- Authentication tests: run `npm run test:auth` in `apps/web`.
- Local Redis authentication-store check: run `npm run test:auth:redis` with the explicit local test Redis URL, following `docs/operations/entra-web-signin.md`.
- Generated files include `docs/api/openapi.json`, event schemas under `packages/contracts/events/` and traceability outputs. Regenerate them using the documented scripts.
- Development database was upgraded to migration `0008`. The Entra implementation adds no database migration.
- Current authentication checkpoint, 2026-10-22: complete within the documented local scope. Seven manual checks, 69 web authentication tests, 24 backend Google token tests, web typecheck and the Windows production web build passed.
- Entra setup guide: `docs/operations/entra-web-signin.md`. The optional Compose override is `compose.entra.example.yaml`; it has not been enabled or live-verified.
- Backend follow-up, 2026-10-22: full regression completed with 406 passed, 2 skipped, 1 deselected and 15 passing subtests; pytest and isolated runner exit codes 0; temporary PostgreSQL container removed. Separate identity/revocation verification recorded seven passing scenarios in the referenced conversation.
- Google local runtime verification completed on 2026-10-23, including controlled real-browser server-session expiry and rebuilt Docker API/web/worker validation. Redis-dependent access-policy and Linux seccomp tests remain skipped in this Windows regression run; natural-duration expiry, qualified Google MFA step-up, real Entra verification and production acceptance remain separate work.
- Independent evaluation datasets, qualified review and other live integration verification remain pending.

## Session log

Historical Next entries describe the handoff at that session. Use the current handoff notes above for the active task.

| Date | Session | Work | Commands and results | Next |
|---|---|---|---|---|
| 2026-10-15 | 1 | Read all 10 documents; gap review, prompt v2, Phase 0 artefacts, Phase 1 foundation, slice 1 | All gates green; 37/37 tests | Phase 4 |
| 2026-10-15 | 2a | Baseline on Windows; P2/P3 completion | 66/66 tests, 16/16 mutations | P4 |
| 2026-10-16 | 2b | P4 workflow foundation | 98/98 tests, 33/33 mutations, slice 2 PASS | P5 |
| 2026-10-16 | 2c | P5 connectors and tool gateway | 133/133 tests, 48/48 mutations | P6 |
| 2026-10-16 | 3 | Baseline reproduced. Added P6 documents/evidence, P7 policy RAG, P8 model gateway/capabilities, P9 AML graph, P10 reports/review/finalization/exports, P11 web UI, P12 evaluation/privacy/ops and P13 deployment definitions. Migrations 0005/0006; ADR-0017..0022; DEC-21..33 | Lint/mypy/Bandit/pip-audit clean; 198/198 tests; 103 release blockers; browser journey PASS; 82/82 mutations; images built and Compose smoke passed; traceability regenerated; development DB migrated to 0006 | Still-missing items and live integrations |
| 2026-10-19 | 4 | Removed unsupported transaction assumptions from mock AML recommendations; added regression test | Lint/typecheck passed; 199 tests passed; web build and browser test passed; API/worker rebuilt; corrected recommendations verified inside worker | Strengthen report narrative validation |
| 2026-10-19 | 5 | Completed provider type annotations and removed private AML test import warning | Editor reported no problems; Ruff and mypy passed; 199 backend tests and one browser test passed; web build passed; traceability regenerated | Strengthen report narrative validation |
| 2026-10-19 | 6 | Strengthened report narrative validation; added 12 regression tests; prevented rejected sections from being saved | Ruff/mypy passed; 211 pytest cases passed; JUnit counter 213; browser passed; traceability refreshed | Verify updated API/worker containers |
| 2026-10-19 | 7 | Verified regenerated report in rebuilt API/worker containers; increased local synthetic step budget to 40 | AI_DRAFT v4 created; deterministic validation passed; corrected analyst questions verified; workflow WAITING_REVIEW | Complete independent report review |
| 2026-10-19 | 8 | Completed synthetic independent review and report finalization | Reviewer approval recorded with MFA; report v4 FINALIZED; case COMPLETED; workflow SUCCEEDED; downstream status write disabled | Verify finalized report export and audit trail |
| 2026-10-19 | 9 | Verified approved report export and audit records | Export contained approved report v4, snapshot v3 and corrected analyst questions; audit recorded approval, finalization, export creation and download | Review remaining implementation gaps |
| 2026-10-20 | 11 | Completed graph typing cleanup, corrected generated LangGraph stubs and ran backend regression | Six stubs and 145 application files passed mypy; Ruff passed; 211 backend tests passed, one deselected; traceability regenerated | Refresh browser regression evidence |
| 2026-10-20 | 12 | Corrected remaining agent typing diagnostics and refreshed browser evidence | Ruff/mypy passed; 65 unit tests passed; browser journey passed in 16.15 seconds | Regenerate traceability and review remaining gaps |
| 2026-10-20 | 13 | Added evaluation metric counts, omitted scenarios, case slices and configuration snapshots; corrected test filename collision | Ruff passed; mypy passed for 146 files; 72 unit tests and 219 backend tests passed; one browser test deselected; traceability regenerated | Implement evaluation dataset separation |
| 2026-10-20 | 14 | Added dataset lineage, version, contamination and purpose controls; restricted promotion eligibility to locked release evidence | Ruff passed; mypy passed for 147 files; 92 unit tests and 241 backend tests passed; browser journey passed in 16.87 seconds; traceability regenerated | Prepare independent validation/release datasets; AER-030 remains partial |
| 2026-10-20 | 15 | Added dataset preparation tooling and verified provisional blocking-gap case through mock provider, real gateway, validator and persistence | Ruff passed; mypy passed for 147 files; 104 unit tests and 254 backend tests passed; one browser test deselected; traceability regenerated | Expand draft coverage and complete independent dataset review |
| 2026-10-20 | 16 | Added regression tests for unsupported customer facts and prohibited guilt/freezing claims | Ruff passed; mypy passed for 147 files; 106 unit tests passed | Complete independent dataset authorship and review |
| 2026-10-20 | 17 | Implemented supervisor case reopening with a new draft report and preserved completion history | Ruff passed; mypy passed for 148 files; 116 unit tests and 279 backend tests passed, including 13 reopening integration tests; OpenAPI endpoint verified; browser journey passed in 17.12 seconds | Add supervisor reopening UI and browser coverage |
| 2026-10-20 | 18 | Added supervisor reopening UI with permission/MFA hints, required reasons and stale-version handling; corrected browser context setup | Ruff passed; mypy passed for 148 files; 116 unit tests and 280 backend tests passed; TypeScript and web build passed; browser journey passed in 16.45 seconds | Refresh traceability and reconcile remaining gaps |
| 2026-10-21 | 19 | Added critical-incident response with capability stops after authorized declaration, preserved evidence, owner-role notifications and investigation-gated restoration | Ruff passed; mypy passed for 150 files; 128 unit tests and 306 backend tests passed, including 14 incident integration cases; browser journey passed in 20.31 seconds; OpenAPI and traceability regenerated | Development DB upgraded to 0007; worker single-tick check passed with no pending work; live routing and owner assignments pending |
| 2026-10-21 | 20 | Added operations dashboard, scoped KPI reporting and connector observations with migration 0008 | Ruff passed; mypy passed for 154 files; 143 unit tests and 331 backend tests passed, including 10 dashboard integration tests; TypeScript and web build passed; browser journey passed in 20.84 seconds; OpenAPI and traceability regenerated; development DB upgraded to 0008 | Docker API/worker/web rebuilt; local operator dashboard, 1/7/28-day views and expanded workflow steps visually checked; live connector and unmeasured metrics pending |
| 2026-10-21 | 21 | Added parser network/process restrictions and required memory enforcement; Windows hosts use a disposable network-disabled Linux parser container with no native fallback | Ruff passed; mypy passed for 156 files; unit suite: 163 passed, one skipped; backend suite: 351 passed, one skipped, one browser test deselected; Windows isolation check passed; API/worker rebuilt; deployed worker returned PARSED with all enforcement flags true; diagnostics passed; browser journey passed in 24.93 seconds | Parser guide and handoff reconciled; inspect Entra sign-in; broader Linux filesystem isolation and production acceptance pending |
| 2026-10-21 | 22 | Implemented Entra web sign-in, encrypted Redis sessions and API identity admission | Ruff passed; mypy passed for 157 source files; 32 authentication tests passed; unit suite: 179 passed, one skipped; backend suite: 376 passed, one skipped, one browser test deselected, including nine Entra identity integration tests; TypeScript and production web build passed; browser journey passed in 31.26 seconds; local Redis session checks passed; OpenAPI and traceability regenerated | Configure test Entra app registrations and trusted principal mappings; verify rebuilt containers, real sign-in and MFA; production acceptance remains pending |

## Google runtime verification - 2026-10-23

Local Google runtime verification is complete for the controlled test scope.

- Controlled real-browser server-session expiry: PASSED. The active Google Redis session TTL was deliberately shortened to 15 seconds; after expiry, refreshing the protected application redirected to `/signin?expired=1` and displayed the expired/missing-session message.
- This controlled test verifies application behavior after server-side session expiry. It does not claim that the browser was left untouched for the natural approximately one-hour maximum session lifetime.
- Docker images for `api`, `web`, and `worker` were rebuilt successfully from `docker-compose.yml` plus `compose.google.example.yaml` with the `app` profile.
- Rebuilt `api`, `web`, and `worker` containers were recreated successfully without recreating PostgreSQL or Redis.
- API post-rebuild runtime: PASSED. Docker reported the API container `running` and `healthy`; its configured `/health/live` check returned exit code 0.
- Web post-rebuild runtime: PASSED. The web container was healthy and the unauthenticated root request returned the expected redirect behavior.
- Worker post-rebuild runtime: PASSED. The worker was running with restart count 0.
- Recent `api`, `web`, and `worker` logs contained no matches for error, exception, traceback, fatal, or panic in the inspected tail.
- Real Google sign-in after rebuild: PASSED.
- Operations dashboard access for the provisioned `PLATFORM_OPERATOR`: PASSED.
- Case-content restriction for `PLATFORM_OPERATOR`: PASSED (`Not permitted` remained enforced).
- Application sign-out after rebuild: PASSED and returned the browser to `/signin`.
- Google login continues to have `mfa_satisfied=False`; MFA-protected actions remain blocked.

Remaining work is separate from this local Google runtime checkpoint: natural-duration session-expiry observation, qualified Google MFA step-up, real Microsoft Entra verification, and production/deployment acceptance.





### Source-adoption session - 2026-10-31

Adopted the verified quality-repair source and four reconciled test files with a reversible backup. Regenerated traceability from the recorded PostgreSQL 16 JUnit evidence. Runtime update remains the next step.


## Development runtime update - 2026-11-02

The canonical API and worker were switched to audited images built from the verified source. Both run as the original non-root user with pypdf 6.21.4 and pip 26.2.2. API liveness/database readiness, worker startup, dependency versions, public auth mode and the sign-in page were checked. The web, performance API, database, Redis and mock-bank containers were preserved. Old API/worker containers are retained stopped for rollback. Runtime report: `reports/runtime-switch-20261102T075936415646`.

No migration, seed, database reset, configuration promotion or cloud deployment was run. Existing queues may continue normal worker processing. Actual interactive sign-in, Operations and Evaluations screens still need post-update user checks. The host virtual environment and host parser image were not upgraded.


## Email/password source checkpoint - 2026-11-02

Adopted the provisioned email/password source from the isolated verification at
reports/password-login-verification-20261102T130403401449. PostgreSQL 16 verification
recorded 470 backend tests passed, one Windows-native Linux skip, one browser test
deselected and 15 subtests passed. Twelve focused password integration checks and
sixteen primary-provider identity checks passed. The Linux isolation probe executed
separately. All 86 web authentication tests, real Redis encrypted-session checks,
TypeScript, production web build, lint, types, static security and dependency audit
passed within their reported scope. Traceability was generated from that JUnit.

This checkpoint adopts source only. Migration 0012, API/web image rebuild and
controlled runtime activation remain outstanding. The main database, host virtual
environment and live services were not changed by source adoption. Password login
remains off until explicitly enabled in the API/web runtime. Existing Google login,
tenant membership, role assignments, case access and MFA requirements are preserved.
Credential provisioning/reset uses a hidden-password schema-owner console. Public
signup, reset-email delivery and qualified password-login MFA are not implemented.
Interactive email sign-in and production acceptance remain pending.


## Local password-login runtime activation - 2026-11-03

Created and archive-validated a user-DPAPI-encrypted database backup, applied only additive migration 0012, and switched the canonical API and web to the audited images. Password login is enabled alongside the unchanged primary provider. API readiness, both sign-in forms, unknown-credential rejection, anonymous case denial and service stability were checked. Worker, performance API and other services were preserved; old API/web containers remain stopped for rollback. No identity, role, case assignment or password credential was provisioned. Interactive email login and production acceptance remain pending. Report: `reports/password-login-activation-20261103T063919704367`.


## Local connector intake candidate - 2026-11-03

Email/password login was configured for the existing Synthetic Bank A analyst1.
User-provided UI evidence confirmed analyst navigation and access to the stored
completed report and case audit history. A separate read-only check at
reports/case-integrity-20261103T101833545917 recomputed the tenant's 165-event audit
chain and checked report v4, snapshot v3, decision/finalization binding and recorded
review separation. Historical MFA remains a stored-flag check, not live IdP proof.

The next candidate implements default-off local/test synthetic connector
credentials for POST /api/v1/alerts only, with short expiry, digest storage,
owner-only provisioning/revocation, current DB-resolved source permissions,
and no MFA or human-role grants. Migration 0013 adds only its credential table.
A local operator demo sends real HTTP intake, verifies idempotent replay, preserves
normal assignment, and revokes the credential. The fixture uses historical mock-
bank data; it does not start a workflow, approve a report or enable downstream writes.
Isolated PostgreSQL/Redis integration and full regression verification are pending.
No connector source was adopted, no migration was applied and no runtime was changed
by candidate preparation. Production machine-identity acceptance remains OPEN.


## Local synthetic connector source checkpoint - 2026-11-03

Adopted source from reports/connector-intake-verification-20261103T115154184350.
The resumed verification checked all 409 candidate file hashes before reusing
the original successful backend evidence. Results: 535 backend tests passed,
one Windows-native Linux skip, one browser deselection and 15 subtests passed;
28 connector integration/demo checks and 16 provider identity checks passed.
The Linux isolation probe, 86 web authentication checks, real Redis checks,
production web build, types, lint, static security and dependency audit passed.
Traceability was generated from the repaired and validated JUnit report.

Source adoption does not enable the connector or apply migration 0013. API image
preparation, database backup and controlled local activation remain outstanding.
Connector credentials remain local/test only, short-lived and scoped to alert
intake. Existing tenant roles, assignment rules, human review, MFA controls and
disabled downstream writes remain unchanged. A fresh live case has not yet been
created through this adapter. Enterprise machine identity acceptance remains open.


## Local connector runtime activation - 2026-11-03

Created and archive-validated a current-user DPAPI encrypted database backup, applied additive migration 0013 and switched only the canonical API to the audited image. The local/test-only connector flag is enabled. Readiness, existing primary and password sign-in configuration, unknown password/connector rejection, anonymous case denial and service stability were checked. Web, worker, performance API and other containers were preserved. The old API is retained stopped for rollback; additive schema is retained if services roll back. No credential, principal, role, assignment or case was created. Fresh live intake remains pending. Production machine identity acceptance remains open. Report: `reports/connector-activation-20261103T132850446724`.


## Local authenticator candidate - 2026-11-05

User-provided runtime evidence confirms connector activation at migration 0013,
one idempotently created synthetic case a7f76d78-0012-4c52-95b8-87693d7f49ac,
revoked temporary connector credentials, analyst2 sign-in and case access, worker
processing, evidence snapshot v1, draft report v1, submission and independent
reviewer1 access. The report retains an outdated proof-of-address limitation.
The pending request 7a44a728-b7fd-4b68-a13f-57753f7aa94e is not approved.

Prepared local/test authenticator step-up code under ADR 0032. Migration 0014,
factor enrollment, encryption-key configuration and runtime activation are NOT
applied by candidate preparation. Isolated PostgreSQL/Redis integration and web
verification remain pending. No reviewer decision, role change or MFA flag was
written to the live environment. Production MFA acceptance remains open.


## Local authenticator verified source checkpoint - 2026-11-05

Adopted source from reports/mfa-verification-20261105T074520528549.
Isolated verification recorded 585 backend tests passed, one Windows-native Linux
skip, one browser deselection and 15 subtests passed. All 25 focused MFA integration
checks and 16 provider identity regression checks passed. The Linux isolation
probe, 86 web authentication tests, real Redis encrypted-store checks, web types,
production web build, source lint/format/types, static security and dependency
audit passed within their reported scopes. Traceability derives from this JUnit.

This is source adoption only. Migration 0014, API/web image preparation, encryption
key configuration, local TOTP activation and private authenticator enrollment are
still pending. No live MFA grant, reviewer decision, role or assignment is created
by adoption. The synthetic review remains pending; enterprise MFA acceptance,
recovery and independent security acceptance remain open under ADR 0032/DEC-35.


## Local authenticator runtime activation - 2026-11-05

Archive-validated a current-user DPAPI encrypted database backup, applied additive migration 0014, and switched the canonical API/web to audited images. The API local/test TOTP verifier is enabled with a separately generated encryption key retained only as a DPAPI encrypted host backup and the API runtime secret environment. Existing provider and password entry points, API readiness, unknown credential rejection, anonymous denial, key encryption and service stability were checked. The worker, performance API and other containers were preserved. Old API/web containers remain stopped for rollback. No factor enrollment, MFA session grant, case decision, identity or role was created. Private enrollment and interactive authenticator verification remain pending. Institutional MFA acceptance and recovery remain open. Compose recreation must restore the same key privately; the overlay intentionally contains no key. Report: `reports/mfa-activation-20261106T051857070515`.


## Gmail verification candidate and mobile-access planning - 2026-11-07

The user selected Gmail codes and Android phone verification. There is no owned
FinAgent domain or mobile HTTPS endpoint, and the PC has no Bluetooth or available
platform authenticator. TOTP enrollment attempts were rejected; no successful
factor enrollment or new reviewer decision has been evidenced. Do not reuse or
record any setup key previously pasted in chat.

Prepared an isolated Gmail email-possession candidate under ADR 0033, with additive
migration 0015 and a same-origin email verification screen. It never grants MFA,
roles or approval authority. Targeted unit checks, source lint/type/static checks
have local results; full PostgreSQL/Redis integration, web regression, actual Gmail
configuration/delivery and runtime activation remain pending. No real email was
sent and no live service, database or credential was changed by candidate work.

ADR 0034 proposes a private mobile HTTPS route, potentially through user-configured
Tailscale Serve. No software, tailnet, proxy, public exposure or passkey credential
has been installed/configured. Passkey code and Android acceptance tests are still
outstanding. Current approval controls stay enforced; the pending case is unchanged.


## Gmail email-verification source checkpoint - 2026-11-09

Adopted source from reports/email-proof-verification-20261109T051108641187.
The resumed verifier checked all candidate source hashes, with an AST-equivalent
unit-test rename fixing a collection collision, before reusing the passed focused
integration evidence. Full backend verification recorded 620 passed, one Windows-
native Linux skip, one browser deselection and 15 subtests passed. All 16 email
integration and 16 provider identity checks passed. Linux isolation, 86 web
authentication tests, real Redis checks, web types/build, lint/format, source types,
static security and dependency audit passed within their reported scopes.
Traceability derives from the final full-backend JUnit report.

SMTP was replaced by a synthetic sink during integration tests; no live Gmail
delivery was verified. This adopts source only. Migration 0015, API/web builds,
private Gmail app-credential and HMAC-key configuration, controlled activation and
interactive email delivery remain outstanding. Email proof does not grant MFA or
reviewer authority. Phone passkeys and private mobile HTTPS remain unimplemented;
no Tailscale installation or network exposure was performed. Existing review and
tenant/role/MFA controls are unchanged. No email, approval decision or credential
was created by source adoption.


## Local Gmail email-verification runtime activation - 2026-11-09

Archive-validated a DPAPI encrypted database backup, applied additive migration 0015 and switched only the canonical API/web to audited images. Privately configured Gmail sender, app credential and keyed-hash secret were loaded into the API environment; the host copy remains user-DPAPI encrypted. Existing provider/password/TOTP settings were preserved. API readiness, anonymous denial, password capability, app-role email-table access, settings and service stability were checked. No email was sent by activation. Interactive Gmail delivery and code verification remain pending. Email possession never grants MFA or approval authority. No principal, role, assignment or review decision was changed. Worker/performance services were preserved; old API/web remain stopped for rollback. Phone passkeys and private mobile HTTPS remain outstanding. Report: `reports/email-activation-20261109T063933021191`.


## Phone passkey candidate - 2026-11-09

An isolated candidate adds feature-gated local/test WebAuthn enrollment, verification and revocation, additive migration 0016, owner-configured enrollment scope and a private mobile BFF interface. No live source, container, database, account or reviewer decision has been changed by this candidate work. Validation status must be generated from the isolated verifier JUnit; PostgreSQL/Redis execution and Android registration remain pending. The current mobile email/password entry remains operational. ADR 0035 documents exact-origin/session binding, five-minute grants and recovery limitations.


## Phone passkey verified source checkpoint - 2026-11-09

Source checkpoint from reports/passkey-verification-20261109T090912383919.
The isolated PostgreSQL/Redis run verified all 21 passkey integration tests and
16 provider identity checks. Full backend verification recorded 667 passed,
one Windows-native Linux skip, one browser deselection and 15 subtests passed.
The Linux network/process probe executed separately. Web verification recorded
88 authentication tests and 18 passkey BFF tests, real Redis session checks,
TypeScript and production build. Lint, format, source types, static security,
dependency consistency and Python/production-web dependency audits completed
within their reported scopes. Traceability was generated from the full JUnit.

This adopts source only: migration 0016, image builds and controlled runtime
activation remain outstanding. No passkey, MFA grant or reviewer decision is
created by adoption. Existing mobile email/password access stays in place until
the mobile container is separately updated. Android enrollment and signed
assertion acceptance remain pending. Institutional acceptance, recovery and
production readiness are not claimed. ADR 0035 and DEC-35/36 remain applicable.


## Local private phone passkey runtime activation - 2026-11-09

Archive-validated a user-DPAPI encrypted database backup, applied additive migration 0016 and updated the canonical API, desktop web and separate private mobile web to audited images. Existing Google/password/email/TOTP settings, roles, cases and private Serve configuration were preserved. Passkey enrollment is enabled only for the existing Synthetic Bank A reviewer1 principal on the configured private HTTPS origin. API readiness, app-role table access, anonymous denial, password infrastructure and desktop/mobile sign-in checks completed. No passkey, MFA grant, email or review decision was created by activation. Actual phone registration and assertion acceptance remain pending. Recovery and institutional acceptance remain open under ADR 0035. Old target containers are retained stopped for rollback; worker and performance services were untouched. Report: `reports/passkey-activation-20261109T131857668474`.
