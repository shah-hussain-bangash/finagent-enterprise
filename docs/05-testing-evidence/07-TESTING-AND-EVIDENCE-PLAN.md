# Verification and evidence plan

**Status: planned work.** This document defines how to collect evidence after the architecture and engineering baseline; it does not claim the checks have all passed. Attach evidence to an exact commit, environment, configuration and execution date. Synthetic results do not prove live-provider behavior or regulatory approval.

## Evidence record

For every check, retain: requirement or risk addressed; owner and reviewer; commit and branch; environment and dependency versions; test data classification and source; command or procedure; actual result and number passed/failed/skipped; logs or artifact location; limitations; defects and disposition; and date. Protect logs and artifacts according to their data classification. Never publish credentials or real customer information in repository evidence.

| Area | Evidence to collect | Acceptance owner to appoint |
|---|---|---|
| UI and accessibility | Desktop, tablet and mobile journeys; keyboard and assistive-technology checks; contrast; loading/error/empty/session-expired cases | Product and accessibility leads |
| Analyst and reviewer workflow | Role-based case-to-decision journeys, rework and separation of duties | AML product and review leads |
| Evidence and documents | Source scope, freshness, conflicts, quarantine, extraction, provenance and snapshot cases | Data and document owners |
| AI and retrieval | Independent labeled datasets, grounding, citations, bilingual limitations, injection and abstention behavior | Model risk and domain leads |
| Security and privacy | Threat model, isolation, identity, authorization, secret scanning, independent security testing and privacy review | Security and privacy leads |
| Operations | Metrics, alerts, incident exercises, recovery, backup and restore in the target environment | Operations and continuity leads |
| Performance | Workload assumptions, representative volumes, response times, capacity and failure behavior | Engineering and operations leads |
| Integrations | Contract tests and authorized live-provider checks per source and environment | Integration and source owners |
| Release | Deployment, migration, rollback, change record, approvals and residual risk | Release owner and accountable sponsor |

## Acceptance sequence

1. Agree on scope, owners, environments, synthetic datasets, risk-based acceptance criteria and defect severity thresholds.
2. Complete the UI checklist in the [work tracker](../production-ui-work-tracker.md), including the full analyst journey and case context across authentication.
3. Run unit, integration, security, browser, accessibility and performance checks appropriate to the selected release. Record skips and environmental blockers separately.
4. Exercise staging integrations, deployment, monitoring and recovery under approved controls; use independent security and domain evaluation where required.
5. Resolve or formally accept defects and residual risks through the institution's process. Named owners sign off on evidence relevant to their roles.
6. Approve release only for the precise build, configuration, data scope and environment supported by the evidence.

## Current known gaps

The UI is not fully accepted across roles, devices, screen readers or realistic production volumes. A candidate return-to-case flow has local and mocked identity-provider tests; browser and live-provider verification remain open, and it does not preserve context when the API rejects an existing session. Live providers, target-environment resilience, institutional policies, independent AI evaluation, security assessment and production deployment are not established. The GitHub pull request and its checks can change over time; inspect the latest run rather than relying on an old status in this document.

## Existing evidence and traceability

Start with [implementation status](../implementation-status.md), [coverage summary](../traceability/coverage-summary.md), [requirements to tests](../traceability/requirements-to-tests.md), [acceptance journey](../testing/acceptance-journey.md) and the relevant GitHub Actions run. The traceability files are generated; do not edit them by hand. Historical checkpoint claims apply only to the recorded checkpoint and test conditions.
