"""Tool Gateway (prompt §12; FR-024; SPR-029; AER-025; MST:INT-001..003).

Every call is authorized independently of the model, in this order, and EVERY decision - allowed,
denied or failed - is persisted (tool_invocations) and audited in its own committed transaction:

  1. tool exists in the frozen registry (no runtime discovery)      -> UNKNOWN_TOOL
  2. tool enabled                                                   -> TOOL_DISABLED
  3. read-only via `invoke`; the write has its own guarded path     -> WRITE_TOOL_NOT_INVOKABLE
  4. tool in the calling capability's allowlist                     -> TOOL_NOT_PERMITTED_FOR_CAPABILITY
  5. case exists in the execution tenant (scope loaded from the DB) -> TENANT_SCOPE
  6. requester's CURRENT rights cover the tool and the case         -> PRINCIPAL_NOT_AUTHORIZED
  7. arguments match the strict schema (no extra fields/URLs)       -> INVALID_ARGUMENTS
  8. subject and time window stay inside the case scope             -> SCOPE_VIOLATION
  9. tool data classes permitted for the capability                 -> DATA_CLASS_NOT_PERMITTED
The scope (tenant, subjects, window) is ALWAYS re-derived from the database; nothing the model
supplies can widen it. Arguments are stored only as a hash.
"""

from __future__ import annotations

import base64
import logging
import re
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, NoReturn

from pydantic import ValidationError
from sqlalchemy import and_, select
from sqlalchemy.orm import Session, sessionmaker

from finagent.agents.registry import CapabilityUnavailableError
from finagent.core.config import Settings
from finagent.core.correlation import get_correlation_id
from finagent.database.models import Alert, Case, ExternalOperation, ToolInvocation
from finagent.database.session import transaction
from finagent.domain.fingerprint import fingerprint
from finagent.integrations.factory import ConnectorFactory
from finagent.integrations.resilience import ConnectorError, ErrorKind
from finagent.integrations.sources import CaseStatusConnector
from finagent.integrations.tool_gateway.definitions import (
    TOOLS,
    CaseStatusArgs,
    SideEffect,
    ToolDefinition,
    TransactionArgs,
    window_within,
)
from finagent.security.authorization.policy import PrincipalContext
from finagent.services.admin_service.feature_flags import CASE_STATUS_WRITE, is_enabled
from finagent.services.audit_service import writer as audit
from finagent.services.case_service.queries import load_scoped_case
from finagent.services.governance_service.capability_incidents import admission
from finagent.services.workflow_service.external_ops import OperationSpec, execute_once
from finagent.worker.leases import LeaseHandle

log = logging.getLogger("finagent.tools")
_SAFE_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
FINALIZATION_CAPABILITY = "SVC-FINALIZATION"


class ToolDeniedError(Exception):
    def __init__(self, reason: str, invocation_id: uuid.UUID) -> None:
        super().__init__(reason)
        self.reason, self.invocation_id = reason, invocation_id


class ToolFailedError(Exception):
    def __init__(self, kind: str, transient: bool, invocation_id: uuid.UUID) -> None:
        super().__init__(kind)
        self.kind, self.transient, self.invocation_id = kind, transient, invocation_id


@dataclass(frozen=True)
class ExecutionContext:
    """Built by deterministic orchestration code - never from model output (FR-024)."""

    tenant_id: uuid.UUID
    case_id: uuid.UUID
    run_id: uuid.UUID | None
    step_id: str
    capability_id: str
    principal: PrincipalContext  # the requester's rights, re-resolved from the database
    allowed_tools: frozenset[str]
    data_classes: frozenset[str]


@dataclass(frozen=True)
class CaseScope:
    case_ref: str
    subject_refs: frozenset[str]
    window_start: datetime
    window_end: datetime


@dataclass(frozen=True)
class ToolResult:
    tool: str
    version: str
    output: dict[str, Any]
    invocation_id: uuid.UUID


def _args_hash(raw: Any) -> str:
    try:
        return fingerprint(raw)
    except TypeError:
        return fingerprint(repr(raw))


class ToolGateway:
    def __init__(
        self,
        factory: sessionmaker[Session],
        connectors: ConnectorFactory,
        settings: Settings,
    ) -> None:
        self._factory, self._connectors, self._settings = factory, connectors, settings

    # ------------------------------------------------------------------ bookkeeping
    def _record(
        self,
        ctx: ExecutionContext,
        tool_name: str,
        tool: ToolDefinition | None,
        outcome: str,
        reason: str | None,
        args_hash: str,
        started: float,
    ) -> uuid.UUID:
        safe_name = tool_name if _SAFE_NAME.fullmatch(tool_name or "") else "<invalid-name>"
        inv_id = uuid.uuid4()
        with transaction(self._factory) as s:
            case_exists = s.scalar(
                select(Case.id).where(Case.tenant_id == ctx.tenant_id, Case.id == ctx.case_id)
            )
            s.add(
                ToolInvocation(
                    id=inv_id,
                    tenant_id=ctx.tenant_id,
                    case_id=case_exists,
                    run_id=ctx.run_id,
                    capability_id=ctx.capability_id[:64],
                    tool_name=safe_name,
                    tool_version=tool.version if tool else None,
                    outcome=outcome,
                    reason_code=reason,
                    args_hash=args_hash,
                    latency_ms=int((time.monotonic() - started) * 1000),
                    correlation_id=get_correlation_id(),
                )
            )
            audit.record(
                s,
                tenant_id=ctx.tenant_id,
                action={
                    "ALLOWED": "TOOL_INVOKED",
                    "DENIED": "TOOL_DENIED",
                    "FAILED": "TOOL_FAILED",
                }[outcome],
                object_type="tool_invocation",
                object_id=str(inv_id),
                outcome=outcome,
                actor_principal_id=ctx.principal.principal_id,
                actor_kind="SYSTEM",
                case_id=case_exists,
                details={
                    "tool": safe_name,
                    "capability": ctx.capability_id[:64],
                    "reason": reason,
                },
            )
        if outcome != "ALLOWED":
            log.warning("tool_" + outcome.lower(), extra={"tool": safe_name, "reason": reason})
        return inv_id

    def _deny(
        self,
        ctx: ExecutionContext,
        name: str,
        tool: ToolDefinition | None,
        reason: str,
        h: str,
        t0: float,
    ) -> NoReturn:
        raise ToolDeniedError(reason, self._record(ctx, name, tool, "DENIED", reason, h, t0))

    def scope(self, session: Session, tenant_id: uuid.UUID, case_id: uuid.UUID) -> CaseScope | None:
        row = session.execute(
            select(Case, Alert)
            .join(
                Alert,
                and_(Alert.tenant_id == Case.tenant_id, Alert.id == Case.alert_id),
            )
            .where(Case.tenant_id == tenant_id, Case.id == case_id)
        ).first()
        if row is None:
            return None
        case, alert = row
        return CaseScope(
            str(case.id),
            frozenset(alert.subject_refs),
            alert.window_start,
            alert.window_end,
        )

    # ------------------------------------------------------------------ authorization
    def _authorize(
        self, ctx: ExecutionContext, name: str, raw_args: Any, *, write: bool
    ) -> tuple[ToolDefinition, Any, CaseScope, str, float]:
        t0, h = time.monotonic(), _args_hash(raw_args)
        tool = TOOLS.get(name)
        if tool is None:
            self._deny(ctx, name, None, "UNKNOWN_TOOL", h, t0)
        if not tool.enabled:
            self._deny(ctx, name, tool, "TOOL_DISABLED", h, t0)
        if (tool.side_effect is not SideEffect.READ_ONLY) != write:
            self._deny(
                ctx,
                name,
                tool,
                "WRITE_TOOL_NOT_INVOKABLE" if not write else "NOT_A_WRITE_TOOL",
                h,
                t0,
            )
        if not write and name not in ctx.allowed_tools:
            self._deny(ctx, name, tool, "TOOL_NOT_PERMITTED_FOR_CAPABILITY", h, t0)
        with transaction(self._factory) as s:
            scope = self.scope(s, ctx.tenant_id, ctx.case_id)
            in_scope = scope is not None and load_scoped_case(s, ctx.principal, str(ctx.case_id)) is not None
        if scope is None or ctx.principal.tenant_id != ctx.tenant_id:
            self._deny(ctx, name, tool, "TENANT_SCOPE", h, t0)
        if not tool.required_permissions <= ctx.principal.permissions or not in_scope:
            self._deny(ctx, name, tool, "PRINCIPAL_NOT_AUTHORIZED", h, t0)
        try:
            args = tool.input_model.model_validate(raw_args)
        except ValidationError:
            self._deny(ctx, name, tool, "INVALID_ARGUMENTS", h, t0)
        if tool.subject_field and getattr(args, tool.subject_field) not in scope.subject_refs:
            self._deny(ctx, name, tool, "SCOPE_VIOLATION", h, t0)
        if isinstance(args, TransactionArgs) and not window_within(
            args.window_start, args.window_end, scope.window_start, scope.window_end
        ):
            self._deny(ctx, name, tool, "SCOPE_VIOLATION", h, t0)
        if not tool.data_classes <= ctx.data_classes:
            self._deny(ctx, name, tool, "DATA_CLASS_NOT_PERMITTED", h, t0)
        return tool, args, scope, h, t0

    # ------------------------------------------------------------------ read tools
    def invoke(self, ctx: ExecutionContext, name: str, raw_args: Any) -> ToolResult:
        try:
            with admission(self._factory, ctx.tenant_id, ctx.capability_id):
                return self._invoke_admitted(ctx, name, raw_args)
        except CapabilityUnavailableError:
            self._deny(
                ctx,
                name,
                TOOLS.get(name),
                "CRITICAL_INCIDENT_ACTIVE",
                _args_hash(raw_args),
                time.monotonic(),
            )

    def _invoke_admitted(self, ctx: ExecutionContext, name: str, raw_args: Any) -> ToolResult:
        tool, args, scope, h, t0 = self._authorize(ctx, name, raw_args, write=False)
        try:
            output = self._run(ctx.tenant_id, tool.handler, args, scope)
        except ConnectorError as exc:
            inv = self._record(ctx, name, tool, "FAILED", exc.kind.value, h, t0)
            raise ToolFailedError(exc.kind.value, exc.transient, inv) from exc
        inv = self._record(ctx, name, tool, "ALLOWED", None, h, t0)
        return ToolResult(tool.name, tool.version, output, inv)

    def _run(self, tenant_id: uuid.UUID, handler: str, args: Any, scope: CaseScope) -> dict[str, Any]:
        c = self._connectors
        if handler == "get_customer_profile":
            profile, meta = c.customers(tenant_id).profile(args.customer_ref)
            return {
                "profile": profile.model_dump(mode="json"),
                "meta": meta.model_dump(mode="json"),
            }
        if handler == "list_kyc_documents":
            docs, meta = c.customers(tenant_id).documents(args.customer_ref)
            return {
                "documents": [d.model_dump(mode="json") for d in docs],
                "meta": meta.model_dump(mode="json"),
            }
        if handler == "get_transactions":
            window = c.transactions(tenant_id).fetch_window(
                args.customer_ref,
                args.window_start or scope.window_start,
                args.window_end or scope.window_end,
            )
            return window.model_dump(mode="json")
        if handler == "fetch_kyc_document":
            # The source must list this document for THIS customer: an id alone cannot reach
            # another customer's file (scope is re-derived, never taken from the arguments).
            docs, _ = c.customers(tenant_id).documents(args.customer_ref)
            match = next((d for d in docs if d.document_id == args.document_id), None)
            if match is None:
                raise ConnectorError(ErrorKind.NOT_FOUND, "core-banking")
            data, media_type = c.documents(tenant_id).content(args.document_id)
            return {
                "document": match.model_dump(mode="json"),
                "media_type": media_type.split(";", 1)[0].strip(),
                "content_b64": base64.b64encode(data).decode(),
            }
        if handler == "get_sanctions_summary":
            summary, meta = c.sanctions(tenant_id).summary(args.customer_ref)
            return {
                "summary": summary.model_dump(mode="json"),
                "meta": meta.model_dump(mode="json"),
            }
        raise RuntimeError(f"no handler bound for {handler}")

    # ------------------------------------------------------------------ the single write
    def invoke_case_status_write(
        self,
        ctx: ExecutionContext,
        raw_args: Any,
        operation_key: str,
        lease: LeaseHandle | None = None,
        fault: Callable[[str], None] | None = None,
    ) -> ExternalOperation:
        try:
            with admission(self._factory, ctx.tenant_id, ctx.capability_id):
                return self._invoke_case_status_write_admitted(ctx, raw_args, operation_key, lease, fault)
        except CapabilityUnavailableError:
            self._deny(
                ctx,
                "update_case_status",
                TOOLS.get("update_case_status"),
                "CRITICAL_INCIDENT_ACTIVE",
                _args_hash(raw_args),
                time.monotonic(),
            )

    def _invoke_case_status_write_admitted(
        self,
        ctx: ExecutionContext,
        raw_args: Any,
        operation_key: str,
        lease: LeaseHandle | None = None,
        fault: Callable[[str], None] | None = None,
    ) -> ExternalOperation:
        """Finalization only (DATA-D06): deployment setting AND tenant flag AND the finalization
        capability AND the approving principal's rights. Executed through the effect ledger."""
        name = "update_case_status"
        tool, args, scope, h, t0 = self._authorize(ctx, name, raw_args, write=True)
        if ctx.capability_id != FINALIZATION_CAPABILITY:
            self._deny(ctx, name, tool, "WRITE_NOT_PERMITTED_FOR_CAPABILITY", h, t0)
        if not self._settings.case_status_write_enabled:
            self._deny(ctx, name, tool, "CASE_STATUS_WRITE_DISABLED", h, t0)
        with transaction(self._factory) as s:
            flag = is_enabled(s, ctx.tenant_id, CASE_STATUS_WRITE)
        if not flag:
            self._deny(ctx, name, tool, "CASE_STATUS_WRITE_FLAG_OFF", h, t0)
        if not isinstance(args, CaseStatusArgs):
            self._deny(ctx, name, tool, "INVALID_ARGUMENTS", h, t0)
        effect = CaseStatusConnector(
            self._connectors.client(ctx.tenant_id),
            scope.case_ref,
            args.status,
            args.reason_code,
        )
        op = execute_once(
            self._factory,
            OperationSpec(
                tenant_id=ctx.tenant_id,
                operation_type="CASE_STATUS_UPDATE",
                operation_key=operation_key,
                request_hash=fingerprint(args.model_dump(mode="json")),
                case_id=ctx.case_id,
                run_id=ctx.run_id,
            ),
            effect,
            lease=lease,
            fault=fault,
        )
        self._record(ctx, name, tool, "ALLOWED", None, h, t0)
        return op
