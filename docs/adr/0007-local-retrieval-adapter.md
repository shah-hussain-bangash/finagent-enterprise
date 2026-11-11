# ADR-0007: Local retrieval adapter instead of pgvector for development

**Status:** Accepted (engineering), conflict C-09 — engineering decision, not institutional approval.
**Date:** 2026-10-15

## Context
Developers must run RAG without Azure.

## Decision
`LocalSearchAdapter` implements the same port as the Azure AI Search adapter: BM25 over chunks + deterministic hashed embeddings, with ACL and effective-date filters applied before scoring. pgvector stays an option for a future ADR.

## Consequences
Deterministic tests; retrieval-quality numbers from the local adapter are NOT evidence for the Azure configuration.
