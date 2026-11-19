# Architecture-to-code and evidence map

**Status: source navigation and verification plan, 2026-11-19.** Entries identify where to start reviewing a control; a file name or test name is not by itself proof that the control is complete or effective. Verify the exact commit, test result, environment and coverage before making an assurance claim.

| Architecture concern | Source starting point | Verification starting point | Remaining assurance |
|---|---|---|---|
| Role and tenant authorization | `src/finagent/security/authorization/policy.py`; `src/finagent/api/deps.py` | `tests/mutations.yaml` entry `tenant-filter`; `tests/security/` | Full role matrix and independent review for the target institution |
| Case-scoped evidence search | `src/finagent/services/evidence_service/search.py`; `src/finagent/api/routers/evidence.py` | `tests/security/test_case_evidence_search_scope.py`; `tests/unit/test_case_evidence_search.py` | Live-source contracts and scale testing |
| Evidence-bound claims and authority limits | `src/finagent/agents/validation.py` | `tests/mutations.yaml` entries `validator-deterministic-value` and `validator-boundary`; `tests/integration/test_agents.py` | Independent evaluation and human review of model behavior |
| Bounded assistant retrieval | `src/finagent/agents/assistant_retrieval.py`; `assistant_grounding.py`; `assistant_candidate.py` | `tests/unit/test_assistant_retrieval.py`; `tests/integration/test_assistant_candidate.py` | Candidate remains disabled; bilingual accuracy and security acceptance not established |
| Document quarantine and evidence | `src/finagent/documents/`; `src/finagent/services/document_service/` | `tests/integration/test_documents_evidence.py`; `tests/unit/test_documents.py` | Live scanner, isolation and end-to-end operations in the target environment |
| Independent review | `src/finagent/services/review_service/`; `src/finagent/api/routers/` | `tests/mutations.yaml` entry `review-separation-of-duties` | Approved reviewer matrix, role-based acceptance and operational policy |
| Ambiguous external effects | `src/finagent/orchestration/`; `src/finagent/worker/` | `tests/mutations.yaml` entry `ambiguous-not-resent`; `tests/integration/test_workflows.py` | Live connector reconciliation and recovery exercise |
| Tool subject scope | `src/finagent/integrations/tool_gateway/` | `tests/mutations.yaml` entry `tool-subject-scope` | Independent negative testing and provider-specific permissions |
| Workspace accessibility and navigation | `apps/web/app/workspace/`; `apps/web/components/` | `tests/browser/test_workspace_mobile_navigation.py`; `tests/browser/test_workspace_table_keyboard.py` | Complete browser, assistive-technology and device testing; table keyboard test previously blocked locally |

## How to use this map

1. Pick one concern, follow its source and test references, and identify the precise behavior and assumptions.
2. Check the latest CI run and test artifacts for the exact commit. Mutation checks demonstrate whether selected tests detect a deliberately introduced defect; they are not a full security assessment.
3. Record outcomes, skips, scope and limitations in the [evidence plan](../05-testing-evidence/07-TESTING-AND-EVIDENCE-PLAN.md). The institution must assign owners and approve its acceptance criteria.
4. Update the map when code or contracts change. Do not edit generated requirement traceability files by hand.
