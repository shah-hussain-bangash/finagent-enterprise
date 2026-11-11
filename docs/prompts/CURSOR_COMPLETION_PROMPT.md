# FinAgent Enterprise — Completion Prompt (P2/P3 completion through P16)

> Saved verbatim from the session-2 brief so later sessions can follow the same checklist.
> `CLAUDE.md` holds the always-on rules; `MASTER_BUILD_PROMPT_v2.md` is the full specification.

You are the lead engineer completing FinAgent Enterprise — Release 1: Human-Reviewed AML Investigation Copilot — in this repository. You may create, edit and refactor files and run terminal commands. Work as an engineering agent: deliver working, tested code, not explanations.

## 0. ORIENTATION (every session, before coding)

Read in order:
1. CLAUDE.md (non-negotiable invariants)
2. docs/implementation-status.md (phase table, verified results, Session log, next-phase plan)
3. docs/prompts/MASTER_BUILD_PROMPT_v2.md (full specification, sections 0–65)
4. docs/decisions/open-decisions.md and docs/traceability/completeness-audit.md
5. For the phase you are building:
   - Requirement IDs: docs/requirements/requirement-inventory.md and requirements.json
   - Exact source wording: grep docs/requirements/source-text/*.txt
   - Do not parse the PDFs.

Then run the baseline:
`make setup && make deps-up && make migrate && make seed && make lint && make typecheck && make test`
(on hosts without GNU make: `python scripts/tasks.py <same targets>`).
If the baseline is not green, fix it first and record what you fixed.

## 1. OPERATING LOOP (one phase per session)

1. Plan. List the phase deliverables from section 3 and map each to requirement IDs.
   - Category B (derived) decisions: write a new ADR in docs/adr/.
   - Category C (needs business/control owner): record in docs/decisions/open-decisions.md, with a conservative default behind configuration.
2. Dependencies. Add packages with pinned or bounded versions. Run pip-audit / npm audit after each addition. Never add a package with a known critical or high advisory without recording it.
3. Implement in small modules under src/finagent/<area>/. One package with module boundaries, per ADR-0002.
4. Test.
   - Add unit, integration, security and recovery tests tagged @pytest.mark.req("ID", ...).
   - Tag control tests @pytest.mark.release_blocker.
   - For every security control, temporarily break it, confirm a test fails, then restore it.
5. Gate. All of these must pass: make lint, make typecheck, make security, make test, and in apps/web: npm run typecheck && npm run build. Never weaken a test to make it pass.
6. Trace. Update docs/traceability/trace-map.yaml, then run make test to regenerate traceability. Never hand-edit generated traceability files.
7. Record. Update docs/implementation-status.md: phase status, real test numbers, a Session log row, the next phase's plan, an honest "Not verified" list.
8. Commit. Example message: "P4: fenced leases + outbox relay [FR-031 FR-034 DIR-031]".
9. Stop. End with a 10-line summary: what was built, test numbers, open issues, next phase.

## 2. HARD RULES

- Release 1 must never move money, freeze or block accounts, make credit decisions, change risk scores, determine guilt, make sanctions decisions, file SAR/STR, write banking records, invent policy, self-approve, or disclose investigation content to the customer.
- The only business write is the feature-flagged case-status update, OFF by default. LLM output never establishes tenant, role, permission, approval or authority.
- Mocks are allowed only for external systems: bank, Entra, Azure OpenAI, AI Search, Service Bus, Blob, notifications. Internal platform logic must really work.
- Every Azure adapter has a local adapter behind the same Protocol port, selected by configuration. The local adapter is the default in local and test.
- No real credentials. Without live Azure or Entra, build and test against mocks and record "LIVE INTEGRATION NOT VERIFIED".
- Never run terraform apply or Azure create/delete commands. Only terraform fmt, terraform validate, tflint and checkov.
- Never claim any of the following that did not actually happen or was not measured: tests passed, NFR targets met, compliance, certification, deployment, UAT, or approval.
- Stop and report instead of improvising when: a mandatory control cannot be implemented as specified, a release-blocker test cannot be fixed, or an OPEN decision has no safe conservative default.
- Keep existing Phase 0–3 work. Extend it; don't rewrite it unless a test proves it wrong.

## 3. PHASE CHECKLIST

A phase is done only when every listed test exists, passes, and is requirement-linked. Slice demos must exist as scripts in scripts/.

**P2/P3 completion:** PATCH /api/v1/cases/{id} with If-Match/record_version, stale → 409 (FR-004, UI-002) · supervisor reassignment with reason (FR-009) · escalation queue with due times, once per rule (FR-011) · explicit, time-bounded, audited delegation (FR-003) · MFA step-up for reviewer decisions (SPR-005) · break-glass role: disabled by default, auto-expiry, alert (SPR-008) · rate limiting and tenant quotas → 429 + Retry-After (NQ-028) · separate migrator and app DB roles; app role has INSERT-only on audit_events.

**P4 Workflow foundation (slice 2):** migration 0002 with composite tenant FKs: workflow_runs, workflow_steps, checkpoints, leases, inbox_events, dead_letters, external_operations · lease manager: guarded claim with monotonic fencing token, heartbeat, token-checked completion, expired-lease takeover · outbox relay (FOR UPDATE SKIP LOCKED) → queue port, with PostgresQueue (local) and AzureServiceBusQueue adapters · consumer inbox dedup; DLQ after N failures · POST /api/v1/workflows (202 + status URL, idempotent), GET /workflows/{id}, POST /workflows/{id}/cancel · LangGraph skeleton graph with a tenant-scoped PostgreSQL checkpointer in our schema · apps/worker entrypoint plus compose service · tests: concurrent claim; stale token rejected; lease-expiry takeover; crash between commit and publish; duplicate delivery; cancel mid-step; approval-after-cancel; graph-version mismatch on resume · slice 2 script: start run → kill worker → new worker resumes → no duplicate event or effect.

**P5 Connectors and tool gateway:** typed connector ports plus a synthetic mock-bank service covering alerts, customer/KYC, transactions (pagination + completeness metadata), documents, policies, sanctions summary, case-status (idempotent operation key), notifications · resilience: timeouts, bounded retry with backoff and jitter, circuit breaker, rate limit, freshness/as-of · ToolDefinition registry plus a Tool Gateway enforcing everything in prompt §12; read-only tools plus the single flag-guarded case-status write · source register docs/data/source-register.md · contract tests per connector: pagination, empty, timeout, 403, stale, duplicate, revocation, schema version · negative tests: money transfer, freeze, role change, arbitrary URL, widened subject/time window — each denied and audited (SPR-029, AER-025).

**P6 Documents and evidence:** POST /api/v1/documents (type + magic-byte check; 20 MiB → 413; scanner port: ClamAV local / Defender for Storage in Azure; quarantine; blob port: local filesystem/Azurite + Azure Blob) · parse PDF, DOCX and CSV in a resource-limited subprocess; never execute macros · OCR port: Tesseract locally, Azure Document Intelligence optional · typed extraction with field-level provenance · versioned EvidenceSnapshot with provenance, freshness and classification · transaction completeness proof (DIR-012) · versioned calculation rules (extend domain/money.py) · short-lived scoped download links (SPR-012) · tests: malicious, oversized and encrypted files; a corrected source record creates a new evidence version and invalidates any affected approval.

**P7 RAG:** ingestion states RECEIVED → SCANNED → PARSED → INDEXED / FAILED / DELETED · section-aware chunking · embeddings port: deterministic local + Azure OpenAI · search port: LocalSearchAdapter (BM25 + hashed vectors, ADR-0007) + Azure AI Search hybrid · ACL and effective-date filtering inside retrieval; rerank, dedup, token budget · citation validator: existence, version, current access, location, semantic support · revocation and deletion invalidate the index and cache · POST /knowledge/search returns {answer, citations, evidence_gaps, policy_version, abstained} · tests: known-answer; no-answer → abstain; expired vs current policy version; restricted policy (no leakage in snippet, citation or cache); conflicting policies; prompt injection embedded in policy text.

**P8 Agents and model gateway:** Model Gateway (prompt §11): AzureOpenAIModelProvider + scripted MockModelProvider; budgets per run, tenant and month; redaction and lineage · fail-closed agent registry (prompt §8) · Pydantic common contract (prompt §10) with one bounded repair attempt · agents: AG-KYC (checklist item states), AG-AML (deterministic calculations only), AG-COMPLIANCE (RAG only; abstains without a source), AG-REPORT, CAP-DOCUMENT, AG-RISK (registered but disabled) · versioned prompt files src/finagent/agents/*/prompts/vN.md · tests: every fixture listed in AER sections 3–7.

**P9 Full AML graph:** LangGraph per prompt §7: authorize → evidence → parallel KYC/AML/Compliance → aggregate → validate → gap? → draft → WAITING_REVIEW interrupt · routing only on validated state · guards on steps, tokens, cost and deadline · ReportVersion with claim↔evidence links · analyst edit, submit and withdraw (FR-027) · E2E tests with the mock model: normal, missing evidence, contradictory evidence, injected instructions.

**P10 Approvals and finalization (completes slice 3):** decisions APPROVE, RETURN_FOR_REWORK, REQUEST_INFORMATION, REJECT (effect set by config, DEC-11) · SoD, delegation and MFA · approval bound to evidence snapshot and report version; invalidated on change · finalization: APPROVED → FINALIZING → idempotent case-status write via ExternalOperation ledger plus reconciliation → COMPLETED; ambiguous → OUTCOME_UNCERTAIN · POST /operations/{id}/reconcile · tests: self-approval denied; stale and expired approvals denied; double submit; timeout after external commit → exactly one effect; replay suite with zero duplicate writes · full slice 3 script: alert → evidence → agents → cited report → review → authorized status update → audit trail.

**P11 Frontend:** Entra OIDC sign-in (Auth.js) plus a local dev sign-in hard-disabled outside local · typed client generated from docs/api/openapi.json (openapi-typescript) · every screen from prompt §23 (1–15) · findings labelled FACT / DETERMINISTIC RESULT / AI INTERPRETATION / GAP · citations open the exact cited revision · Vitest unit tests · Playwright E2E for analyst, reviewer, restricted-user and citation-inspection journeys, with @axe-core/playwright checks (WCAG 2.2 AA target).

**P12 Evaluation:** versioned synthetic scenario families in evaluation/datasets: 100 first, 200 target, split and sliced per AER §10 · contamination log · runners and metrics with numerators and denominators (AER-032) · gates in evaluation/gates.yaml, marked PROPOSED · run model-driven cases 3× · POST /evaluations plus an Evaluation Dashboard · show N/A where a metric does not apply; never claim results for runs that did not happen.

**P13 Hardening:** full security suite (prompt §26) and fault-injection suite (prompt §30) · OpenTelemetry for FastAPI, SQLAlchemy and httpx, Azure Monitor exporter behind a flag · auth-protected /metrics · dashboards and alert rules as code in monitoring/ · runbooks per prompt §37 · k6 or Locust scripts for the NQ §2 profile, labelled "local smoke, not NFR evidence" · the missing documents from prompt §45.

**P14 Infrastructure:** Terraform modules with dev, staging and prod tfvars (prompt §33) · Helm charts (prompt §34) · run terraform fmt -check, terraform validate, tflint, checkov, helm lint, kubeconform · no apply.

**P15 CI/CD:** extend .github/workflows with image build, syft SBOM, trivy image scan, cosign keyless signing, Terraform plan via OIDC federated credential, staging deploy + migrations + smoke tests, agent and RAG evaluation gates, manual approval environment, canary and rollback.

**P16 UAT and handover:** docs/operations/uat-scripts.md built from PUC-01..19 · go-live-checklist.md, handover.md, training notes, rollback guide · final completion report per prompt §54.

## 4. FINAL ACCEPTANCE

Following only the README, a new developer can:
1. Clone, configure, start dependencies, migrate and seed.
2. Sign in as the synthetic analyst, open the alert's case and run the investigation.
3. View evidence, inspect the cited AI findings and submit for review.
4. Sign in as the reviewer and approve, reject or return the case for rework.
5. See the final authorized case status and inspect the complete audit trail.
6. Run all test and evaluation suites from documented commands.

`make test-release-blockers` must be 100% green. Institutional sign-offs listed in the open-decisions register remain open; never report them as done.
