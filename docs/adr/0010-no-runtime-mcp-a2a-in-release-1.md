# ADR-0010: No runtime MCP/A2A in Release 1

**Status:** Accepted (engineering) — engineering decision, not institutional approval.
**Date:** 2026-10-15

## Context
Master §11 describes MCP/A2A; R1 needs only typed read-only connectors.

## Decision
The Tool Gateway port is protocol-neutral. MCP/A2A adapters may be added later only via administrator-registered ToolDefinitions; runtime tool discovery is rejected.

## Consequences
Smaller attack surface for the pilot.
