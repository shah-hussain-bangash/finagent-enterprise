# Business capability and operating model

**Status:** working architecture, 19 November 2026. Roles, control ownership and service targets require approval by an actual institution. The project uses synthetic data; it is not a bank operating model or regulatory determination.

## Business outcome

Enable an authorized analyst to investigate an alert using traceable source information, prepare a reviewable report and hand it to an independent reviewer. The workflow should make missing evidence, conflicting information, stale records and uncertainty visible. Improvements in productivity, accuracy or cost have not been measured against an institutional baseline.

## Capability map

| Capability | Core activities | Primary repository reference | Maturity and decision gap |
|---|---|---|---|
| Alert and case management | Ingest, assign, track, block, escalate and reopen | `src/finagent/services/case_service/` | Synthetic workflows exist; institutional assignment policy and live intake are open |
| Evidence and transaction review | Collect, scope, compare and inspect source records | `src/finagent/services/evidence_service/` | Source contracts, freshness policy and real volumes require approval |
| Document intelligence | Upload, scan, parse, quarantine, correct and inspect provenance | `src/finagent/documents/`; `src/finagent/services/document_service/` | Live scanning, OCR/provider selection and production isolation remain unverified |
| Investigation assistance | Run controlled analysis and present supported findings | `src/finagent/agents/`; `src/finagent/orchestration/` | The experimental assistant is disabled; independent model evaluation remains open |
| Report and independent review | Edit versions, validate, submit and review | `src/finagent/services/review_service/` | Reviewer matrix, decision policy and pilot acceptance remain open |
| Policy management | Manage applicable policy content and retrieval | `src/finagent/services/knowledge_service/` | Real policy ownership, applicability and provider verification are open |
| Oversight and operations | Monitor workflows, exceptions, audit and recovery | `src/finagent/worker/`; `docs/operations/` | On-call ownership, recovery exercises and service targets are not approved |
| Security and governance | Identity, authorization, evaluation and controlled release | `src/finagent/security/`; `src/finagent/evaluation/` | Independent assessment, legal/privacy decisions and production controls remain open |

## Value stream and control points

| Stage | Responsible role to confirm | Information and decision | Failure or hold point |
|---|---|---|---|
| Alert intake and case assignment | AML operations / supervisor | Establish source, subject scope, alert and authorized ownership | Invalid, duplicate or unauthorized intake requires investigation |
| Evidence collection | Assigned analyst | Review source, version, time window, freshness and completeness | Missing, stale, inaccessible or conflicting sources must remain visible |
| Document and analysis review | Assigned analyst | Inspect document state, extracted fields, recorded calculations and supported claims | Quarantine, unverified fields and unsupported outputs cannot silently become reliable facts |
| Report preparation | Assigned analyst | Prepare and validate a version bound to investigation context | Blocking problems prevent the appropriate submission path |
| Independent review | Authorized reviewer | Assess the exact report version and associated evidence | Separation of duties, authorization, stale context and step-up requirements apply |
| Finalization or rework | Authorized workflow and designated human roles | Record decision and authorized downstream action when configured | Disabled writes, external uncertainty or changed evidence must be handled explicitly |
| Monitoring and learning | Operations, risk and governance | Review incidents, audit, evaluation and configuration | No production improvement claim without measured baseline and ownership |

## Proposed decision rights

| Decision | Accountable owner to appoint | Supporting roles | Current status |
|---|---|---|---|
| Product scope and pilot | Business sponsor | AML lead, engineering, compliance | Open |
| Case and reviewer permissions | Security and AML governance | Privacy, identity, audit | Local defaults only; institutional matrix open |
| Source contracts and data use | Data and source-system owners | Integration, legal, privacy | Synthetic sources; live approvals open |
| Model and evaluation release | Model risk / AI governance | AML, security, engineering | Evaluation criteria and independent review open |
| Service targets and recovery | Operations owner | Business continuity, engineering | Targets proposed in repository; not institutionally approved |
| Production release | Accountable release authority | Security, product, operations, data owners | No production approval recorded |

This is an illustrative responsibility map, not an approved RACI. Named persons, deputies, escalation authority and evidence must be added by the adopting organization. The [open decision register](../decisions/open-decisions.md) is the existing source for detailed decisions.

## Measures to define before an enterprise pilot

Possible measures include time from assignment to review, percentage of cases with documented source provenance, rate of unavailable or stale evidence, review rework, accessibility defects, and recovery time. Each requires a named owner, definition, baseline, collection method, target and review date. No numerical improvement, salary outcome, regulatory compliance, or cost saving should be inferred from the architecture alone.

## Related documentation

[Enterprise architecture](../02-system-architecture/03-ENTERPRISE-SOLUTION-ARCHITECTURE.md) · [Integration and interface design](../02-system-architecture/04-INTEGRATION-AND-INTERFACE-DESIGN.md) · [Source register](../data/source-register.md) · [Role and access controls](../security/access-control.md) · [Acceptance journey](../testing/acceptance-journey.md) · [Evidence plan](../05-testing-evidence/07-TESTING-AND-EVIDENCE-PLAN.md)
