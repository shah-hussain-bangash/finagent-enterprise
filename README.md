# FinAgent Enterprise

**Enterprise Financial Agentic AI Platform. Release 1: Human-Reviewed AML Investigation Copilot.**

> **Status: functional end to end on SYNTHETIC data, verified locally.** Alert intake, case
> management, authorized evidence gathering, isolated document processing, immutable evidence
> snapshots, approved-policy retrieval with citations, specialist capabilities behind a model gateway
> with deterministic validation, versioned reports, independent review, a guarded finalization write,
> exports, a functional web UI, evaluation gates, configuration promotion and legal holds are
> implemented and tested (including a 20-step automated acceptance journey and a real-browser journey).
> External integrations run against local adapters (mock bank, mock model, local retrieval, local
> blob store, local test scanner); their live counterparts (Entra ID, Azure OpenAI, Azure AI Search,
> Azure Blob, ClamAV, Service Bus) are implemented as adapters but **LIVE NOT VERIFIED**.
> Nothing here is regulator-approved, penetration-tested, deployed or UAT-accepted. Exact status:
> [`docs/implementation-status.md`](docs/implementation-status.md).

## Start here

| If you want to... | Read |
|---|---|
| Continue building with Claude Code / Cursor | [`CLAUDE.md`](CLAUDE.md), then [`docs/prompts/MASTER_BUILD_PROMPT_v2.md`](docs/prompts/MASTER_BUILD_PROMPT_v2.md) |
| See what is built, verified, blocked and next | [`docs/implementation-status.md`](docs/implementation-status.md) |
| Requirement-level coverage (generated from test evidence) | [`docs/traceability/coverage-summary.md`](docs/traceability/coverage-summary.md) |
| The acceptance journey and how to run it | [`docs/testing/acceptance-journey.md`](docs/testing/acceptance-journey.md) |
| Open business, control or legal questions | [`docs/decisions/open-decisions.md`](docs/decisions/open-decisions.md) |
| Operations runbooks | [`docs/operations/`](docs/operations/) |
| Architecture decisions | [`docs/adr/`](docs/adr/) |

## Run locally on Windows (PowerShell)

Requirements: Python 3.12, Node 22, Docker Desktop. Ports default to 8001 (API), 3001 (web), 8100
(mock bank), 55432 (PostgreSQL) and 56379 (Redis) so they do not collide with other projects.

```powershell
powershell -ExecutionPolicy Bypass -File scripts\windows\setup.ps1      # venv, deps, .env (generated local secrets), DB, migrate, seed
powershell -ExecutionPolicy Bypass -File scripts\windows\start-dev.ps1  # API + worker + web in three windows
powershell -ExecutionPolicy Bypass -File scripts\windows\diagnose.ps1   # read-only health check (no secrets printed)
```

Open `http://127.0.0.1:3001/signin` and pick a SYNTHETIC identity (local issuer only), e.g.
`a.analyst1@synthetic`, `a.reviewer1@synthetic`, `a.policyadmin1@synthetic`, `a.operator1@synthetic`.
Policy administrators must synchronise and approve policies (Policy search -> Policy administration)
before compliance analysis can cite them. Create an alert with
`python scripts\dev_token.py` + `POST /api/v1/alerts` (see `scripts/demo_slice1.sh`) or run the tests.

**Containers instead of host processes:** `docker compose --profile app up -d --build`
(migrate -> api :8001, worker, web :3001, mock bank :8100).

## Quality gates (same commands on any OS)

```powershell
python scripts\tasks.py lint typecheck security      # ruff, mypy --strict, bandit, pip-audit
python scripts\tasks.py test                         # full suite incl. E2E journey + regenerated traceability
python scripts\tasks.py test-release-blockers        # prompt §31 subset - must be 100% green
python scripts\tasks.py mutation-check               # break each security control, a test must catch it
python scripts\tasks.py web-check                    # tsc + next build
python scripts\tasks.py test-browser                 # real-browser journey (needs `playwright install chromium`)
```

GNU make users can run the same targets with `make <target>`.

## Architecture in one paragraph

One FastAPI API and one worker process share the `finagent` package (ADR-0002). PostgreSQL holds all
state with composite tenant keys; every business change, audit event (hash-chained, append-only) and
outbox event commits in one transaction. LangGraph owns the investigation workflow (`aml-1`) with
encrypted, tenant-scoped checkpoints, fenced leases and an external-effect ledger. Capabilities never
touch the database, network or tools: deterministic orchestration gathers evidence through the tool
gateway, calls the model only through the model gateway, and deterministic validators decide what is
accepted. Reviewers decide; the platform only drafts. The Next.js UI is a backend-for-frontend: tokens
never reach the browser.

## Repository layout

| Path | Contents |
|---|---|
| `src/finagent/api` | FastAPI app and routers (cases, evidence, knowledge, review, workflows, governance, ops, admin) |
| `src/finagent/agents` | Capability registry, contracts, model gateway, providers, deterministic validation, runner |
| `src/finagent/documents` | Format checks, scanner port, isolated parser, typed extraction, encrypted blob storage |
| `src/finagent/services` | case, evidence, document, knowledge, review (reports/approvals/finalization/exports), governance, workflow, audit, admin |
| `src/finagent/orchestration` | LangGraph graphs (`aml-1`, `durability_probe`), runtime, checkpointer |
| `src/finagent/domain` | State machines, money, AML calculations (pure) |
| `src/finagent/evaluation` | Versioned SYNTHETIC datasets, PROPOSED thresholds, evaluation runner |
| `apps/web` | Next.js UI (15 screens), `apps/api`, `apps/worker`, `apps/mock-bank` (SYNTHETIC) |
| `tests/` | unit, contract, integration, security, recovery, e2e, browser |
| `infra/` | Helm chart and Terraform (NOT VALIDATED / NOT APPLIED) |
| `docs/` | Requirements, ADRs, decisions, traceability, operations runbooks, testing evidence |
