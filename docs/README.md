# FinAgent Enterprise — Documentation

Read the project [README](../README.md) first, then follow the numbered folders below. The numbered collection is a portfolio reading path; existing requirements, ADRs, runbooks and generated evidence remain in their established locations and are linked where relevant.

For the current documented baseline, open the [status and release evidence record](05-testing-evidence/13-CURRENT-STATUS-AND-RELEASE-EVIDENCE.md). Historical checkpoints are retained as history.

## Choose a reading path

| Reader | Suggested sequence | What to look for |
|---|---|---|
| Recruiter or hiring manager | [Project overview](../README.md) → [case study](07-case-study/08-CASE-STUDY.md) → [verified investigation](05-testing-evidence/14-SYNTHETIC-ACCEPTANCE-RECORD.md) | The AML problem, engineering contribution, demonstrated workflow and its limits |
| Architect or engineer | [Enterprise architecture](02-system-architecture/03-ENTERPRISE-SOLUTION-ARCHITECTURE.md) → [architecture-to-code map](02-system-architecture/06-ARCHITECTURE-TO-CODE-MAP.md) → [architecture decisions](adr/) | Trust boundaries, design trade-offs and source-to-test traceability |
| Reviewer assessing readiness | [Current status and release evidence](05-testing-evidence/13-CURRENT-STATUS-AND-RELEASE-EVIDENCE.md) → [testing and evidence plan](05-testing-evidence/07-TESTING-AND-EVIDENCE-PLAN.md) → [open decisions](decisions/open-decisions.md) | Exact tested revisions, unverified controls and outstanding institutional decisions |

The investigation and CI evidence use synthetic data. The cloud architecture and provider adapters are reference designs until verified in their intended environment; they should not be presented as a deployed bank system.

## Numbered documentation

| Order | Folder | Contents |
|---|---|---|
| 01 | [Business requirements](01-business-requirements/) | Scope, objectives, stakeholders, business capabilities and acceptance |
| 02 | [System architecture](02-system-architecture/) | Enterprise architecture, interfaces, decisions and links to code |
| 03 | [AI and agent governance](03-ai-agent-governance/) | Model gateway, retrieval, evaluation and responsible-use references |
| 04 | [Security and governance](04-security-governance/) | Identity, access, tenant isolation, policy and risk references |
| 05 | [Testing and evidence](05-testing-evidence/) | Verification plan, recorded CI results, gaps and acceptance |
| 06 | [Engineering and DevOps](06-engineering-devops/) | Engineering handbook, code navigation and documentation roadmap |
| 07 | [Case study](07-case-study/) | Architecture narrative, trade-offs, evidence and interview priorities |

For a document-by-document sequence see the [architecture and engineering index](architecture/README.md). The material is an engineering portfolio using synthetic data, not an approved production deployment. CI results apply only to the recorded commits and environments.
