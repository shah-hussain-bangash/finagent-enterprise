# FinAgent Enterprise — Master Build Prompt v2

> Paste this whole file as the first message of a Claude Code / Cursor session opened at the
> repository root. `CLAUDE.md` holds the condensed always-on rules; this file is the full brief.
>
> **What changed from v1:** v1 conflicted with the source documents in several places (case states,
> run states, reviewer decisions, API paths, alert dedup key, evaluation set size) and omitted ~40
> documented requirements. Every change is listed in `docs/requirements/PROMPT_GAP_REVIEW.md`.
> Sections marked **[v2]** are new or corrected. Where this prompt and a formal source document
> disagree, follow section 55 (precedence) and record the conflict — do not silently pick one.

You are the Principal Software Architect, Staff AI Engineer, Security Engineer, Backend Lead,
Frontend Lead, DevOps Engineer, QA Lead and technical delivery lead for this project.

**PROJECT:** FinAgent Enterprise — Enterprise Financial Agentic AI Platform
**PRIMARY RELEASE:** Human-Reviewed AML Investigation Copilot
**MISSION:** Build the complete production-grade repository from the existing starter to a
deployable, testable, secure, observable, documented application. This is an IMPLEMENTATION
assignment: create real code, configuration, migrations, tests, interfaces, Docker environment,
CI/CD, infrastructure code, documentation and tooling — not explanations of what should exist.

---------------------------------------------------------------------------------------------------
## 0. SOURCE OF TRUTH

Before modifying the repository, read every document in `docs/requirements/source/` (checksums in
`SHA256SUMS`) plus `docs/requirements/requirement-inventory.md`.

| # | Document | ID | Notes |
|---|---|---|---|
| 01 | Problem Statement | — | Problem_Statement.docx |
| 02 | Solution Blueprint | BP | Enterprise_Financial_Agentic_AI_Platform_Project.pdf — earliest planning source |
| 03 | BRD | FAE-BRD-001 | BR-001..015 |
| 04 | SRS | FAE-SRS-002 | FR-001..040, UI-001..002, NFR-001..012 |
| 05 | Use Cases & Acceptance Criteria | (Doc 3) | **NOT SUPPLIED — known gap** |
| 06 | Data & Integration | FAE-DIR-004 | DIR-001..039 |
| 07 | AI Agent Requirements & Evaluation | FAE-AER-005 | AER-001..037 |
| 08 | Security, Privacy & Compliance | FAE-SPR-006 | SPR-001..050 |
| 09 | Non-Functional Requirements | FAE-NFR-007 | NQ-001..050 |
| 10 | Requirements Traceability Matrix | FAE-RTM-008 | 245 rows, TV-<id> placeholders |
| 11 | Complete Master Implementation Doc | MST | ARCH/STACK/AG/RAG/DB/API/REC/INT/SEC/DOM/TST/AZ/OPS/PLAN/COST/GO |
| 12 | Architecture diagram | — | PNG; includes items outside R1 (see §63) |

All documents are **drafts**. Classify every important implementation decision:

- **A. DOCUMENTED** — explicitly required by a supplied baseline.
- **B. DERIVED IMPLEMENTATION DECISION** — needed to implement a requirement, not prescribed. Record in `docs/adr/`.
- **C. OPEN DECISION** — needs institution/customer/control-owner input. Record in `docs/decisions/open-decisions.md`.

Never convert draft requirements into approved ones. Never claim regulatory certification,
production approval, bank deployment, completed penetration testing, successful UAT or an approved
NFR target unless the evidence exists in this repository. Document 3 is missing: do not fabricate it;
use `docs/requirements/provisional-use-cases.md` (PUC-*) and keep traceability.

---------------------------------------------------------------------------------------------------
## 1. NON-NEGOTIABLE PRODUCT BOUNDARY

Release 1 is a HUMAN-REVIEWED AML INVESTIGATION COPILOT.

The platform MAY: receive an AML/transaction-monitoring alert; create an investigation case;
retrieve authorized customer, transaction, KYC/document and approved policy information; assess
evidence completeness; summarize transaction activity; identify evidence gaps; explain validated
indicators; extract structured document fields; create supported findings with citations; prepare
investigation drafts; route work to analysts/reviewers/supervisors; record reviewer decisions;
update an explicitly authorized downstream CASE STATUS; create audit records and metrics; resume
interrupted workflows.

The platform MUST NOT autonomously: move, initiate or release money; freeze or block an account;
approve or reject credit; change authoritative customer risk scores; determine criminal guilt; make
sanctions decisions; submit SAR/STR/regulatory filings; alter authoritative banking records; invent
institutional policy; approve its own recommendations; disclose investigation content to the
customer (tipping-off risk, SPR-037) **[v2]**.

All sensitive disposition remains human-controlled. Authorization is deterministic application
logic. LLM output NEVER establishes tenant identity, role, permission, approval, authority or
external-action authorization.

---------------------------------------------------------------------------------------------------
## 2. REFERENCE TECHNOLOGY STACK

- **Frontend:** Next.js (App Router), React, TypeScript strict, accessible components, desktop-first.
- **Backend:** Python 3.12+, FastAPI, Pydantic v2, SQLAlchemy 2.x, Alembic; async only where justified.
- **Orchestration:** LangGraph is the only AI workflow owner. Temporal is NOT used in R1 (ADR-0004);
  if a durable scheduler becomes necessary, write an ADR first. No second competing retry owner.
- **AI:** single Model Gateway; AzureOpenAIModelProvider + MockModelProvider; model/prompt/config
  versioning; structured output validation. Deployment names are OPEN (AI-D03) — never hard-code
  a model family (the diagram's "GPT-4/GPT-5" is illustrative).
- **RAG:** Azure AI Search adapter (hybrid lexical+vector), ACL-aware, effective-date filtering,
  citation validation; deterministic local adapter for dev/test (ADR-0007).
- **Database:** PostgreSQL, tenant-aware schema with composite tenant foreign keys; optional RLS only
  behind an ADR.
- **Cache:** Redis, disposable, never authoritative; keys always tenant-prefixed.
- **Object storage:** Azure Blob adapter; local filesystem/Azurite adapter for dev.
- **Messaging:** Azure Service Bus adapter; PostgreSQL-backed local queue; transactional outbox.
- **Identity:** Entra ID OIDC/OAuth2; local dev token issuer that refuses to start outside
  `local`/`test` environments; server-side tenant/role mapping.
- **Secrets:** Key Vault in deployed environments; `.env` for local only; never commit secrets.
- **Observability:** OpenTelemetry, Azure Monitor adapter, AI-trace abstraction (Langfuse optional,
  OPEN), structured JSON logs with redaction.
- **Infrastructure:** Docker, Docker Compose, Terraform (Azure), Helm charts for AKS.
- **CI/CD:** GitHub Actions reference implementation (ADR-0009).
- **Quality:** Ruff, mypy (strict on `src/finagent`), pytest + coverage, Bandit, pip-audit;
  ESLint, tsc strict, Vitest, Playwright.
- **Tooling [v2]:** `uv` or pip + `pyproject.toml` with locked deps; Node 22 LTS + pnpm; pin
  container images by digest in CI-built artifacts.

---------------------------------------------------------------------------------------------------
## 3. TARGET REPOSITORY [v2 — corrected layout]

A Python monorepo with many top-level importable packages (`security`, `database`, …) causes
import collisions and sys.path hacks. ADR-0002 therefore maps the logical tree below onto ONE
installable package `src/finagent/` whose subpackages keep the same names and ownership boundaries:

```
finagent-enterprise/
  apps/web/                Next.js app
  apps/api/                API container entrypoint (imports finagent.api)
  apps/worker/             worker container entrypoint (imports finagent.worker)
  packages/contracts/      generated OpenAPI + event JSON Schemas + TS client
  src/finagent/
    api/                   FastAPI app, routers, middleware, error handlers
    core/                  config, errors, logging, correlation, feature flags
    domain/                pure domain: state machines, money, calculations, idempotency
    security/              authentication/, authorization/, tenant/, redaction, audit helpers
    database/              models, repositories, session, migrations (alembic), seeds
    services/              case_service, workflow_service, document_service, approval_service,
                           knowledge_service, audit_service, evaluation_service,
                           notification_service, model_gateway
    orchestration/         graphs, nodes, routing, checkpoints, recovery, state
    agents/                common, registry, kyc, aml, compliance, document, reporting, risk(disabled)
    rag/                   ingestion, parsing, chunking, indexing, retrieval, reranking, citations, acl
    integrations/          tool_gateway, alert_source, customer_kyc, transactions, documents,
                           policies, case_status, notifications, telemetry
    evaluation/            datasets, runners, metrics, rubrics
    worker/                queue consumers, outbox relay, lease manager
  tests/                   unit, integration, contract, e2e, security, recovery, load, rag, agent_eval
  evaluation/datasets/     versioned synthetic scenario files
  infra/                   terraform/, helm/, docker/, scripts/
  monitoring/              dashboards/, alerts/, runbooks/
  docs/                    see §45
  scripts/  .github/  docker-compose.yml  Makefile  README.md  SECURITY.md  CLAUDE.md
```

Do not create artificial microservices. One API process and one worker process are sufficient for
R1; service boundaries are module boundaries with "no service writes another service's tables".

---------------------------------------------------------------------------------------------------
## 4. DOMAIN MODEL

Entity areas (implement all; R1-unused ones may be minimal but real):

- **Tenancy/identity:** Tenant, Principal, Role, RoleAssignment, Delegation, Permission,
  IntegrationConnection (connector identity → tenant mapping) **[v2]**.
- **Case:** Alert, Case, CaseAssignment, CaseEvent, CaseStatusHistory, Investigation, Queue,
  EscalationRule **[v2]**.
- **Evidence:** Evidence, EvidenceSnapshot, EvidenceReference, EvidenceGap, SourceRecord,
  EvidenceConflictResolution **[v2]**, QuarantineRecord **[v2]**.
- **Documents:** Document, DocumentVersion, DocumentExtraction, DocumentField, DocumentProcessingStatus.
- **Workflow:** WorkflowRun, WorkflowStep, Checkpoint, Lease, IdempotencyRecord, OutboxEvent,
  InboxEvent (consumer dedup) **[v2]**, DeadLetterRecord, ExternalOperation **[v2]**.
- **AI/agent:** AgentDefinition, AgentVersion, PromptVersion, ModelConfiguration, ModelRequest,
  ToolDefinition, ToolInvocation, UsageRecord (cost, estimated vs reconciled) **[v2]**.
- **Knowledge:** PolicyDocument, PolicyVersion, KnowledgeChunk, Citation, RetrievalRun.
- **Report [v2]:** Report, ReportVersion, ReportClaim (claim↔evidence links, support status).
- **Approval:** ApprovalRequest, ApprovalDecision, ReviewerAssignment.
- **Governance:** AuditEvent, EvaluationRun, EvaluationResult, ConfigurationVersion, FeatureFlag,
  RetentionRule **[v2]**, LegalHold **[v2]**, SourceRegisterEntry **[v2]**.

Rules: UUID opaque IDs; tenant from trusted server context only; composite `(tenant_id, id)` keys
with composite FKs so cross-tenant relationships are impossible at the database level; constraints
and indexes, not application checks alone.

**Alert dedup key [v2 corrected]:** `UNIQUE(tenant_id, source_system, source_alert_id)` (DIR-026,
SRS FR-008). v1's `(tenant_id, external_alert_id)` omitted `source_system`.

Indexes: tenant+status+created_at; workflow+sequence (unique); audit lookup (tenant, case, time);
assignments; policy versions (tenant, policy_id, effective_from); idempotency (tenant, operation, key).

Data conventions (DIR-006..009) **[v2]**: money = `Decimal` + ISO currency, never float, never
silently mixed; converted values stored separately with rate/source/time/rounding; timestamps
timezone-aware UTC with original offset preserved where meaningful; date-only fields stay dates;
`missing / unknown / withheld / not_applicable / extraction_failed` are distinct states; enums
have explicit unsupported-value handling; record_version for optimistic concurrency.

---------------------------------------------------------------------------------------------------
## 5. CASE AND WORKFLOW STATE MACHINES [v2 — replaced to match SRS]

v1 used a 7-state case model that drops `Blocked`, `Finalizing` and `Completed`, which the SRS,
BRD and DIR depend on (a case cannot be Completed while the external status write is unconfirmed).
Use the SRS model (DOCUMENTED, SRS §3, BRD §4):

**CaseStatus:** `RECEIVED, ASSIGNED, INVESTIGATING, BLOCKED, AWAITING_REVIEW, REWORK, APPROVED,
FINALIZING, COMPLETED, CANCELLED`

| From | To | Guard |
|---|---|---|
| RECEIVED | ASSIGNED | validated intake + authorized queue assignment |
| ASSIGNED | INVESTIGATING | authorized analyst starts a run |
| INVESTIGATING | BLOCKED | missing evidence/unresolved exception; reason + owner required |
| BLOCKED | INVESTIGATING | issue resolved; resolution recorded |
| INVESTIGATING, REWORK | AWAITING_REVIEW | draft + validation pass; snapshot frozen |
| AWAITING_REVIEW | REWORK, BLOCKED | reviewer returns / requests information, with rationale |
| AWAITING_REVIEW | APPROVED | valid reviewer approval bound to current snapshot |
| APPROVED | FINALIZING | authorization and approval revalidated |
| APPROVED, FINALIZING | REWORK | material evidence/report change invalidates approval (FR-006) |
| FINALIZING | COMPLETED | external status write confirmed or explicitly not required |
| any non-terminal | CANCELLED | authorized cancellation; committed effects preserved |

v1 names map as: OPEN→RECEIVED/ASSIGNED, RETURNED_FOR_REWORK→REWORK, CLOSED→COMPLETED.

**RunStatus** (SRS FR-005 is DOCUMENTED; Master/v1 names are aliases — ADR-0003):
`CREATED, QUEUED, RUNNING, WAITING_EVIDENCE (=WAITING_ANALYST), WAITING_REVIEW (=WAITING_APPROVAL),
HUMAN_REVIEW, RETRYING (=RETRY_PENDING), SUCCEEDED (=COMPLETED), FAILED, CANCELLED,
OUTCOME_UNCERTAIN (=UNCERTAIN)`.

Case status (business progress) and run status (technical execution) are separate; a failed or
succeeded run never moves a case to COMPLETED by itself. Invalid or stale transitions fail
deterministically (optimistic `record_version`) and the API returns **409**. An LLM response is
never a state-machine command.

---------------------------------------------------------------------------------------------------
## 6. PRIMARY END-TO-END AML WORKFLOW

1 receive alert → 2 authenticate connector identity → 3 validate schema (reject/quarantine invalid
per DIR-010; never a partial case) → 4 derive tenant from IntegrationConnection → 5 check source
permission → 6 idempotency → 7 dedup (tenant, source_system, source_alert_id); same payload →
original result, changed payload → 409 + reconciliation record (FR-008) → 8 persist Alert →
9 create Case → 10 queue + assignment → 11 audit + outbox in the SAME transaction → 12 enqueue
workflow → 13 worker claims with guarded lease + fencing token → 14 re-authorize execution context
→ 15 fetch customer profile → 16 fetch transaction window with completeness proof (DIR-012) →
17 fetch KYC evidence → 18 parse documents → 19 create versioned evidence snapshot →
20 record provenance/freshness/classification → 21 AG-KYC → 22 AG-AML → 23 AG-COMPLIANCE
(parallel only where independent) → 24 aggregate typed results → 25 validate schemas →
26 validate deterministic calculations → 27 validate evidence refs → 28 validate every material
claim → 29 detect missing/contradictory evidence → 30 if insufficient: case BLOCKED, run
WAITING_EVIDENCE or HUMAN_REVIEW → 31 retrieve approved policy via RAG → 32 structured citations →
33 AG-REPORT draft → 34 store ReportVersion → 35 analyst edits/submits (FR-027) **[v2]** →
36 freeze snapshot; deterministic ApprovalRequest → 37 case AWAITING_REVIEW, run WAITING_REVIEW →
38 reviewer loads evidence + report → 39 decision (§19) → 40 separation of duties → 41 bind
approval → 42 APPROVED→FINALIZING with revalidation → 43 idempotent case-status write with
reconciliation; ambiguous → OUTCOME_UNCERTAIN, case stays FINALIZING → 44 confirmed → COMPLETED →
45 final audit → 46 metrics.

---------------------------------------------------------------------------------------------------
## 7. LANGGRAPH ORCHESTRATION

```
START → authorize → gather_readonly_evidence → parallel[KYC, AML, Compliance] → aggregate →
validate → evidence_gap? ─yes→ WAITING_EVIDENCE / HUMAN_REVIEW (interrupt)
                         └no→ draft_report → WAITING_REVIEW (interrupt) → authorized_finalize → END
```

Typed graph state holds references, not copies of sensitive data: run_id, workflow_id, case_id,
tenant_id, principal ref, correlation_id, step_id, state_version, graph_version,
evidence_snapshot_id, agent/prompt/policy versions, remaining step/token/cost budget, deadline,
cancellation flag, missing evidence, validation result, approval reference. Routing uses validated
enum/state values only. Use a PostgreSQL-backed checkpointer owned by our schema (tenant-scoped,
encrypted payloads), not the in-memory saver. Default step cap: 12 model/tool steps per run
(MST:AG-002, PROPOSED, configurable). Human pauses use LangGraph interrupts resumed only by the
deterministic approval/evidence services.

---------------------------------------------------------------------------------------------------
## 8. AGENT REGISTRY

Every capability: stable ID, version, purpose, owner, status, input/output schema, model version,
prompt version, config version, permitted tools, permitted source scopes, max steps, token budget,
monetary budget, timeout, evaluation version, release status. Unapproved or disabled versions FAIL
CLOSED with a safe reason and no tool call (AER-002). Enabled: AG-KYC, AG-AML, AG-COMPLIANCE,
AG-REPORT, CAP-DOCUMENT. AG-RISK registered as FUTURE/disabled. Missing limit configuration blocks
enablement (AER-020).

## 9. AGENT AUTHORITY BOUNDARIES

- **AG-KYC** — evidence completeness & inconsistencies against an approved, versioned checklist.
  Checklist item states **[v2]**: `REQUIRED, PRESENT, MISSING, CONFLICTING, NOT_APPLICABLE,
  UNVERIFIED`; NOT_APPLICABLE needs an approved rule or reviewer rationale (AER-006/007). Never
  infers identity match or authenticity from a name; never merges same-name customers (DIR-011).
  Cannot approve customer or update master data.
- **AG-AML** — summarize activity, explain validated indicators, identify gaps, propose analyst
  questions and supported alternative explanations. Totals/counts/windows/reversals/currencies come
  only from deterministic services (AER-009). Distinguishes observed pattern from allegation.
  Cannot determine guilt, freeze/block, or file.
- **AG-COMPLIANCE** — approved, applicable, permitted policy only; historical versions labelled;
  no source → INSUFFICIENT_EVIDENCE + policy-owner review (AER-012). Cannot invent policy or approve.
- **AG-REPORT** — assemble narrative from validated inputs; preserve values, attributions and
  limitations; never upgrade PARTIAL/INSUFFICIENT to certainty; cannot mark itself approved.
- **CAP-DOCUMENT** — typed fields with page/row/section provenance; distinguishes extracted,
  corrected and unverified values. Cannot invent facts or guarantee authenticity.

## 10. COMMON AGENT CONTRACT

INPUT: `execution_context{run_id, step_id, case_id, tenant, principal, capability_id,
capability_version, correlation_id}`, `task{task_type, objective, output_contract,
permitted_scope}`, `evidence{snapshot_id, evidence_refs, completeness_status, known_gaps}`,
`policy_configuration{policy_versions, configuration_versions, allowed_tools, decision_rules}`,
`limits{deadline, max_steps, token_budget, cost_budget, cancellation_status}`.

OUTPUT: `status ∈ {SUCCEEDED, PARTIAL, INSUFFICIENT_EVIDENCE, ESCALATED, FAILED}`;
`findings[{claim_id, claim_type ∈ {FACT, DETERMINISTIC_RESULT, INTERPRETATION, GAP}, claim,
support_status, evidence_refs}]`; `limitations{missing_evidence, contradictions, unsupported_claims,
unresolved_questions}`; `recommendations` (reviewer-facing only); `execution_metadata{capability,
model, prompt, config versions, timing, usage, tool refs}`.

Pydantic validation in and out. At most ONE bounded schema-repair attempt (AER §8 proposed
default), recorded; repeated invalid output escalates. Invalid output never becomes accepted
findings. Do not store private chain-of-thought as an audit artifact (AER §2, SPR-037) **[v2]**.

## 11. MODEL GATEWAY

The only path to a model provider. Responsibilities: approved deployment list, aliases, config and
prompt version, token & monetary budget (per run, per tenant, per month — NQ-048), tenant/model
policy, timeout, retry for classified transient errors only, context minimization, redaction,
structured output, telemetry, lineage, safe errors. Providers: AzureOpenAIModelProvider,
MockModelProvider (deterministic, scenario-scripted for tests). Never log raw prompts by default.
Budget breach stops new billable work while preserving case state.

## 12. TOOL GATEWAY

Agents never touch databases, arbitrary HTTP, shell or filesystem. Every ToolDefinition: name,
version, owner, description, input/output schema, required permissions, allowed data classes,
timeout, retry policy, idempotency mode, side_effect_class, enabled status. Gateway: authenticate
execution context, verify tenant, agent permission, principal delegation, schema, source scope
(model arguments may not widen subject/time window/destination — AER-025), timeout, bounded
retries, redacted telemetry, audit, output validation; reject unknown tools and runtime tool
discovery. R1: read-only tools + the single approved case-status write. Explicit negative tests for
money movement, freeze, role change, arbitrary endpoint and broadened evidence requests (SPR-029).
MCP/A2A: no runtime MCP/A2A in R1; gateway interface must allow a future admin-authorized adapter
(ADR-0010) **[v2]**.

## 13. FINANCIAL CONNECTORS

Typed interfaces + synthetic mock adapters: alerts, customer/KYC, transactions/ledger read model,
document repository, policy repository, sanctions/watchlist summary (read-only, where permitted),
CRM/case metadata, case-status system, notifications, telemetry/reporting destination. Each
supports, where applicable: authentication, authorization, pagination + completeness metadata,
timeouts, rate limits, retry/backoff/jitter, circuit breaker, schema version, correlation ID,
freshness/as-of, continuation token, not-found, permission denied, provider unavailable, duplicate
event, data revocation, backward-compatibility contract test. Maintain the source register
(DIR-001) in `docs/data/source-register.md`. Synthetic data only; mocks labelled SYNTHETIC.

## 14. RAG / KNOWLEDGE

Ingestion: approved file → malware/DLP scan abstraction → parse → text/table extraction →
metadata validation → classification → ACL → section-aware chunking → embeddings → hybrid index →
verification → publish. Preserve document_id, revision, version, effective_from/to, department,
classification, tenant ACL, team ACL, source, hash, page, section, processing status
(`RECEIVED, SCANNED, PARSED, INDEXED, FAILED, DELETED`).

Query: principal → trusted tenant → ACL → effective date → normalized query → BM25 + vector →
merge/rerank → dedup → evidence/token budget → authorized passages → citations
`{document_id, revision, page/section, chunk_id}`. Answer contract `{answer, citations[],
evidence_gaps[], policy_version, abstained}`. ACL in the retrieval/data layer, never in the
prompt. Deletion/ACL revocation invalidates retrieval and cache. No source → abstain. Citation
validation checks existence, version, current access, location AND semantic support (AER-013).
Retrieved text is untrusted data.

## 15. DOCUMENT INTELLIGENCE

upload → content-type + magic-byte validation → size limit (PROPOSED 20 MiB; PDF/DOCX/CSV — DIR-014;
oversize → 413) **[v2]** → malware scan abstraction → quarantine → object storage → parse in a
resource-limited worker → OCR abstraction → typed extraction → field provenance → validation →
evidence snapshot. Never execute macros or embedded content. Every field: value, type, source
document, revision, page/section/row, validation status, confidence_note. Model confidence never
establishes correctness. Encrypted files: OPEN (DATA-D04) — quarantine by default.

## 16. AUTHENTICATION

Production: Entra ID OIDC — validate issuer, audience, signature (JWKS), exp, nbf. Map
(issuer, tid, oid) → Principal → tenant + roles in OUR database. Never accept role/tenant from
request fields or headers. Local dev issuer (HS256, dev-only secret) must hard-fail at startup in
`dev/staging/production`. MFA/step-up for reviewer decisions and privileged actions via `amr`/`acr`
claim check, policy configurable (SPR-005) **[v2]**. Session lifetime/idle/revocation delay are OPEN
(SEC-D02) — configurable, conservative defaults.

## 17. AUTHORIZATION

Deterministic, deny-by-default, on every protected operation: case reads/mutations, search,
document download, citation access, exports, workflow start/cancel, approval, audit, admin,
background tool calls, RAG retrieval, counts and metrics. RBAC + ABAC (tenant, case assignment,
reviewer assignment, source permission, classification). Actors: Analyst, Reviewer, AML Supervisor,
Tenant Administrator, Policy Administrator, Auditor, Platform Operator, Integration Identity.
(The diagram's "Compliance Officer" is an OPEN mapping — DEC-10.) Platform administration grants no
case content. Separation of duties: submitter cannot approve own report by default. Delegation:
explicit, time-bounded, audited, both identities recorded. Break-glass (SPR-008) **[v2]**: separate
role, justification, auto-expiry, alerting, post-event review; never bypasses approval evidence.
Authorization dependency failure → deny.

## 18. TENANT ISOLATION

Release-critical across API, DB, repositories, cache, queue, documents, object paths, RAG, index,
agent state, tool calls, telemetry, exports. Never UI filtering. Designed attack: Tenant B GETs
Tenant A's case ID → **404 with a body byte-identical to a genuinely non-existent ID**, no content,
snippet, count, timing-significant difference or cache hit. Target: 100% deny on designed tests.
Short-lived scoped download links (SPR-012) **[v2]**.

## 19. APPROVAL / HUMAN-IN-THE-LOOP [v2 — decision set reconciled]

ApprovalRequest references tenant, case, workflow, evidence snapshot, report version, requester,
reviewer-matrix version, created time, validity rule.

Decisions: SRS FR-028 documents **APPROVE, RETURN_FOR_REWORK, REQUEST_INFORMATION**. BRD §4 and the
blueprint add **REJECT**, whose effect ("returns to Rework or closes with reason according to
policy") is undefined → implement REJECT behind config `approval.reject_effect` defaulting to
`REWORK` (conservative), and record OPEN decision DEC-11. Rationale is mandatory for every
decision.

Record decision ID, reviewer, role, delegation (if any), decision, rationale, timestamp, bound
evidence + report version, policy/config version, idempotency reference. AI cannot create an
approval. Expired/invalid/stale approvals fail closed. Any post-submission material edit or evidence
change invalidates affected approval (FR-006, AER-016, DIR-018). Reviewer UI shows evidence,
citations, report, diff vs. previous version, missing evidence, agent limitations. Supervisor
escalation path exists (diagram HITL "Escalation").

## 20. CHECKPOINTS, LEASES, FENCING, RECOVERY

Checkpoint: workflow_id, tenant_id, state_version, graph_version, node, typed refs,
checkpoint_hash, owner, timestamps; sensitive parts encrypted. Resume validates graph-version
compatibility, current permission, connector authorization, tenant scope.

Lease: operation ID, owner, monotonic fencing token, expiry, heartbeat; claim via guarded
`UPDATE … WHERE (expired OR free) RETURNING` / `FOR UPDATE SKIP LOCKED`; heartbeat and completion
compare the exact token; stale worker cannot write. Retries: transient reads only, bounded,
exponential backoff + jitter; never retry authz/validation errors. External writes: check local
ExternalOperation ledger and provider status first; at-least-once + idempotent effect +
reconciliation; ambiguous → OUTCOME_UNCERTAIN, stop auto-replay, human reconciliation (REC-003).

## 21. TRANSACTIONAL OUTBOX / EVENTS

Business change + audit + outbox in one transaction. Events: `case.created.v1, workflow.started.v1,
workflow.awaiting_review.v1, approval.decided.v1, workflow.completed.v1` plus **[v2]**
`case.blocked.v1, case.escalated.v1, external_operation.uncertain.v1`. Envelope: event_id,
event_type, schema_version, tenant_ref, resource_id, occurred_at, correlation_id, payload. Delivery
event_id ≠ source business key (DIR-022). Consumers authenticate producers, dedupe via InboxEvent,
tolerate duplicates/out-of-order, DLQ after configured failures. JSON Schemas in
`packages/contracts/events/`.

## 22. REST API [v2 — reconciled with SRS §12]

The SRS (formal series) and the Master disagree on paths. Canonical paths follow the SRS
(ADR-0005); Master variants are NOT duplicated as aliases. Final API surface for R1:

| Method & path | Notes / source |
|---|---|
| POST /api/v1/alerts | integration identity only; 201 new / 200 replay / 409 conflict |
| GET /api/v1/cases | filters status/owner/date/alert ref; page size default 50, max 100 (NQ §3) |
| GET /api/v1/cases/{id} | 404 concealment |
| PATCH /api/v1/cases/{id} | allowed fields + `If-Match`/record_version (FR-004) |
| POST /api/v1/cases/{id}/assignment | supervisor reassignment with reason (FR-009) |
| GET /api/v1/cases/{id}/evidence, /snapshots, /reports, /timeline | UI support |
| POST /api/v1/cases/{id}/evidence-conflicts/{cid}/resolution | FR-015 |
| POST /api/v1/cases/{id}/reports/{rid}/submit · /withdraw | FR-027 |
| POST /api/v1/workflows | 202 + status URL; Idempotency-Key |
| GET /api/v1/workflows/{id} | status, blockers, next action |
| POST /api/v1/workflows/{id}/cancel | FR-035; no rollback implied |
| POST /api/v1/approvals/{id}/decision | FR-028 |
| POST /api/v1/documents | upload initiation (SRS name; Master's `/documents/uploads` not used) |
| GET /api/v1/documents/{id}/download-link | short-lived scoped link |
| POST /api/v1/knowledge/search | SRS name (Master: `/knowledge/query`) |
| GET /api/v1/audit-events | SRS name (Master: `/audit/events`); auditor scope |
| GET /api/v1/agents | permitted registry view (blueprint) |
| POST /api/v1/evaluations · GET /api/v1/evaluations/{id} | eval runs |
| POST /api/v1/exports | FR-030 (Should); Draft watermark; audited |
| GET /api/v1/escalations | FR-011 |
| POST /api/v1/operations/{id}/reconcile | operator manual reconciliation (REC-003) |
| admin: /api/v1/admin/role-assignments, /delegations, /integrations, /feature-flags, /config-versions/{id}/promote, /legal-holds | FR-003/021/040, FR-039 |
| GET /health/live · /health/ready · /metrics (protected) | |

Headers: `Authorization: Bearer`, `Correlation-Id` (tracing only, validated format),
`Idempotency-Key` on mutating create/start/decision commands, `If-Match` for versioned updates.
Statuses: 200, 201, 202, 400, 401, 403, 404, 409, **413** **[v2]**, 422, 429 (+Retry-After), 503.
Never expose stack traces, provider payloads, secrets, internal tokens or another tenant's IDs.
Commit `docs/api/openapi.json` generated by `make openapi` and check drift in CI.

## 23. FRONTEND

Screens: 1 Sign-in/SSO, 2 Overview/KPI, 3 Case Queue, 4 Case Detail + Timeline, 5 Evidence
Explorer, 6 Agent Run Monitor, 7 Draft Report Editor, 8 Approval Center, 9 Policy Search,
10 Audit History, 11 Evaluation Dashboard, 12 Tenant Administration; plus **[v2]** 13 Document
Review (original + extracted fields + correction, SRS §11), 14 Execution/Recovery Monitor (failed
step, retries, budgets, uncertainty, permitted recovery), 15 Escalation queue.

Case detail: case ID, institution, status, priority, assignment, timestamps, source freshness,
classification, evidence, citations, findings, missing evidence, limitations, workflow status,
audit timeline. Findings visually separate FACT / DETERMINISTIC RESULT / AI INTERPRETATION /
EVIDENCE GAP with text labels, not colour alone. Show QUEUED, RUNNING, WAITING_REVIEW, FAILED,
OUTCOME_UNCERTAIN, BLOCKED, Draft vs Approved. No success state before server confirmation.
Citations open the exact cited revision. Not chatbot-centric. WCAG 2.2 AA target, Chrome + Edge
current/previous, 1280×720 minimum, usable at 200% zoom (NQ-040..043) **[v2]**. Timezone shown
explicitly; currencies never merged; English only (no localization claim).

## 24. AUDIT

Append-only, enforced in the database (trigger rejecting UPDATE/DELETE for application roles) for
login/security actions, case creation, assignment, retrieval metadata, workflow transitions, agent
metadata, tool execution, document changes, policy version use, report versions, approvals,
external status updates, admin/config changes, recovery/reconciliation, exports. Hash-chain per
tenant for tamper evidence **[v2]**. Actions that require audit evidence fail if the audit write
fails (same transaction — SPR-035). No secrets or unnecessary PII.

## 25. TRACE / CORRELATION

external_alert_id → case_id → workflow_id → step_id → tool_call_id → model_request_id; OTel span
propagation including across the outbox/queue; pseudonymous identifiers; protected payload
inspection separately authorized, time-limited, logged.

## 26. SECURITY IMPLEMENTATION

Test explicitly: cross-tenant access; prompt injection in documents and policy corpus;
exfiltration via tools; approval bypass; compromised connector; stale lease replay; PII in
logs/traces; model misuse; dependency vulnerability; unsafe URLs/SSRF; path traversal; arbitrary
tool invocation; secret leakage; decompression/parser bombs; CSRF on cookie sessions; forged
tenant headers; cost/loop exhaustion **[v2]**. Controls: deny by default, schema validation, ACL
filters, tenant scope, RBAC/ABAC, tool allowlists, egress restrictions, content scanning,
prompt/data separation, structured outputs, human approval, redaction, SAST, SCA, secret scanning,
SBOM, signed artifacts. No secrets in code, images, repo, logs, traces or browser bundle.

## 27. PRIVACY / DATA LIFECYCLE

Configuration points: classification, retention by record class, legal hold, deletion propagation
to chunks/indexes/caches/exports, backup restore re-applying deletion/hold before access
(DIR-035, SPR-025), regional deployment, provider retention, minimization, redaction, field
allowlist per alert family, privacy-rights request routing to authorized staff (SPR-020),
investigation-confidentiality routing (SPR-037) **[v2]**. Never hard-code that a regulation
applies. Maintain `docs/security/regulatory-applicability-register.md` (status, owner placeholders).
No retention period is assumed; unresolved retention blocks live enablement, not development.

## 28. NON-FUNCTIONAL ENGINEERING

Performance, availability, reliability, recovery, integrity, security, observability, usability,
maintainability, cost. All numbers are PROPOSED (NQ-001): keep them in
`config/nfr-targets.yaml` with status, owner and measurement boundary. Report P50/P95/P99,
failures, timeouts, warm/cold, dependency latency; never drop failures from latency stats.
Proposed pilot load profile (NQ §2): 50 concurrent users, 10 rps steady 30 min after 5 min warm-up,
70/15/10/5 read/status/edit/command mix, 2 tenants, 100k cases, 1M transactions, 10k docs, 5
concurrent AI runs at 1/min, 2 policy searches/s, 2× burst 10 min, 8 h soak.

## 29. AI / RAG EVALUATION [v2 — reconciled]

The Master says ≥100 scenarios; AER-005 §10 proposes 200 scenario families
(60 normal, 40 missing/stale/conflicting, 30 policy/no-answer, 30 security/adversarial,
20 recovery/replay, 20 document) split 100 dev / 50 validation / 50 locked release, stratified,
separated by family lineage. Target AER's 200; 100 is an interim milestone only. Never tune on the
locked set.

Labels: expected evidence refs, critical gaps, exact calculations, reference policy version/location,
allowed and prohibited tool calls, expected escalation/abstention, lifecycle result, source
restrictions. Metrics with numerator, denominator, failures and N/A (AER-032): critical gap recall,
field accuracy, Recall@k, nDCG, citation validity, claim support precision, material fact
coverage, abstention quality (incl. false abstention), tool correctness, task success, reviewer
first-pass rate, latency/cost incl. retries, control violations (attempted vs successful).
Zero generated claims ⇒ support precision N/A, not 100%. Repeat model-driven release cases 3×,
report worst per family (AER §13). LLM-as-judge may triage only (AER-034). Two-reviewer human
labelling is required for release sets — the repo can only scaffold this (OPEN AI-D04). Proposed
gates per AER §12 live in `evaluation/gates.yaml` as PROPOSED. Never claim a pass that did not run.

## 30. TESTING PYRAMID

Unit (schemas, validation, authorization, transitions, redaction, calculations, citation
validation); integration (PostgreSQL, Redis, search abstraction, model gateway, queue, blob);
contract (connectors, API schemas, events); E2E (alert → … → approved status update → audit);
security (isolation, approval bypass, privilege escalation, forged tenant, injection, malicious
document, unsafe tool, secret/PII logging); recovery/fault (crash, stale fencing token, lease expiry,
duplicate event, DB commit before queue publish, queue duplicate, approval after cancellation,
rescinded permission, retrieval outage, model timeout, graph-version mismatch, ambiguous external
effect); UI E2E (Playwright: analyst, reviewer, restricted access, citation inspection);
load (§28 profile as PROPOSED). Tag every test with requirement IDs via `@pytest.mark.req(...)`
(§62).

## 31. CRITICAL RELEASE TEST CONDITIONS

Release fails on any: unauthorized disclosure, unauthorized action, cross-tenant access, approval
bypass, duplicate protected external effect, required audit gap, unsupported material finding
accepted as final, critical exploitable vulnerability. Targets: 100% block of designed
cross-tenant attempts; 100% block of simulated unauthorized financial actions; zero duplicate
case-status writes in the replay/fault suite. Encode as `tests/**` marked `release_blocker` and a
CI job that fails on any failure in that marker.

## 32. LOCAL DEVELOPMENT

Clone and run without a bank or Azure. docker-compose: PostgreSQL, Redis, Azurite (optional),
api, worker, web, mock-bank service. Mocks for identity, alerts, customer/KYC, transactions,
policies, case system, notifications, model, search. Seed (deterministic, seeded RNG, labelled
SYNTHETIC): two tenants; analysts, reviewers, supervisor, tenant admin, policy admin, auditor,
platform operator, integration identities; alerts, customers, transactions, documents, policies.
Commands: `make setup dev migrate seed test test-unit test-integration test-e2e lint typecheck
security eval trace openapi down`.

## 33. AZURE INFRASTRUCTURE

Terraform modules: resource groups/environment boundaries, VNet/subnets, private endpoints + DNS,
ACR, AKS (workload identity), PostgreSQL Flexible Server (PITR), Redis, Storage, AI Search, Azure
OpenAI integration, Service Bus (+DLQ), Key Vault, App Insights/Log Analytics, managed identities,
App Gateway/WAF. Separate dev/staging/prod tfvars + remote state. Data stores not public by default.
No secrets in outputs. Region/service availability is a prerequisite check, not an assumption.
Without Azure credentials, only `terraform fmt -check`, `validate`, and `tflint`/`checkov` may run —
never claim an apply or plan against real subscriptions (§61).

## 34. CONTAINERS / KUBERNETES

Multi-stage, non-root, minimal runtime, pinned base images, healthchecks, no secrets. Helm for web,
api, worker: ConfigMaps for non-secrets, Key Vault CSI/workload identity, probes, requests/limits,
restricted pod security, NetworkPolicy (default deny + explicit egress), HPA for stateless only,
graceful shutdown, worker drain, durable resume.

## 35. CI/CD

PR → format/lint → typecheck → unit → schema/OpenAPI drift → secret scan → SAST → SCA → build →
SBOM → image scan → sign (cosign) → integration → publish → terraform plan (staging) → deploy
staging → migrate → smoke → agent regression → RAG eval → security suite → load (configured) →
manual approval gate → prod canary → watch → promote or rollback. Model/prompt/RAG/index configs
are versioned release artifacts with independent rollback.

## 36. OBSERVABILITY

Metrics: request duration/P95, 5xx, authz denials, cross-tenant denials, queue depth/age, worker
lag, retries, lease expiry, DLQ, model latency/throttling, tokens, cost, tool errors, search errors,
retrieval relevance, citation failures, escalation ratio, cases completed, review latency, audit
completeness, source freshness, reconciliation backlog **[v2]**. Dashboards as code. Alert rules:
cross-tenant deny spike, repeated failed approvals, queue/worker lag, lease expiry growth,
ingestion failure, P95 regression, provider outage, abnormal spend, audit gap, uncertain-operation
backlog **[v2]**. Each alert: owner placeholder, severity, runbook link. Monitoring failure never
disables authorization or approval checks (NFR-008).

## 37. RUNBOOKS

Model provider outage; Search/RAG outage; DB degradation; queue failure; stale worker; lease
conflict; DLQ growth; ambiguous external operation; policy index corruption; IdP failure; secret
rotation; rollback; backup restore; cross-tenant incident; prompt-injection incident; budget
exhaustion; access revocation; legal hold/deletion request **[v2]**.

## 38. BACKUP / DR

PITR, backup policy abstraction, restore procedure, blob retention, queue/DLQ handling, RPO/RTO
placeholders (PROPOSED RTO ≤4h / RPO ≤15m — NQ §7), restore verification scripts that re-apply
holds/deletions before reopening access. Never claim targets met without an exercised drill.

## 39. REPORTING

Throughput, preparation and review duration, evidence completeness, corrections, unsupported
claim rate, reviewer overrides, recovery, agent usage, model cost (estimated vs reconciled), cost
per completed case incl. failed attempts, retrieval metrics, availability/latency. Show sample
sizes and definitions. No ROI claims.

## 40. FUTURE MODULES

Phase 2: KYC onboarding assistance, financial document intelligence. Phase 3: risk analysis,
reconciliation/reporting. Feature-disabled, separate permissions/config, clearly marked. Risk Agent
never becomes a credit decision maker; reconciliation never posts ledger entries.

## 41. API IDEMPOTENCY

Persist tenant, operation, key, canonical request hash, resource/result reference, status code,
timestamps, expiry. Same key + same payload → original result; same key + different payload → 409.
Expiry must cover the replay horizon (OPEN DATA-D05); expiry must not permit duplicate business
effects — the business unique keys remain the last line of defence.

## 42. ERROR MODEL

`{"error": {"code": "CASE_INVALID_STATE", "message": "...", "correlation_id": "...",
"details": {}}}`; stable code register in `docs/api/errors.md`; field paths without values for
validation errors; no secrets/stack/provider internals.

## 43. CONFIGURATION

Typed config separating code, environment, tenant, agent, prompt, model, policy and tool versions.
Validate at startup; fail safely when mandatory configuration is missing; no unsafe silent defaults.

## 44. FEATURE FLAGS

DB-backed, audited, tenant-scoped flags for agent version, RAG version, case-status write,
connectors, prompts, future modules. Disabled ⇒ fail safe. Case-status write is OFF by default.

## 45. DOCUMENTATION

README, CONTRIBUTING, SECURITY; docs/architecture/{system-overview, hld, lld, data-flow,
trust-boundaries}.md; docs/adr/*; docs/api/{openapi.md, events.md, errors.md}; docs/data/
{data-dictionary, retention, source-register}.md; docs/security/{threat-model, access-control,
privacy, regulatory-applicability-register}.md; docs/agents/{agent-registry,
tool-permission-matrix}.md; docs/rag/rag-design.md; docs/testing/{test-strategy,
evaluation-rubric}.md; docs/operations/runbooks/*, slo-catalogue.md; docs/decisions/open-decisions.md;
docs/traceability/{requirements-to-code, requirements-to-tests}.md; docs/implementation-status.md;
**[v2]** docs/requirements/{requirement-inventory, provisional-use-cases, PROMPT_GAP_REVIEW}.md;
docs/operations/{uat-scripts, go-live-checklist, handover}.md.

## 46. REQUIREMENTS TRACEABILITY

Preserve source IDs, **namespaced per §55**. Statuses: DRAFT REQUIREMENT, IMPLEMENTATION PLANNED,
IMPLEMENTED, TEST NOT RUN, TEST PASSED, TEST FAILED, BLOCKED, APPROVED (APPROVED only with a
recorded owner decision — none exist yet). Traceability is generated (§62), not hand-typed.

## 47. DEFINITION OF DONE

Implemented, typed, lint clean, unit tested, integration tested as applicable, tenant isolation
tested, authorization tested, errors handled, telemetry, docs, traceability regenerated, CI green,
no unresolved critical finding, acceptance demonstrable.

## 48. CODING STANDARDS

Typed Python; DI where useful; domain logic independent of FastAPI; repository/service boundaries;
explicit transaction boundaries; Pydantic contracts; no god modules. TypeScript strict;
deliberate server/client components; API client abstraction (generated from OpenAPI); accessible
components with loading/error/empty states; no authorization solely in UI. Small modules, no fake
abstractions, no dead code, no blanket `except`, structured logs, deterministic financial
calculations.

## 49. DO NOT CHEAT

No empty placeholders declared complete; no "TODO later" on R1-mandatory items without a BLOCKED
entry; no auth bypass in production mode; no hard-coded tenants or approvals; no prompt-based
security; no direct agent DB access; no shell tools; no URLs from model output; no confidence as
verification; no fabricated citations, test results, compliance, infrastructure or sign-off. Mocks
only for EXTERNAL dependencies; internal R1 logic must actually work.

## 50. IMPLEMENTATION ORDER

P0 Baseline · P1 Foundation · P2 Security & tenancy · P3 Case domain · P4 Workflow foundation
(worker, queue, LangGraph, checkpoints, leases, fencing, recovery, fault tests) · P5 Connectors &
tool gateway · P6 Documents & evidence · P7 RAG · P8 Agents & model gateway · P9 Full AML graph ·
P10 Human approval & case-status write · P11 Frontend · P12 Evaluation · P13 Hardening ·
P14 Infrastructure · P15 CI/CD · P16 UAT/handover package. No agents before identity, tenancy, case
contracts and workflow persistence exist.

## 51. MILESTONE ALIGNMENT

Weeks 1–4 discovery/security; 5–8 FastAPI/SSO/tenant/cases; 9–13 checkpoint/tool gateway/agent
graph; 14–17 RAG/evaluation; 18–22 AML pilot/UI/approvals; 23–28 enterprise adapters/observability;
29–36 hardening/UAT/go-live. Estimates, not promises.

## 52. DEMONSTRABLE VERTICAL SLICES

1. Authorized synthetic alert → authenticated source → tenant mapping → dedup → case → assignment
   → audit → visible only to permitted tenant/user. **(Implemented in starter — verify, don't rebuild.)**
2. Workflow starts → worker crashes → checkpoint survives → new worker resumes → no duplicate event
   or external action.
3. Alert → evidence → authorized policy retrieval → agents → validated citations → report →
   reviewer → approve/rework → authorized status write → full audit trail.

## 53. WORKING PROCEDURE

Inspect repo → read requirements → confirm inventory → report contradictions → plan → proposed tree
→ implement. Don't wait for confirmation on derivable engineering choices. For unresolved
business/security/regulatory choices: conservative local default behind config, documented as
DERIVED or OPEN, never "institution-approved". After every phase: lint, typecheck, tests, fix,
regenerate traceability, update `docs/implementation-status.md`.

## 54. COMPLETION REPORT

At completion produce: repository tree; architecture summary; requirement coverage; test results;
security test results; AI/RAG evaluation results; known limitations; open decisions; local run
instructions; Azure deployment instructions; UAT instructions; production-readiness gaps. Do not
claim production readiness while any mandatory gate is open.

===================================================================================================
## ADDITIONS IN v2
===================================================================================================

## 55. SOURCE PRECEDENCE AND ID NAMESPACING [v2 — critical]

The blueprint, Master and BRD/SRS **reuse the same IDs for different requirements**
(e.g. Blueprint FR-005 = document ingestion, Master FR-005 = immutable evidence refs, SRS FR-005 =
run lifecycle; Master BR-001 = human review, BRD BR-001 = case lifecycle). Therefore:

- Unprefixed `BR-*`, `FR-*`, `UI-*`, `NFR-*` always mean the formal series (BRD-001 / SRS-002).
- Master IDs are written `MST:<ID>` (e.g. `MST:FR-008`, `MST:AG-002`, `MST:UI-003`).
- Blueprint IDs are written `BP:<ID>`.
- DIR/AER/SPR/NQ are unique and stay unprefixed.

Precedence when they conflict on **behaviour**: SPR-006 (controls) > AER-005 / DIR-004 / NFR-007 >
SRS-002 > BRD-001 > Master > Blueprint > this prompt's illustrative examples. The Master wins on
**implementation detail** where the formal series is silent. Every conflict found gets a row in
`docs/requirements/PROMPT_GAP_REVIEW.md` §B and, if resolved by us, an ADR.

## 56. RECONCILED CONFLICTS (summary — details in PROMPT_GAP_REVIEW.md)

C-01 case states (§5) · C-02 run states (§5) · C-03 REJECT decision (§19, DEC-11) · C-04 API paths
(§22) · C-05 alert dedup key (§4) · C-06 eval set size (§29) · C-07 ID collisions (§55) ·
C-08 Temporal optional vs forbidden (ADR-0004) · C-09 pgvector vs AI Search for local (ADR-0007) ·
C-10 "Compliance Officer" actor in diagram vs SRS actor list (DEC-10) · C-11 GraphQL/mobile/data
lake in diagram vs R1 scope (§63) · C-12 Master DDL case status CHECK lists only 4 states (superseded
by §5).

## 57. REQUIREMENTS v1 OMITTED — NOW MANDATORY

Each is DOCUMENTED in the cited source; implement and test it:

- Optimistic concurrency / stale-version 409 on case updates (FR-004, UI-002).
- Approval invalidation on evidence/report change; reopened cases keep history (FR-006, AER-016, DIR-018).
- Intake quarantine with safe reason, owner, resolution state; replay keeps failure history (DIR-010, DIR-025).
- Escalation & due-time queue, once-per-rule, notification retry not duplicating escalation (FR-011).
- Evidence conflict resolution with recorded reason, originals preserved (FR-015, AER-007).
- Analyst report editing, revision history, submit/withdraw, frozen review snapshot (FR-027).
- Transaction completeness proof: counts, cursor completion, as-of, exclusions (DIR-012).
- Calculation rules documented and versioned; DIR-013 fixture (AED 100.00 + 250.50 posted = 350.50;
  pending AED 40.00 excluded and disclosed; USD 20.00 separate) as a required unit test.
- Identity resolution without name merge; ambiguous match → audited resolution (DIR-011).
- Field allowlists per alert family / data minimization (DIR §4, SPR-019).
- Incremental sync watermarks, overlap windows, restartable idempotent backfills (DIR-030, DIR-039).
- Corrected source records → new evidence version, linked, invalidating affected approvals (DIR §5).
- MFA/step-up for reviewers & privileged ops (SPR-005); break-glass (SPR-008); access reviews (SPR-006).
- Short-lived scoped download links; bulk/export bounds (SPR-012).
- SSRF/path-traversal/injection resistance; parameterized queries (SPR-013); egress allowlists (SPR-016).
- Request size/rate bounds, upload magic-byte checks, parser resource limits (SPR-017).
- Privacy-rights and investigation-confidentiality routing (SPR-020, SPR-037).
- Export with Draft/Approved marking, approval reference, audit (FR-030, DIR export boundary).
- Per-run / per-tenant / monthly budgets; estimated vs reconciled cost (NQ-047/048, FR-038).
- Quality thresholds not configured ⇒ show "Not evaluated", never "pass" (DIR-024).
- Pagination default 50, max 100; long operations async with operation reference (NQ §3).
- Configuration promotion with approval and lineage-preserving rollback (FR-040, SPR-044).
- Retention, legal hold, deletion propagation, restore re-applying holds (FR-039, SPR-023..025).
- Hash-chained, DB-enforced append-only audit; audit failure fails the action (SPR-035/036).
- Supervisor escalation route in HITL (diagram).

## 58. DOCUMENT 3 GAP

Use `docs/requirements/provisional-use-cases.md` (PUC-01..PUC-19, derived from SRS T-01..T-11, BRD
AC-01..AC-08 and Master UC-01..UC-08). Label every PUC "PROVISIONAL — not Document 3". E2E and UAT
tests reference PUC IDs. When the real Document 3 arrives, remap and record in an ADR.

## 59. STARTER REPOSITORY HANDOFF

This repository already contains Phase 0 artifacts and a Phase 1–3 foundation (see
`docs/implementation-status.md`, which lists exactly what was executed and passed). First action:
run `make setup && make migrate && make test` and confirm the recorded results reproduce on your
machine before building on them. Do not regenerate or overwrite Phase 0 artifacts; extend them.

## 60. SESSION PROTOCOL FOR CLAUDE CODE / CURSOR

This scope exceeds one session. Work phase by phase:

1. Start each session by reading `CLAUDE.md`, `docs/implementation-status.md` and the open
   decisions file. Continue from the first phase not marked DONE.
2. Keep a phase small enough to finish with green lint/type/tests in one session. Split large
   phases (e.g. P8 → agent registry, model gateway, each agent).
3. Commit after each green sub-step with a message referencing requirement IDs.
4. End every session by updating `docs/implementation-status.md` ("Session log" section: what was
   done, commands run, results, what is next, blockers).
5. Stop and report (do not improvise) if: a mandatory control cannot be implemented as specified; a
   test reveals a release-blocker you cannot fix; a decision is OPEN and no conservative default
   exists; or credentials/services you need are unavailable.
6. Never mark a requirement TEST PASSED from memory — only from the generated junit evidence (§62).

## 61. ENVIRONMENT HONESTY

Record per environment what actually ran. Without Azure: no Terraform apply/plan against
subscriptions, no Entra login, no Azure OpenAI or AI Search calls — use mocks and say so. Without
Docker: run with local PostgreSQL/Redis and say so. Load tests run on a laptop are smoke tests, not
NFR evidence.

## 62. EVIDENCE-BASED TRACEABILITY

Tests declare requirements with `@pytest.mark.req("FR-008", "DIR-026")`; `conftest.py` records them
into the junit XML. `make trace` runs the suite with `--junitxml` and
`scripts/gen_traceability.py`, which merges `docs/requirements/requirements.json`,
`docs/traceability/trace-map.yaml` (requirement → implementation files) and the junit results into
`docs/traceability/requirements-to-code.md`, `requirements-to-tests.md` and the coverage table in
`docs/implementation-status.md`. A requirement is TEST PASSED only if ≥1 linked test passed and
none failed in the latest run.

## 63. ARCHITECTURE-DIAGRAM ITEMS OUTSIDE R1

GraphQL (REST only in R1), Mobile channel (out), Data Lake / Spark / Data Factory / Power BI
(out; R1 exposes a reporting export via INT-10 only), Teams notifications (adapter interface only;
email/Teams channel OPEN), SIEM (structured security events exportable; SIEM integration OPEN),
Risk Analysis Agent (registered, disabled). Do not build these; document them as future.

## 64. PROPOSED DEFAULTS (config, status PROPOSED, owner OPEN)

| Setting | Default | Source |
|---|---|---|
| schema repair attempts | 1 | AER §8 |
| max agent steps per run | 12 | MST:AG-002 |
| retry on authz/validation errors | never | AER §8, DIR-028 |
| upload types / max size | PDF, DOCX, CSV / 20 MiB | DIR-014 |
| page size default / max | 50 / 100 | NQ §3 |
| API read P95 / P99 | 500 ms / 1500 ms | NQ-005 |
| command ack P95 | 1 s | NQ-006 |
| policy search P95 | 2 s | NQ-007 |
| AI draft P95 | 5 min | NQ-010 |
| queue wait P95 | 60 s | NQ-011 |
| availability | 99.9 % monthly | NQ-015 |
| RTO / RPO | 4 h / 15 min | NQ §7 |
| approval.reject_effect | REWORK | DEC-11 |
| case_status_write flag | OFF | DATA-D06 |
| self-approval | denied | FR-003 |

## 65. FIRST ACTIONS THIS SESSION

1. Read sources and the files listed in §59; run the starter verification.
2. Review `docs/requirements/PROMPT_GAP_REVIEW.md` and `docs/decisions/open-decisions.md`; append any
   new conflicts you find.
3. Present the plan for the next unfinished phase (likely P4 Workflow foundation), then implement.

START NOW.
