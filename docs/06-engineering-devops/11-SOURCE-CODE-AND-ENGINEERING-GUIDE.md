# 11 — Source Code and Engineering Guide

**Purpose:** a practical reading order for reviewers and engineers. This map points to the repository as it exists; code location is not independent verification of production behavior. Read the project [README](../../README.md) and [solution architecture](../02-system-architecture/03-ENTERPRISE-SOLUTION-ARCHITECTURE.md) first.

## 1. Start at the user interface

| Order | Location | What to inspect |
|---|---|---|
| 1.1 | [`apps/web/app`](../../apps/web/app/) | Portal routes, case screens, reports, review and workspace pages |
| 1.2 | [`apps/web/components`](../../apps/web/components/) | Shared controls, navigation, status and operational components |
| 1.3 | [`apps/web/lib`](../../apps/web/lib/) | Server-side API, authentication/session helpers and view-specific utilities |
| 1.4 | [`apps/web/tests`](../../apps/web/tests/) | Focused frontend rendering and authentication checks |

The workspace includes connected read-only pages and explicitly illustrative experiences. Server-side authorization governs connected data; a preview role selector is not an access-control mechanism.

## 2. Follow a request into the backend

| Order | Location | What to inspect |
|---|---|---|
| 2.1 | [`src/finagent/api`](../../src/finagent/api/) | FastAPI application, dependencies, request handling and routers |
| 2.2 | [`src/finagent/security`](../../src/finagent/security/) | Identity, authorization, tenant/case scope, rate limiting and redaction |
| 2.3 | [`src/finagent/services`](../../src/finagent/services/) | Case, evidence, documents, review, operations and other application services |
| 2.4 | [`src/finagent/domain`](../../src/finagent/domain/) | Domain states, money and deterministic AML calculations |
| 2.5 | [`src/finagent/database`](../../src/finagent/database/) | Models, database sessions, grants and migrations |

Follow one request end to end: the page or client request, the API route and dependencies, the service's authorization and state rules, then persistence and tests. UI controls do not replace backend enforcement.

## 3. Follow evidence, AI and workflows

| Order | Location | What to inspect |
|---|---|---|
| 3.1 | [`src/finagent/integrations`](../../src/finagent/integrations/) | Source adapters, typed projections, tool gateway and resilience |
| 3.2 | [`src/finagent/documents`](../../src/finagent/documents/) | Upload validation, scanner interface, storage, isolation and extraction |
| 3.3 | [`src/finagent/agents`](../../src/finagent/agents/) | Capability registry, model gateway, validation, retrieval and disabled assistant candidate |
| 3.4 | [`src/finagent/orchestration`](../../src/finagent/orchestration/) and [`worker`](../../src/finagent/worker/) | Workflow graphs, checkpoints, workers, leases and event delivery |
| 3.5 | [`src/finagent/evaluation`](../../src/finagent/evaluation/) | Evaluation logic and synthetic assessment helpers |

Trace the source, scope and time of evidence through a recorded snapshot before discussing a finding. Model outputs are drafts; deterministic checks reduce risk but do not establish truth or completeness.

## 4. Inspect delivery and verification

| Order | Location | What to inspect |
|---|---|---|
| 4.1 | [`tests`](../../tests/) | Unit, integration, security, browser, recovery and mutation scenarios |
| 4.2 | [`tests/mutations.yaml`](../../tests/mutations.yaml) | Specific intentional control changes and tests expected to catch them |
| 4.3 | [`.github/workflows/ci.yml`](../../.github/workflows/ci.yml) | Actual CI jobs and their dependencies |
| 4.4 | [`infra`](../../infra/) | Reference Helm and Terraform, not validated production deployment |
| 4.5 | [`docs/operations`](../operations/) | Deployment guidance and operational runbooks requiring target-environment exercises |

## 5. Example technical review

1. Open the [case study](../07-case-study/08-PORTFOLIO-CASE-STUDY.md) and select the tenant-isolation, AI-boundary or review-separation example.
2. Inspect its source file and linked mutation definition. Identify the expected behavior and the change the test deliberately introduces.
3. Inspect the corresponding [recorded GitHub run](https://github.com/zenithdevelopers7-source/finagent-enterprise/actions/runs/44469329182) and its exact commit. A deliberately failing test can be the expected successful outcome of a mutation check.
4. Compare with the [evidence register](../05-testing-evidence/12-TESTING-AND-EVIDENCE-REGISTER.md) and identify what has not been verified.
5. Use a disposable synthetic environment for any further execution; check the database target before running fixtures that reset schemas.
