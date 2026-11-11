# Tool Permission Matrix (prompt §12, §45; FR-024, SPR-029, AER-025)

Source of truth: `src/finagent/integrations/tool_gateway/definitions.py` (frozen registry). There is no
runtime tool discovery.

**Design as built (ADR-0019):** capabilities (AG-KYC, AG-AML, AG-COMPLIANCE, AG-REPORT, CAP-DOCUMENT)
never invoke tools. Deterministic orchestration (`SVC-EVIDENCE`, the workflow's evidence-gathering
step) calls the read tools on the capabilities' behalf with the REQUESTER's current rights, and the
finalization service is the only caller of the single write. The registry still records each
capability's intended source scope (`permitted_tools`, `permitted_source_types`) for audit and future
use.

| Tool | Version | Side effect | Required permission (requester) | Data classes | Scope pinning | SVC-EVIDENCE (workflow) | Capabilities | SVC-FINALIZATION |
|---|---|---|---|---|---|---|---|---|
| `get_customer_profile` | 1 | READ_ONLY | case:read + case in scope | CONFIDENTIAL | subject in alert subjects | yes | never | no |
| `list_kyc_documents` | 1 | READ_ONLY | as above | CONFIDENTIAL | subject | yes | never | no |
| `fetch_kyc_document` | 1 | READ_ONLY | as above | CONFIDENTIAL | subject; document must be listed by the source FOR that subject | yes | never | no |
| `get_transactions` | 1 | READ_ONLY | as above | CONFIDENTIAL | subject + window within the alert window | yes | never | no |
| `get_sanctions_summary` | 1 | READ_ONLY | as above | RESTRICTED | subject | yes | never | no |
| `update_case_status` | 1 | CASE_STATUS_WRITE | approval:decide + case in scope (approving reviewer) | - | case of the run | no | **never** | only if `case_status_write_enabled` AND tenant flag `case_status_write`, via the effect ledger |

**Not tools (no definition exists; any request is `UNKNOWN_TOOL`, denied and audited):** money
transfer/payment, freeze/block, credit decisions, risk-score changes, sanctions decisions, SAR/STR
filing, role or permission changes, arbitrary HTTP/URL fetch, shell, filesystem, database access.

Every decision (allowed, denied, failed) is persisted in `tool_invocations` with an argument hash
only and audited (`TOOL_INVOKED` / `TOOL_DENIED` / `TOOL_FAILED`).
