# \# Operations dashboard and connector observations

# 

# Status: \*\*Automated verification passed; development database migrated to 0008; Docker application images rebuilt; local operator dashboard, 1/7/28-day views and expanded workflow step records visually checked.\*\*

# 

# Live connector verification and production acceptance remain pending. FR-037/038 and AER-035 remain subject to their documented scope and acceptance requirements.

# 

# \## Entry points and authorization

# 

# The `/operations` web page includes an operational dashboard alongside the existing uncertain-operation reconciliation form. Dashboard data comes from `GET /api/v1/reporting/dashboard`.

# 

# The `days` query accepts 1 through 28. The web page offers 1, 7 and 28 days, defaulting to 7. Invalid query values are rejected with HTTP 400 and `SCHEMA\_VALIDATION\_FAILED`, following the application's validation-error contract.

# 

# GET requests read recorded data and never initiate a connector probe, model call or reconciliation. Existing `/reporting/overview` and `/metrics` contracts are unchanged.

# 

# `PLATFORM\_OPERATE` sees aggregate metadata for its own tenant, including unlinked model requests and tool/external-operation records. It receives no case-detail links unless it separately has `CASE\_READ` and the individual case passes the existing row-scope predicate.

# 

# Other `CASE\_READ` users see only their existing assigned/review-assigned or supervisory case scope. Connector observations require `PLATFORM\_OPERATE` and are omitted for ordinary case readers.

# 

# Reconciliation continues to require its existing API permission and MFA checks. No permissions or budget rules are relaxed.

# 

# \## Recorded connector observations

# 

# Migration `0008` adds `connector\_observations` and an immutable-row trigger. Application grants allow SELECT/INSERT while denying UPDATE/DELETE/TRUNCATE, matching the project's immutable ledgers.

# 

# The factory attaches a callback bound to the execution tenant and a fixed source category. Observations persist across API/worker restarts; in-memory circuit objects are not presented as shared circuit state.

# 

# One observation is recorded for a logical JSON request or byte download. Fields are tenant, fixed source, operation category, success/failure, classified error, attempt count, duration and time.

# 

# No URL, path, query, headers, payload, credential, customer ID or document ID is recorded.

# 

# Attempts count executions of the resilience callback, not proof that an HTTP packet was sent. A call stopped by an already-open circuit has zero attempts. Retries are `max(attempts - 1, 0)`. Duration includes retries and backoff for the logical call.

# 

# The HTTP layer measures transport, HTTP errors, JSON parsing and envelope/schema-version/as-of checks. Business-object projection after that layer may still fail. Rejected local path parameters or missing credentials before client admission are not observations of remote availability.

# 

# Observation persistence is best effort: failure emits a fixed redacted warning and must never repeat or alter a business effect. Critical action auditing remains in the existing gateways. No historical observations are fabricated or backfilled.

# 

# Only `core-banking` is instrumented here. Model usage continues to come from `ModelRequest`.

# 

# States describe the latest recorded call inside the selected window:

# 

# | State | Meaning |

# | --- | --- |

# | `NO\_OBSERVATIONS` | No eligible observations; future-dated samples are excluded |

# | `STALE` | Latest observation is older than five minutes |

# | `LAST\_CALL\_SUCCEEDED` | Latest eligible call succeeded within five minutes |

# | `LAST\_CALL\_FAILED` | Latest eligible call failed within five minutes |

# 

# The five-minute boundary is a display rule, not an approved SLA or availability guarantee. Counts include all eligible observations in the selected window. No automatic health probe is run.

# 

# `NO\_OBSERVATIONS` does not establish connector health or failure. Opening or refreshing the dashboard does not generate an observation.

# 

# \## Metric definitions

# 

# | Metric | Definition and limits |

# | --- | --- |

# | Backlog | Current visible case/workflow status counts, all ages |

# | Created volume | Visible cases created during the event window |

# | First completion | Earliest recorded COMPLETED history per case minus case creation; cohort uses first-completion time |

# | Review turnaround | Decision time minus request creation for decisions in the window; includes waiting |

# | Model latency | Recorded per-attempt duration; budget/policy denials and incident-admission denials excluded |

# | Model usage/cost | Request/token ledger totals; configured cost estimates separated by currency |

# | Model slices | Case alert type, gateway `config\_version` and currency; UNLINKED includes evaluations without a case |

# | Recovery/requeue | Run-history transitions to RETRYING/QUEUED, excluding initial creation; not proof of successful recovery |

# | Output rejection rate | Outputs containing at least one rejected finding divided by all scoped output records |

# | Output gap rate | Outputs containing a GAP finding divided by all scoped output records; repeated gaps can occur across capabilities |

# | Output escalation rate | ESCALATED output records divided by all scoped output records |

# | Analyst-edit origin | Report versions with ANALYST\_EDIT origin, including reopening copies; not independently labelled corrections |

# | Source freshness | Current evidence using stored freshness and `as\_of` with the configured source-age limit at report time |

# | External operations | Latest states for operations updated in the window; not an immutable transition count |

# 

# Zero denominators and empty duration cohorts are N/A. Nonnegative zero measurements remain zero. Negative duration samples are omitted and counted.

# 

# Currency amounts are never merged. Recorded `config\_version` is the gateway configuration label, not a full deployment fingerprint. Model requests include denial/repair/retry rows and must not be equated with successful or billable calls.

# 

# Active human handling time, monetary tool fees, independently labelled reviewer corrections and policy review/applicability freshness are not recorded sufficiently for these reports. They remain explicitly N/A. Existing gateway budget enforcement is retained; this reporting change does not implement new budget policies.

# 

# Recent workflow detail is bounded to 20 runs and 200 step records across those runs, with explicit truncation indicators. Run cost covers that run's recorded lifetime.

# 

# Model slices are bounded to the 100 largest groups with an indicator; aggregate totals still include all groups.

# 

# Event aggregates use the selected UTC interval. Backlog/current evidence and recent-run detail deliberately include older records. Changing the date window therefore does not remove older records from those current-state sections.

# 

# The API uses a read-only, repeatable-read transaction for a consistent report snapshot. It does not reconstruct historical state at an arbitrary past timestamp.

# 

# \## Verification completed

# 

# Verification recorded on 2026-10-21:

# 

# \- Ruff lint and formatting checks passed.

# \- Mypy passed for 154 source files.

# \- All 143 unit tests passed, including 15 operations-reporting unit cases.

# \- The full backend suite passed: 331 tests passed and one browser test was deselected.

# \- All 10 operations-dashboard PostgreSQL integration tests passed.

# \- The existing connector contract tests passed within the backend suite.

# \- Web TypeScript checking and the production build passed.

# \- The extended browser journey passed separately: one test passed in 20.84 seconds.

# \- OpenAPI and traceability were regenerated with the latest test evidence.

# \- The development database was upgraded to `0008 (head)`.

# \- Docker API, worker and web images were rebuilt and their containers recreated.

# \- API and web containers reported healthy; local service diagnostics passed.

# 

# The 143 unit tests are included in the full backend suite and are not additional to its 331-test total.

# 

# Added regression coverage checks unknown/stale observations, retry behavior, telemetry-write failure, safe labels, tenant and case scopes, first-completion/review arithmetic, quality denominator semantics, currency/config isolation, factory callback binding and immutability. Browser coverage checks analyst/operator visibility.

# 

# \### Local visual checks

# 

# The operator dashboard was checked while signed in as `a.operator1@synthetic`:

# 

# \- The operational dashboard and observed connector-status section rendered.

# \- The 1-day, 7-day and 28-day views loaded.

# \- The 1-day view displayed N/A for empty metric cohorts.

# \- The 7-day and 28-day views displayed recorded activity.

# \- Current backlog remained visible across date windows.

# \- Workflow step sections containing 32 and 5 records expanded and displayed step names, states, attempts and elapsed times.

# \- Unavailable measurements and the existing uncertain-operation section remained visible.

# \- Connector status displayed `NO\_OBSERVATIONS`.

# 

# These visual checks confirm local rendering. They do not establish live connector availability, independent metric accuracy or production acceptance.

# 

# \## Running the updated application

# 

# The development database must include migration `0008` before the new dashboard is used.

# 

# For the Docker application stack, rebuild and recreate the application containers after source changes:

# 

# &#x20;   docker compose --profile app up -d --build --no-deps api worker web

# 

# Check container and service status:

# 

# &#x20;   docker compose ps

# &#x20;   powershell -ExecutionPolicy Bypass -File .\\scripts\\windows\\diagnose.ps1

# 

# Open:

# 

# &#x20;   http://127.0.0.1:3001/operations?days=7

# 

# Use the local synthetic operator identity to view connector observations. Local synthetic sign-in is for development only.

# 

# The Windows host-process launcher and Docker application containers use the same default API/web ports. Use the existing running mode consistently; the host launcher refuses to start when those ports are occupied.

# 

# The installer itself performs no migration, connector request or business action.

# 

# \## Remaining scope

# 

# The following remain separate work:

# 

# \- Live connector validation and observation capture in the intended environment.

# \- Production load and operational performance testing.

# \- Billing reconciliation against provider invoices or authoritative usage records.

# \- Active human handling-time measurement.

# \- Monetary tool-fee recording.

# \- Independently labelled reviewer-correction measurement.

# \- Complete quality and policy freshness/applicability coverage.

# \- Independently reviewed validation and locked release datasets.

# \- Approved operational targets, thresholds and production acceptance.

# 

# Traceability currently reports 122 TEST PASSED requirements, 60 TEST PASSED (partial scope) requirements and 118 DRAFT REQUIREMENT entries, totalling 300. All source requirements remain DRAFT; none is APPROVED.

# 

# Do not mark all of FR-037/038 or AER-035 complete from dashboard rendering or passing regression tests alone.

