# FinAgent Enterprise — Architecture and Engineering Portfolio

**Portfolio scope:** human-reviewed AML investigation on synthetic data  
**Document status:** working drafts, 19 November 2026  
**Implementation:** selected workflows and controls are implemented and tested in development; production approval, live integrations, full accessibility, and institutional acceptance remain open.

This collection presents FinAgent in a clear sequence from business need to architecture, engineering, verification and portfolio presentation. Documents describe a mixture of current source behavior, engineering decisions, and proposed enterprise requirements. Each document identifies its scope; a document or successful synthetic test is not evidence of an approved production system.

## Recommended reading order

| No. | Document | What it explains |
|---|---|---|
| 01 | [Business scope and acceptance](../01-business-requirements/01-BUSINESS-SCOPE-AND-ACCEPTANCE.md) | Problem, objectives, scope, stakeholders and draft acceptance scenarios |
| 02 | [Business capabilities and operating model](../01-business-requirements/02-BUSINESS-CAPABILITIES-AND-OPERATING-MODEL.md) | Investigation value stream, responsibilities and decisions |
| 03 | [Enterprise solution architecture](../02-system-architecture/03-ENTERPRISE-SOLUTION-ARCHITECTURE.md) | System context, application components, data, security boundaries and deployment |
| 04 | [Integration and interface design](../02-system-architecture/04-INTEGRATION-AND-INTERFACE-DESIGN.md) | APIs, sources, events, retries, error handling and external effects |
| 05 | [Engineering handbook](../06-engineering-devops/05-ENGINEERING-HANDBOOK.md) | Repository structure, engineering workflow, controls and change review |
| 06 | [Architecture-to-code map](../02-system-architecture/06-ARCHITECTURE-TO-CODE-MAP.md) | Source locations, test entry points and remaining assurance gaps |
| 07 | [Testing and evidence plan](../05-testing-evidence/07-TESTING-AND-EVIDENCE-PLAN.md) | Required verification, evidence records, owners and acceptance sequence |
| 08 | [Portfolio case study](../07-case-study/08-PORTFOLIO-CASE-STUDY.md) | Technical narrative, trade-offs, selected test evidence and interview walkthrough |
| 09 | [Portfolio priorities](../07-case-study/09-PORTFOLIO-PRIORITIES.md) | The five artifacts to prioritize for hiring conversations |
| 10 | [Documentation roadmap](../06-engineering-devops/10-DOCUMENTATION-ROADMAP.md) | Further phases, review criteria and institutional decisions |
| 11 | [Source code and engineering guide](../06-engineering-devops/11-SOURCE-CODE-AND-ENGINEERING-GUIDE.md) | A reading path through the working UI, backend, AI, integration and delivery code |
| 12 | [Testing and evidence register](../05-testing-evidence/12-TESTING-AND-EVIDENCE-REGISTER.md) | Actual CI evidence, exclusions and outstanding verification |

## Quick paths

- **Hiring manager:** start with the [project README](../../README.md), then the [portfolio case study](../07-case-study/08-PORTFOLIO-CASE-STUDY.md).
- **Architect or engineer:** read the [solution architecture](../02-system-architecture/03-ENTERPRISE-SOLUTION-ARCHITECTURE.md), [interface design](../02-system-architecture/04-INTEGRATION-AND-INTERFACE-DESIGN.md), [source guide](../06-engineering-devops/11-SOURCE-CODE-AND-ENGINEERING-GUIDE.md), and relevant [architecture decisions](../adr/).
- **Reviewer:** read the [evidence register](../05-testing-evidence/12-TESTING-AND-EVIDENCE-REGISTER.md), [testing plan](../05-testing-evidence/07-TESTING-AND-EVIDENCE-PLAN.md), and [open decision register](../decisions/open-decisions.md); verify results against the actual commit and environment.

## Status and evidence

GitHub Actions [run #7](https://github.com/zenithdevelopers7-source/finagent-enterprise/actions/runs/44469329182) passed its five jobs for commit `7ccb3c6`. That result does not validate later edits or demonstrate production readiness. Live source-provider verification, independent security and AI assessment, complete UI accessibility testing, realistic-volume testing, recovery exercises, and named institutional approvals are still required.

The project-level [README](../../README.md) links to the latest known code and documents; the default `main` branch may differ from the integration branch. Do not disclose secrets or real customer data when preparing demonstrations or evidence.
