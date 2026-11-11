# Open Decisions Register (Category C)

Consolidated from all source documents plus decisions surfaced during implementation. **None are
resolved.** Where development needs a value, a conservative local default is used behind
configuration (see "Local default"), which is NOT an institutional decision.

Status values: OPEN · DEFAULTED-LOCAL (conservative dev default in place) · RESOLVED (needs named owner + date + evidence).

## A. From source documents

| ID | Decision | Owner (role) | Gate | Local default | Status |
|---|---|---|---|---|---|
| DEC-01 | Sponsor, Product Owner, named signatories | Sponsor | G1 | — | OPEN |
| DEC-02 | Pilot institution, alert family, users, language, exclusions | AML lead | G1 | 1 synthetic alert family `TM_STRUCTURING`, English | DEFAULTED-LOCAL |
| DEC-03 | Process baseline, sample size, rubric, KPIs | Product Owner | G1 | — | OPEN |
| DEC-04 | Source systems, fields, permissions, quality limits | Data owners | G3 | synthetic mocks | DEFAULTED-LOCAL |
| DEC-05 | Jurisdiction, obligations, residency, retention, holds | Compliance | G3 | no retention auto-deletion | DEFAULTED-LOCAL |
| DEC-06 | Reviewer matrix, delegation, approval validity, SLAs | AML/Compliance | G3 | self-approval denied; approval valid until content changes | DEFAULTED-LOCAL |
| DEC-07 | Budget, per-case cap, cloud/model permission | Sponsor | G2 | mock model, zero cost | DEFAULTED-LOCAL |
| DEC-08 | Availability, support hours, RTO/RPO, incident ownership | Operations | G3 | PROPOSED values in config | DEFAULTED-LOCAL |
| DEC-09 | Narrowed MVP vs wider P0 blueprint scope | Sponsor | G1 | narrowed AML MVP (BRD proposal) | DEFAULTED-LOCAL |
| D-01..D-09 | SRS §17 decisions (stakeholders, reviewer matrix, connectors/limits, jurisdiction, eval thresholds, NFR targets, budgets, revocation/accessibility, status-write semantics) | various | pilot | mirrors DEC-* above | OPEN |
| DATA-D01 | Real source systems, endpoints, owners, access approvals | Data owners | connector build | mocks | DEFAULTED-LOCAL |
| DATA-D02 | Alert family, subject scope, required fields, window semantics (inclusive/exclusive) | AML | mapping | `[start, end)` half-open window | DEFAULTED-LOCAL |
| DATA-D03 | Customer identity mapping, minimization, masking | Data/Security | live data | allowlist of synthetic fields | DEFAULTED-LOCAL |
| DATA-D04 | Freshness thresholds, completeness exceptions, extraction limits, encrypted files | AML/QA | pilot | encrypted files quarantined; thresholds "Not evaluated" | DEFAULTED-LOCAL |
| DATA-D05 | Source rates, pagination, volumes, replay horizon, sync cadence | Integration | pilot | idempotency retention 30 days | DEFAULTED-LOCAL |
| DATA-D06 | Case-status write target, expected-version rules, reconciliation | Case-system owner | write enablement | feature flag OFF; mock target | DEFAULTED-LOCAL |
| DATA-D07 | Policy applicability, retention, holds, location, provider handling | Compliance/Security | live data | — | OPEN |
| DATA-D08 | Revocation propagation delay, quotas, retries, recovery ownership | Ops/Security | pilot | permission checked per request (0 s cache) | DEFAULTED-LOCAL |
| DATA-D09 | Canonical wire schemas, mapping registry, change notice | Engineering/source owners | build | schema_version 1.0 | DEFAULTED-LOCAL |
| AI-D01 | Agent/domain/control owners; permitted MVP alert family | PO/AML | pilot | — | OPEN |
| AI-D02 | Approved checklist, indicator interpretation, report template | AML/Compliance | pilot | synthetic checklist v0 marked SYNTHETIC | DEFAULTED-LOCAL |
| AI-D03 | Model deployment, provider handling, versions, permitted context | Security/AI lead | pilot | MockModelProvider only | DEFAULTED-LOCAL |
| AI-D04 | Dataset counts/split, labels, rubric, confidence, thresholds; two-reviewer labelling | QA/Model governance | pilot | AER §10 plan; synthetic single-author labels flagged UNREVIEWED | DEFAULTED-LOCAL |
| AI-D05 | Task latency, token/step/tool/cost limits, fallback | Ops/Sponsor | pilot | 12 steps, 1 repair | DEFAULTED-LOCAL |
| AI-D06 | Eval trace retention, production sampling, incident/retest | Compliance/Ops | pilot | — | OPEN |
| SEC-D01 | Institution, licence, regulator/laws, controller/processor, legal register | Compliance/Legal | live | all PENDING | OPEN |
| SEC-D02 | Role/reviewer matrix, MFA, session and revocation targets | Security/AML | pilot | MFA required for reviewers when claim available; 8 h session | DEFAULTED-LOCAL |
| SEC-D03 | Access-review intervals, break-glass scope, support roles | Security/Ops | pilot | break-glass disabled | DEFAULTED-LOCAL |
| SEC-D04 | Classification, lawful basis, privacy assessment, notices, rights exceptions | Privacy/Legal | live | — | OPEN |
| SEC-D05 | Record-class retention, holds, deletion/backup | Compliance/Data | live | no auto-delete | DEFAULTED-LOCAL |
| SEC-D06 | Cloud/model regions, remote support, subprocessors, transfers | Legal/Security | live | — | OPEN |
| SEC-D07 | Cryptography, keys, network exposure, egress, parser limits | Security/Eng | pilot | TLS 1.2+, 20 MiB | DEFAULTED-LOCAL |
| SEC-D08 | Incident thresholds, notification matrix/deadlines | Compliance/IR | live | — | OPEN |
| SEC-D09 | RPO/RTO, support hours, continuity | Ops/Business | live | PROPOSED 4h/15m | DEFAULTED-LOCAL |
| SEC-D10 | Independent testing, defect deadlines, residual risk | Security/Sponsor | live | — | OPEN |
| NFR-D01..D09 | Workload, thresholds, SLO, DR, quotas, 24x7, accessibility, budgets, revocation | various | pilot/prod | `config/nfr-targets.yaml` PROPOSED | DEFAULTED-LOCAL |

## B. Surfaced during v2 review / implementation

| ID | Decision | Owner | Local default | Status |
|---|---|---|---|---|
| DEC-10 | Map diagram actor "Compliance Officer" to Reviewer, Policy Administrator, or a new role | Product Owner/Compliance | not a separate role | DEFAULTED-LOCAL |
| DEC-11 | Effect of REJECT decision: Rework vs close with reason | AML/Compliance | `approval.reject_effect=REWORK` | DEFAULTED-LOCAL |
| DEC-12 | Formal Document 3 (Use Cases & Acceptance Criteria) | Product Owner | provisional PUC-* | OPEN |
| DEC-13 | Assignment rule for new cases (queue only vs auto-assign analyst) | AML Ops | least-loaded active analyst in tenant, else queue only | DEFAULTED-LOCAL |
| DEC-14 | Analyst visibility: assigned cases only vs all tenant cases in their queue | AML/Security | assigned only; supervisor sees tenant | DEFAULTED-LOCAL |
| DEC-15 | Whether Auditor may read case content or only audit metadata | Compliance | audit metadata only | DEFAULTED-LOCAL |
| DEC-16 | Escalation due times and owners per case status (part of DEC-06 SLAs) | AML Ops | RECEIVED 4 h, ASSIGNED 24 h, BLOCKED 48 h, AWAITING_REVIEW 72 h → AML Supervisor; FINALIZING 1 h → Platform Operator (`escalation_rules`, version `1-PROPOSED`) | DEFAULTED-LOCAL |
| DEC-17 | Request quotas per principal and per tenant (part of NFR-D05) | Ops/Security | principal 600/min burst 60; tenant 3000/min burst 300 | DEFAULTED-LOCAL |
| DEC-18 | Who may grant delegations, maximum window, delegable roles (part of DEC-06/SEC-D02) | AML/Security | Tenant Admin only (no self-service), ≤ 30 days, ANALYST and REVIEWER only | DEFAULTED-LOCAL |
| DEC-19 | Break-glass enablement, eligible people, scope, duration, reviewer (SEC-D03) | Security/Ops | disabled; when enabled: read-only tenant case access, ≤ 60 min, MFA, reviewed by another Tenant Admin | DEFAULTED-LOCAL |
| DEC-20 | MFA step-up permission set and accepted `amr`/`acr` values (SEC-D02) | Security | decide, assign, tenant admin, policy publish, export, reconcile, break-glass; `amr` contains mfa/otp/hwk/fido/rsa/swk | DEFAULTED-LOCAL |
| DEC-21 | Document parser limits (pages, rows, decompressed size/ratio, timeout, memory) (SEC-D07, DATA-D04) | Security/Eng | 200 pages, 50k rows, 100 MiB / ratio 100, 60 s, 768 MiB | DEFAULTED-LOCAL |
| DEC-22 | KYC extraction field profile and required fields per document type (AI-D02) | AML/KYC | `kyc-fields-v1-SYNTHETIC` (passport: name, number, DOB, expiry; PoA: name, address, issue date) | DEFAULTED-LOCAL |
| DEC-23 | Required evidence per alert family and gap severities (FR-015) | AML | `evidence-req-v1-SYNTHETIC`: profile, transactions, KYC list, sanctions = BLOCKING if missing/incomplete; documents/staleness = REVIEW | DEFAULTED-LOCAL |
| DEC-24 | Per-policy role ACL and applicability for synced policies (DATA-D07) | Compliance | all case-working roles; AML department -> TM_STRUCTURING | DEFAULTED-LOCAL |
| DEC-25 | KYC checklist items and thresholds (AI-D02) | AML/KYC | `kyc-checklist-0.1-SYNTHETIC`: PoA <= 90 days, KYC review <= 365 days | DEFAULTED-LOCAL |
| DEC-26 | Structuring indicator threshold, band and indicator rules (AI-D02) | AML | 10,000.00 AED, 10% band, >= 3 deposits on >= 3 days; turnover ratio > 1.5; debit >= threshold | DEFAULTED-LOCAL |
| DEC-27 | Model budgets: per-capability calls/tokens/cost, per-run tokens/cost, tenant monthly cost (DEC-07, AI-D05) | Sponsor/Ops | 4 calls, 24k tokens, 1.00; run 150k tokens / 10.00; tenant 500.00 per month; mock price 0 | DEFAULTED-LOCAL |
| DEC-28 | Evaluation thresholds and dataset governance (AI-D04) | Model governance | `eval-thresholds-0.1-PROPOSED` = 1.0 on control metrics; one SYNTHETIC dataset, single-author labels | DEFAULTED-LOCAL |
| DEC-29 | Whether AML Supervisors may download documents / who may export | AML/Security | supervisors: no document download; exports: reviewer + supervisor (MFA) | DEFAULTED-LOCAL |
| DEC-30 | Downstream case-status values and reason codes for the single write (DATA-D06) | Case-system owner | `UNDER_REVIEW_COMPLETE` / `REVIEWED_AND_APPROVED`; write OFF by default | DEFAULTED-LOCAL |
| DEC-31 | Retention periods, deletion execution and backup treatment (DEC-05, SEC-D05) | Compliance | rules `NOT_SET`; dry-run only; deletion not executed in R1 | DEFAULTED-LOCAL |
| DEC-32 | Download-link lifetime and binding (SPR-012) | Security | 300 s, bound to principal + exact version, re-authorized at download | DEFAULTED-LOCAL |
| DEC-33 | Enterprise UI sign-in (Entra ID OIDC for the web BFF) | Security/IT | local SYNTHETIC issuer only; Entra web sign-in NOT BUILT | OPEN |

| DEC-34 | Institutional machine-identity provider and credential lifecycle for alert producers | Security/Integration | Local/test synthetic opaque credentials only; 900 s default, 3600 s maximum, owner provisioning/revocation, intake-only route binding; production adapter disabled | OPEN |

| DEC-35 | Institution-approved second factors, enrollment identity proofing, recovery and key custody | Security/IT | Candidate local/test authenticator verification only; five-minute session-bound grants; institutional MFA acceptance remains pending | OPEN |

| DEC-36 | Email possession verification, mail delivery/recovery policy and phone passkey enrollment | Security/IT | Gmail candidate verifies existing email only and never grants MFA; private mobile HTTPS and passkeys remain unimplemented | OPEN |

## Local phone passkey candidate clarification - 2026-11-09

DEC-35 and DEC-36 remain OPEN. Private mobile HTTPS and reviewer email/password access were demonstrated by the user. ADR 0035 proposes owner-allowlisted password-plus-recent-email enrollment and session-bound WebAuthn step-up for local/test only. Candidate code is isolated; database/Redis integration and actual passkey enrollment are not yet verified. Recovery/replacement, hardware assurance, institutional policy and production acceptance remain outstanding.

## Local phone passkey candidate clarification - 2026-11-09

DEC-35 and DEC-36 remain OPEN. Private mobile HTTPS and reviewer email/password access were demonstrated by the user. ADR 0035 proposes owner-allowlisted password-plus-recent-email enrollment and session-bound WebAuthn step-up for local/test only. Candidate code is isolated; database/Redis integration and actual passkey enrollment are not yet verified. Recovery/replacement, hardware assurance, institutional policy and production acceptance remain outstanding.
