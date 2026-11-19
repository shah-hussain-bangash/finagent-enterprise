# FinAgent engineering handbook

**Status: working baseline, 2026-11-19.** Read alongside the [enterprise architecture](../02-system-architecture/03-ENTERPRISE-SOLUTION-ARCHITECTURE.md), [implementation status](../implementation-status.md), [open decisions](../decisions/open-decisions.md) and the repository's applicable instructions. The handbook describes the current development process, not approval to process real financial data.

## Development workflow

1. Open the intended repository folder and verify its branch and uncommitted changes before editing. The integration branch and `main` can differ. Avoid copying credentials, local backups, databases, build output or test-only dependency directories into Git.
2. Read the relevant requirement, decision and existing implementation. Record assumptions and keep local/demo behavior distinct from approved enterprise behavior.
3. Make the smallest cohesive change that completes the intended behavior. Preserve tenant and case scope, audit context, evidence provenance, role boundaries and failure behavior.
4. Run the checks relevant to the change. Review failures and skipped tests; never describe a failed, skipped or unexecuted check as a pass.
5. Review the changed files and their diff, then commit with an attributable identity. Commit and push intentionally; saving in Cursor does not automatically publish code to GitHub.
6. Push the intended branch and update its pull request. Review automated results, document limitations, and obtain required approvals before merging or deployment.

## Repository map

| Location | Engineering ownership |
|---|---|
| `apps/web` | Next.js UI, server-side web authentication and rendering tests |
| `src/finagent/api`, `services`, `domain` | API contracts, application services and business rules |
| `src/finagent/agents`, `orchestration`, `worker` | Model boundaries, investigation execution and recovery |
| `src/finagent/integrations`, `documents` | Tool/source access and document handling |
| `src/finagent/security`, `database` | Identity, authorization, tenancy, schema and audit persistence |
| `tests` | Unit, integration, security, browser, recovery and mutation tests |
| `docs`, `infra` | Requirements, decisions, runbooks and reference deployment templates |

## Local environment and data

Use the project setup and diagnostic commands in the [README](../../README.md). Prerequisites and working ports must be confirmed for the machine. Treat `.env` files, session keys, tokens and provider credentials as secrets. Use only authorized synthetic data in development and demonstrations. Test fixtures may reset their configured database: inspect the test configuration and target before running them, and never point destructive fixtures at a live or shared database.

The connected workspace currently combines read-only recorded views with existing workflow actions. Some workspace screens are illustrative and the assistant candidate is disabled. A real deployment requires approved providers, source-system contracts and operational controls.

## Engineering contracts

- **Access:** enforce permission, tenant and case scope on the server, not merely through hidden UI controls. Preserve independent review and step-up requirements.
- **Evidence:** retain source, timestamps, versions and limitations. Differentiate current evidence from recorded snapshots; do not present missing or unavailable data as an assurance of no risk.
- **Documents:** show scan/quarantine state and authorize each download; extraction and corrections do not establish authenticity.
- **AI:** keep source content untrusted, constrain tool and model access, validate outputs, disclose limitations, and do not enable experimental capabilities without an approved integration and evaluation.
- **State changes:** preserve version checks, idempotency, audit and recovery semantics. Describe ambiguous external outcomes explicitly.
- **UI:** retain selected-case context where supported; provide accessible names, keyboard operation and understandable loading, empty, error and access-denied states. Mark unconnected and sample content.
- **Data:** do not mix currencies or imply filtered totals are account balances; use the source and window associated with the recorded value.

## Verification and review

The README lists quality, web, browser and mutation commands. CI in `.github/workflows/ci.yml` is the executable pipeline definition, not proof that a given run passed. The relevant pull request or run must be tied to the exact commit and its result inspected. Earlier local test results do not automatically apply to later code. See the [evidence plan](../05-testing-evidence/07-TESTING-AND-EVIDENCE-PLAN.md) for the records required later.

Reviewers should examine: scope and behavior; authorization and tenant isolation; input and output contracts; error/empty/session states; changes to data or external effects; accessibility and responsive behavior; tests and their environment; migrations and rollback; dependency and security implications; and documentation accuracy. Do not merge or release based solely on screenshots, green build output, or synthetic test results.

## API and schema changes

The API specification is generated by `scripts/export_openapi.py`; event schemas have their own export script. Regenerate and review contract changes using the project's intended dependency environment, then run the drift checks. Database changes require reviewed migrations, application/migrator privilege separation, compatibility planning and a tested recovery approach. Do not rewrite immutable history as a shortcut.

## Incident and operational changes

Use the existing runbooks under `docs/operations/` for recovery, uncertain external outcomes, model outages and backup/restore. Before a production rollout, an institution must validate these procedures in its own environment, assign on-call ownership and approve recovery and communication targets.
