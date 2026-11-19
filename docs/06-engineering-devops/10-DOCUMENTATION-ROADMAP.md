# Enterprise engineering documentation roadmap

**Status:** working plan, 19 November 2026. Documents are complete as drafts only when their scope, source links, open decisions and review criteria are recorded. They do not become approved enterprise standards or evidence of production readiness by being written. Existing generated traceability files remain generated and must not be manually edited.

## Phases and deliverables

| Phase | Documentation outcome | Current working material | Completion requires |
|---|---|---|---|
| 1. Business scope and value | Problem, objectives, boundaries, stakeholders, value stream, use cases and draft acceptance criteria | [Business scope and acceptance](../01-business-requirements/01-BUSINESS-SCOPE-AND-ACCEPTANCE.md); [operating model](../01-business-requirements/02-BUSINESS-CAPABILITIES-AND-OPERATING-MODEL.md) | Source review, assigned owners and scope/criteria approval |
| 2. Enterprise and solution architecture | Capability map, context and container views, trust boundaries, deployment options and architecture principles | [Enterprise architecture](../02-system-architecture/03-ENTERPRISE-SOLUTION-ARCHITECTURE.md); existing [ADRs](../adr/) | Architecture review and target-environment decisions |
| 3. Interfaces and integration | API/event catalog, source contracts, data ownership, errors, retries, idempotency and reconciliation | [Interface design](../02-system-architecture/04-INTEGRATION-AND-INTERFACE-DESIGN.md); [source register](../data/source-register.md); [events](../api/events.md) | Source-owner contracts, compatibility and integration acceptance |
| 4. Data and evidence | Logical entities, data flows, classification, provenance, lifecycle, retention and deletion | Evidence and snapshot [ADR](../adr/0017-documents-evidence-and-snapshots.md); source register | Data/privacy ownership and lifecycle decisions |
| 5. Security and privacy | Threat model, identity, role/case/tenant boundaries, secrets, privacy and control mapping | [Access control](../security/access-control.md); [tool matrix](../agents/tool-permission-matrix.md) | Independent security/privacy review and approved controls |
| 6. AI and model governance | Model and retrieval flow, tool constraints, validation, evaluation, change and release | [Model gateway ADR](../adr/0019-model-gateway-and-deterministic-validation.md); [evaluation datasets](../testing/evaluation-datasets.md) | Independent domain and model-risk review |
| 7. UI and accessibility | Role journeys, navigation, case context, states, responsive design and accessibility criteria | [UI work tracker](../production-ui-work-tracker.md); [workspace preview](../workspace-interface-preview.md) | Device, keyboard, screen-reader and usability acceptance |
| 8. Quality and evidence | Test strategy, traceability, environment, datasets, defect rules and evidence register | [Verification plan](../05-testing-evidence/07-TESTING-AND-EVIDENCE-PLAN.md); [generated traceability](../traceability/coverage-summary.md) | Reproducible results for exact release and independent review where required |
| 9. Operations and resilience | Monitoring, incident response, backup, restore, recovery, ownership and service objectives | [Operations runbooks](../operations/); [deployment guide](../operations/deployment.md) | Target-environment exercises and approved ownership/targets |
| 10. Delivery and release | Engineering workflow, branch/CI, deployment, migrations, rollout and rollback | [Engineering handbook](05-ENGINEERING-HANDBOOK.md); `.github/workflows/ci.yml` | Validated staging path, change controls and accountable sign-off |
| 11. Portfolio and handover | README, architecture case study, demo, limitations, evidence and interview walkthrough | [README](../../README.md); [portfolio brief](../07-case-study/08-PORTFOLIO-CASE-STUDY.md); [code map](../02-system-architecture/06-ARCHITECTURE-TO-CODE-MAP.md) | Current, reproducible and accurately attributed artifacts |

## Status rules

- **Draft:** content authored but not accepted by accountable reviewers.
- **Reviewed:** named reviewer, date, scope and findings recorded; review does not imply production approval.
- **Approved:** named decision-maker accepts a specific version and scope with evidence.
- **Verified:** a named procedure was executed for a specific commit and environment with recorded outcome and limitations.
- **Blocked / not run:** record the reason and owner; never count as passed.

The phases can inform each other. Writing a phase does not prevent documenting dependencies discovered later, and no ordering removes the need for testing. The user's requested sequence is documentation first, followed by testing and evidence updates.
