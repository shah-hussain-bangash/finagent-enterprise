# ADR-0019: Model gateway, capability registry and deterministic validation

Status: Accepted (engineering) - 2026-10-16

## Decision
- Capabilities (AG-KYC, AG-AML, AG-COMPLIANCE, AG-REPORT, CAP-DOCUMENT; AG-RISK FUTURE) are a frozen,
  versioned registry with prompt hashes and limits; non-enabled or incompletely limited capabilities
  fail closed before any call.
- Capabilities have no tools of their own: deterministic orchestration gathers evidence through the
  tool gateway and retrieves policy as the requester; the capability only receives minimized facts
  (e.g. AG-AML never sees name/DOB/nationality) and references.
- `ModelGateway` is the only path to a provider: budget checks before each call (capability calls and
  tokens, run tokens/cost, tenant monthly cost), timeout, transient-only bounded retries, at most one
  schema repair, a usage row for every attempt, hashes instead of prompts in logs.
- All numbers come from `domain/aml_calculations` (Decimal, per currency, disclosed exclusions).
- `validation.py` decides acceptance: authority-boundary language rejects the whole output; references
  must resolve; numbers must occur in referenced sources; deterministic values must match exactly;
  statuses never upgrade. Rejected claims never reach a report.
- Providers: deterministic SYNTHETIC `MockModelProvider` (scriptable faults for tests/evaluation) and
  `AzureOpenAIProvider` (JSON-schema output; LIVE NOT VERIFIED).
- Configuration promotion: when `require_promoted_configuration` (mandatory outside local/test) the
  gateway refuses unpromoted configuration hashes; promotion needs a PASS_PROPOSED evaluation.

## Consequences
The mock provider builds its answers from the structured input, so local quality metrics measure the
platform's controls, not a real model's quality. Real-model evaluation needs an approved deployment.
