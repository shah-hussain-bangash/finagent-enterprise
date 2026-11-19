# FinAgent: focused portfolio priorities

**Purpose:** make the project easy for a hiring manager to understand and technically credible for senior AI/solution architecture interviews. This is a prioritized working plan, not a guarantee of employment or salary. Actual experience, interview performance, employer requirements and the job market matter alongside the portfolio.

## The five priority artifacts

| Priority | Artifact | What the reader should learn | Current source and next action |
|---|---|---|---|
| 1 | **GitHub README** | Business problem, working scope, architecture, decisions, tested evidence and honest limitations in a short first read | [README](../../README.md): drafted; review and publish the latest version |
| 2 | **Architecture and engineering case study** | Context and component diagram, boundaries, data and integration flows, alternatives, risks and trade-offs | [Enterprise architecture](../02-system-architecture/03-ENTERPRISE-SOLUTION-ARCHITECTURE.md) and [portfolio brief](08-PORTFOLIO-CASE-STUDY.md): consolidate and refine for a 10-minute technical review |
| 3 | **Working synthetic demonstration** | A real analyst/reviewer journey with evidence provenance and visible failure/permission states | [Demo walkthrough](../workspace-demo-walkthrough.md): validate against the current build and add accurate screenshots or a recording when available |
| 4 | **Verification and evidence register** | Which controls were tested, with commands, exact commit, outcomes and remaining gaps | [Evidence plan](../05-testing-evidence/07-TESTING-AND-EVIDENCE-PLAN.md), [code map](../02-system-architecture/06-ARCHITECTURE-TO-CODE-MAP.md), and CI run links: collect a small number of reproducible examples before expanding |
| 5 | **Targeted résumé and interview narrative** | The candidate's actual contribution, architecture decisions, implementation depth and lessons learned | Prepare from verified work and the [portfolio brief](08-PORTFOLIO-CASE-STUDY.md); tailor to a real job description and the candidate's experience |

Existing detailed requirements, ADRs, security notes, operations runbooks and the [documentation roadmap](../06-engineering-devops/10-DOCUMENTATION-ROADMAP.md) remain supporting references. They should be extended when a concrete implementation, risk, reviewer or release requires them—not created simply to increase document count.

## Suggested sequence

1. Refine the README and architecture case study for clarity and accuracy; identify which branch contains the current work.
2. Reproduce one synthetic end-to-end journey and record the exact build, steps and visible limitations.
3. Select three representative engineering decisions—for example case authorization, evidence snapshots and model output validation—and trace each from requirement to code, a test and the latest relevant result.
4. Add a compact evidence table, screenshots and a concise demonstration only after verifying they match the code and environment.
5. Tailor a résumé and interview walkthrough to the candidate's verified responsibilities and the specific employer's requirements.

## Target-role competency map

| Competency | What to demonstrate with FinAgent | Evidence to prepare |
|---|---|---|
| AI / solution architecture | Business problem, component boundaries, integration design and trade-offs | Architecture diagram, decision references, code walkthrough |
| Agentic AI and governance | Controlled tools, human review, evidence grounding and limits | Gateway/validator code, negative tests, evaluation limitations |
| Enterprise engineering | API and data contracts, security, reliable workflow and delivery | Selected ADRs, CI results, source-to-test links, failure scenarios |
| Technical leadership | Scope choices, delivery sequencing, risks and stakeholder decisions | Explicit ownership, review record and examples of actual contribution |

FinAgent uses synthetic data. Do not claim a bank deployment, live model quality, regulatory certification, personal job title, years of experience or measured business improvement without independent supporting facts. Production readiness requires the work listed in the [evidence plan](../05-testing-evidence/07-TESTING-AND-EVIDENCE-PLAN.md).
