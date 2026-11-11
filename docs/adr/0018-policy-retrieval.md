# ADR-0018: Policy knowledge lifecycle and permission-first retrieval

Status: Accepted (engineering) - 2026-10-16

## Decision
- Policy versions carry owner, department, classification, applicability (alert types), effective
  window, allowed roles and a content hash; ingestion is RECEIVED -> SCANNED -> PARSED -> INDEXED but
  only an explicit APPROVE (Policy Admin, MFA) makes a version retrievable. RETIRE/REVOKE deactivate
  chunks in the same transaction and bump `index_version`.
- Retrieval filters in SQL first (tenant, APPROVED+INDEXED, effective on the as-of date, role ACL
  overlap, applicability) and only then ranks: local deterministic BM25 (ADR-0007) or Azure AI Search
  (query filtered to the authorized versions AND every hit re-checked against that set).
- Outcomes: ANSWERED / INSUFFICIENT_EVIDENCE (abstain, ask the policy owner; never general knowledge)
  / CONFLICTING_GUIDANCE (two approved versions of one policy effective on the same date).
- Citations are structured `{policy_version_id, version_label, section, page, chunk_id, text_hash}` and
  validated per property (exists, version, current access, location, provenance, lexical support).
  The support check is a documented lexical proxy, not semantic proof.

## Consequences
Synced SYNTHETIC policies default to all case-working roles (DATA-D07 OPEN). Azure AI Search index
population is implemented as an adapter but LIVE NOT VERIFIED.
