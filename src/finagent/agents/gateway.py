"""Central model gateway (prompt §11; AER-019..022; NQ-048; MST:AG-002).

The ONLY path to a model provider. Per call it:
  1. checks the capability is enabled and its limits complete (fail closed, no call),
  2. checks budgets BEFORE spending: per-invocation calls/tokens, per-run tokens/cost, per-tenant
     monthly cost; a breach records BUDGET_DENIED and stops new billable work (case state untouched),
  3. calls the provider with a timeout; retries ONLY classified transient errors, bounded, with backoff,
  4. parses and validates the structured output; at most ONE schema-repair attempt (prompt §10),
  5. records a usage row for EVERY attempt - success, failure, timeout, invalid output, retry, repair -
     in its own committed transaction, so usage survives a failed step.
Raw prompts and outputs are never logged or stored; only hashes and counts.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from finagent.agents.contracts import AgentInput
from finagent.agents.providers import ModelProvider, ProviderError, ProviderRequest
from finagent.agents.registry import (
    CONFIG_VERSION,
    Capability,
    CapabilityUnavailableError,
    require_enabled,
)
from finagent.core.config import Settings
from finagent.core.correlation import get_correlation_id
from finagent.database.models import ModelRequest
from finagent.database.session import transaction
from finagent.domain.fingerprint import fingerprint
from finagent.services.governance_service.capability_incidents import admission

log = logging.getLogger("finagent.models")
T = TypeVar("T", bound=BaseModel)


class BudgetExceededError(Exception):
    def __init__(self, scope: str) -> None:
        super().__init__(scope)
        self.scope = scope


class ModelUnavailableError(Exception):
    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind


class ModelOutputInvalidError(Exception):
    def __init__(self, errors: list[str]) -> None:
        super().__init__("model output failed validation after the permitted repair")
        self.errors = errors


@dataclass(frozen=True)
class CallContext:
    tenant_id: uuid.UUID
    case_id: uuid.UUID | None
    run_id: uuid.UUID | None
    step_id: str


@dataclass
class Usage:
    calls: int = 0
    retries: int = 0
    repairs: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost: Decimal = Decimal("0")
    request_ids: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "calls": self.calls,
            "retries": self.retries,
            "repairs": self.repairs,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost": str(self.cost),
            "request_ids": self.request_ids,
        }


def _schema_errors(exc: Exception) -> list[str]:
    """Safe repair hint: field paths and error types only - never echo model text back."""
    if isinstance(exc, ValidationError):
        return [f"{'.'.join(str(p) for p in e['loc'])}: {e['type']}" for e in exc.errors()][:20]
    return ["output is not valid JSON"]


class ModelGateway:
    def __init__(
        self,
        factory: sessionmaker[Session],
        provider: ModelProvider,
        settings: Settings,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._factory, self._provider, self._s, self._sleep = (
            factory,
            provider,
            settings,
            sleep,
        )

    @property
    def provider_name(self) -> str:
        return self._provider.name

    def _cost(self, input_tokens: int, output_tokens: int) -> Decimal:
        per_k_in = Decimal(self._s.model_price_per_1k_input)
        per_k_out = Decimal(self._s.model_price_per_1k_output)
        return (Decimal(input_tokens) * per_k_in + Decimal(output_tokens) * per_k_out) / Decimal(1000)

    def _record(
        self,
        cap: Capability,
        ctx: CallContext,
        *,
        purpose: str,
        attempt: int,
        status: str,
        model: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        latency_ms: int = 0,
        error_code: str | None = None,
    ) -> ModelRequest:
        row = ModelRequest(
            tenant_id=ctx.tenant_id,
            case_id=ctx.case_id,
            run_id=ctx.run_id,
            capability_id=cap.capability_id,
            capability_version=cap.version,
            provider=self._provider.name[:64],
            model=model[:100],
            prompt_version=cap.prompt_version,
            prompt_hash=cap.prompt_hash,
            config_version=CONFIG_VERSION,
            purpose=purpose,
            attempt=attempt,
            status=status,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost=self._cost(input_tokens, output_tokens),
            latency_ms=latency_ms,
            error_code=error_code,
            correlation_id=get_correlation_id(),
        )
        with transaction(self._factory) as s:
            s.add(row)
        return row

    def _spent(self, ctx: CallContext) -> tuple[int, Decimal, Decimal]:
        with transaction(self._factory) as s:
            run_tokens, run_cost = 0, Decimal(0)
            if ctx.run_id is not None:
                row = s.execute(
                    select(
                        func.coalesce(
                            func.sum(ModelRequest.input_tokens + ModelRequest.output_tokens),
                            0,
                        ),
                        func.coalesce(func.sum(ModelRequest.estimated_cost), 0),
                    ).where(
                        ModelRequest.tenant_id == ctx.tenant_id,
                        ModelRequest.run_id == ctx.run_id,
                    )
                ).one()
                run_tokens, run_cost = int(row[0]), Decimal(row[1])
            month_start = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            tenant_cost = Decimal(
                s.scalar(
                    select(func.coalesce(func.sum(ModelRequest.estimated_cost), 0)).where(
                        ModelRequest.tenant_id == ctx.tenant_id,
                        ModelRequest.created_at >= month_start,
                    )
                )
                or 0
            )
        return run_tokens, run_cost, tenant_cost

    def _check_budget(self, cap: Capability, ctx: CallContext, usage: Usage, attempt: int) -> None:
        scope = None
        if usage.calls >= cap.max_model_calls:
            scope = "CAPABILITY_CALLS"
        elif usage.input_tokens + usage.output_tokens >= cap.token_budget:
            scope = "CAPABILITY_TOKENS"
        elif usage.cost > cap.cost_budget:
            scope = "CAPABILITY_COST"
        else:
            run_tokens, run_cost, tenant_cost = self._spent(ctx)
            if run_tokens >= self._s.run_token_budget:
                scope = "RUN_TOKENS"
            elif run_cost >= Decimal(self._s.run_cost_budget):
                scope = "RUN_COST"
            elif tenant_cost >= Decimal(self._s.tenant_monthly_cost_budget):
                scope = "TENANT_MONTHLY_COST"
        if scope is not None:
            self._record(
                cap,
                ctx,
                purpose="INITIAL" if attempt == 1 else "RETRY",
                attempt=attempt,
                status="BUDGET_DENIED",
                model="-",
                error_code=scope,
            )
            log.warning(
                "model_budget_denied",
                extra={"capability": cap.capability_id, "scope": scope},
            )
            raise BudgetExceededError(scope)

    def invoke(
        self,
        capability_id: str,
        agent_input: AgentInput,
        output_model: type[T],
        ctx: CallContext,
        should_stop: Callable[[], bool] | None = None,
    ) -> tuple[T, Usage]:
        require_enabled(capability_id)
        try:
            with admission(self._factory, ctx.tenant_id, capability_id):
                return self._invoke_admitted(capability_id, agent_input, output_model, ctx, should_stop)
        except CapabilityUnavailableError as exc:
            if exc.reason == "CRITICAL_INCIDENT_ACTIVE":
                self._record(
                    require_enabled(capability_id),
                    ctx,
                    purpose="INITIAL",
                    attempt=1,
                    status="FAILED",
                    model="-",
                    error_code=exc.reason,
                )
            raise

    def _invoke_admitted(
        self,
        capability_id: str,
        agent_input: AgentInput,
        output_model: type[T],
        ctx: CallContext,
        should_stop: Callable[[], bool] | None = None,
    ) -> tuple[T, Usage]:
        cap = require_enabled(capability_id)
        if self._s.require_promoted_configuration:
            from finagent.services.governance_service.config import (
                current_configuration,
                is_promoted,
            )

            with transaction(self._factory) as s:
                promoted = is_promoted(s, ctx.tenant_id, current_configuration(self._s)["config_hash"])
            if not promoted:  # fail closed before any provider call (AER-031..034)
                raise CapabilityUnavailableError(capability_id, "CONFIGURATION_NOT_PROMOTED")
        usage = Usage()
        user = json.dumps(agent_input.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        schema = output_model.model_json_schema()
        repair_hint: list[str] | None = None
        transient_left = self._s.model_max_retries
        purpose, attempt = "INITIAL", 0
        while True:
            attempt += 1
            if should_stop is not None and should_stop():
                raise ModelUnavailableError("CANCELLED")
            self._check_budget(cap, ctx, usage, attempt)
            content = user if repair_hint is None else f"{user}\n<repair>{json.dumps(repair_hint)}</repair>"
            request = ProviderRequest(
                capability_id=cap.capability_id,
                model=cap.model_alias,
                system=cap.prompt_text,
                user=content,
                output_schema=schema,
                timeout_s=min(cap.timeout_s, self._s.model_timeout_seconds),
                max_output_tokens=4096,
            )
            t0 = time.monotonic()
            usage.calls += 1
            try:
                response = self._provider.complete(request)
            except ProviderError as exc:
                latency = int((time.monotonic() - t0) * 1000)
                row = self._record(
                    cap,
                    ctx,
                    purpose=purpose,
                    attempt=attempt,
                    latency_ms=latency,
                    model="-",
                    status="TIMEOUT" if exc.kind == "TIMEOUT" else "FAILED",
                    error_code=exc.kind,
                )
                usage.request_ids.append(str(row.id))
                if exc.transient and transient_left > 0:
                    transient_left -= 1
                    usage.retries += 1
                    purpose = "RETRY"
                    self._sleep(self._s.model_retry_backoff_seconds * (2**usage.retries - 1))
                    continue
                raise ModelUnavailableError(exc.kind) from exc
            latency = int((time.monotonic() - t0) * 1000)
            usage.input_tokens += response.input_tokens
            usage.output_tokens += response.output_tokens
            usage.cost += self._cost(response.input_tokens, response.output_tokens)
            try:
                parsed = output_model.model_validate(json.loads(response.text))
            except (
                ValueError,
                ValidationError,
            ) as exc:  # json.JSONDecodeError is a ValueError
                errors = _schema_errors(exc)
                row = self._record(
                    cap,
                    ctx,
                    purpose=purpose,
                    attempt=attempt,
                    status="INVALID_OUTPUT",
                    model=response.model,
                    input_tokens=response.input_tokens,
                    output_tokens=response.output_tokens,
                    latency_ms=latency,
                    error_code="SCHEMA_INVALID",
                )
                usage.request_ids.append(str(row.id))
                if usage.repairs < self._s.model_max_repairs:
                    usage.repairs += 1
                    repair_hint, purpose = errors, "REPAIR"
                    continue
                raise ModelOutputInvalidError(errors) from exc
            row = self._record(
                cap,
                ctx,
                purpose=purpose,
                attempt=attempt,
                status="SUCCEEDED",
                model=response.model,
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
                latency_ms=latency,
            )
            usage.request_ids.append(str(row.id))
            log.info(
                "model_call",
                extra={
                    "capability": cap.capability_id,
                    "attempts": attempt,
                    "tokens": usage.input_tokens + usage.output_tokens,
                    "output_hash": fingerprint(response.text)[:16],  # hash only, never content
                },
            )
            return parsed, usage
