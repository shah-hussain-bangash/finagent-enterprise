"""Operational views, management reporting, metrics and the LOCAL-ONLY development sign-in.

* `/cases/{id}/workflows`     run list for the case (scoped like the case).
* `/operations`               uncertain/failed external operations queue (operators; metadata only).
* `/reporting/overview`       management/operational counts (prompt §39, NQ-038). Counts are computed
                              inside the caller's row scope: they never reveal inaccessible records.
* `/metrics`                  Prometheus text for the caller's tenant (PLATFORM_OPERATE bearer).
                              Labels are enums only - no identifiers, names or content (AER-027).
* `/dev/identities`, `/dev/session`  - development sign-in for the web UI. Exists ONLY when the API
                              runs in local/test with the local issuer; otherwise 404 (ADR-0008).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel, ConfigDict, StringConstraints
from sqlalchemy import and_, func, select, text

from finagent.api.deps import AppSettings, Factory, Principal
from finagent.core.config import LOCAL_ISSUER, LOCAL_ONLY_ENVIRONMENTS, AuthMode
from finagent.core.errors import AppError, ErrorCode, not_found
from finagent.database.models import (
    ApprovalRequest,
    AuditEvent,
    Case,
    DeadLetter,
    Escalation,
    ExternalOperation,
    ModelRequest,
    QuarantineRecord,
    RoleAssignment,
    WorkflowRun,
)
from finagent.database.models import (
    Principal as PrincipalRow,
)
from finagent.database.session import transaction
from finagent.security.authentication.tokens import LocalDevTokenValidator
from finagent.security.authorization.policy import (
    CaseScope,
    Permission,
    case_scope,
    require,
)
from finagent.services.case_service.queries import load_scoped_case, scope_filter
from finagent.services.reporting_service.dashboard import dashboard
from finagent.services.workflow_service.diagnostics import get_diagnostics
from finagent.services.workflow_service.runs import run_view

ops = APIRouter(prefix="/api/v1")


@ops.get("/cases/{case_id}/workflows", tags=["workflows"])
def case_workflows(case_id: str, ctx: Principal, factory: Factory) -> dict[str, Any]:
    require(ctx, Permission.CASE_READ)
    with transaction(factory) as s:
        case = load_scoped_case(s, ctx, case_id)
        if case is None:
            raise not_found()
        runs = s.scalars(
            select(WorkflowRun)
            .where(WorkflowRun.tenant_id == ctx.tenant_id, WorkflowRun.case_id == case.id)
            .order_by(WorkflowRun.created_at.desc())
        ).all()
        return {"items": [run_view(s, r) for r in runs]}


@ops.get("/cases/{case_id}/activity", tags=["audit"])
def case_activity(case_id: str, ctx: Principal, factory: Factory) -> dict[str, Any]:
    """Case audit timeline for people working the case (prompt §23 case detail). Metadata only -
    the same fields as /audit-events, limited to ONE case the caller may read."""
    require(ctx, Permission.CASE_READ)
    with transaction(factory) as s:
        case = load_scoped_case(s, ctx, case_id)
        if case is None:
            raise not_found()
        rows = s.scalars(
            select(AuditEvent)
            .where(AuditEvent.tenant_id == ctx.tenant_id, AuditEvent.case_id == case.id)
            .order_by(AuditEvent.seq)
            .limit(500)
        ).all()
        return {
            "items": [
                {
                    "seq": e.seq,
                    "occurred_at": e.occurred_at.isoformat(),
                    "action": e.action,
                    "object_type": e.object_type,
                    "object_id": e.object_id,
                    "outcome": e.outcome,
                    "actor_kind": e.actor_kind,
                    "actor_principal_id": str(e.actor_principal_id) if e.actor_principal_id else None,
                    "case_id": str(e.case_id),
                }
                for e in rows
            ]
        }


@ops.get("/operations", tags=["operations"])
def list_operations(
    ctx: Principal, factory: Factory, status: str | None = "OUTCOME_UNCERTAIN"
) -> dict[str, Any]:
    require(ctx, Permission.OPERATION_RECONCILE)
    with transaction(factory) as s:
        stmt = select(ExternalOperation).where(ExternalOperation.tenant_id == ctx.tenant_id)
        if status:
            stmt = stmt.where(ExternalOperation.status == status)
        rows = s.scalars(stmt.order_by(ExternalOperation.updated_at.desc()).limit(100)).all()
        return {
            "items": [
                {
                    "operation_id": str(o.id),
                    "operation_type": o.operation_type,
                    "target_system": o.target_system,
                    "status": o.status,
                    "attempts": o.attempts,
                    "last_error_code": o.last_error_code,
                    "run_id": str(o.run_id) if o.run_id else None,
                    "case_id": str(o.case_id) if o.case_id else None,
                    "updated_at": o.updated_at.isoformat(),
                }
                for o in rows
            ]
        }


def _counts(s: Any, stmt: Any) -> dict[str, int]:
    return {str(k): int(v) for k, v in s.execute(stmt).all()}


@ops.get("/reporting/overview", tags=["reporting"])
def overview(ctx: Principal, factory: Factory) -> dict[str, Any]:
    if not ({Permission.CASE_READ, Permission.PLATFORM_OPERATE} & ctx.permissions):
        raise AppError(ErrorCode.FORBIDDEN)
    with transaction(factory) as s:
        out: dict[str, Any] = {"generated_at": datetime.now(UTC).isoformat(), "scope": "own-cases"}
        if Permission.CASE_READ in ctx.permissions:
            scoped = and_(Case.tenant_id == ctx.tenant_id, scope_filter(ctx))
            out["cases_by_status"] = _counts(
                s, select(Case.status, func.count()).where(scoped).group_by(Case.status)
            )
            out["pending_reviews"] = int(
                s.scalar(
                    select(func.count(ApprovalRequest.id))
                    .join(
                        Case,
                        and_(Case.tenant_id == ApprovalRequest.tenant_id, Case.id == ApprovalRequest.case_id),
                    )
                    .where(scoped, ApprovalRequest.status == "PENDING")
                )
                or 0
            )
            out["open_escalations"] = int(
                s.scalar(
                    select(func.count(Escalation.id))
                    .join(Case, and_(Case.tenant_id == Escalation.tenant_id, Case.id == Escalation.case_id))
                    .where(scoped, Escalation.resolved_at.is_(None))
                )
                or 0
            )
            out["open_quarantine"] = int(
                s.scalar(
                    select(func.count(QuarantineRecord.id))
                    .join(
                        Case,
                        and_(
                            Case.tenant_id == QuarantineRecord.tenant_id, Case.id == QuarantineRecord.case_id
                        ),
                    )
                    .where(scoped, QuarantineRecord.status == "OPEN")
                )
                or 0
            )
            out["scope"] = "tenant" if case_scope(ctx) is CaseScope.TENANT else "assigned-cases"
        if Permission.PLATFORM_OPERATE in ctx.permissions:
            out["runs_by_status"] = _counts(
                s,
                select(WorkflowRun.status, func.count())
                .where(WorkflowRun.tenant_id == ctx.tenant_id)
                .group_by(WorkflowRun.status),
            )
            out["uncertain_operations"] = int(
                s.scalar(
                    select(func.count(ExternalOperation.id)).where(
                        ExternalOperation.tenant_id == ctx.tenant_id,
                        ExternalOperation.status == "OUTCOME_UNCERTAIN",
                    )
                )
                or 0
            )
            out["open_dead_letters"] = int(
                s.scalar(
                    select(func.count(DeadLetter.id)).where(
                        DeadLetter.tenant_id == ctx.tenant_id, DeadLetter.status == "OPEN"
                    )
                )
                or 0
            )
            month = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            usage = s.execute(
                select(
                    func.count(),
                    func.coalesce(func.sum(ModelRequest.input_tokens + ModelRequest.output_tokens), 0),
                    func.coalesce(func.sum(ModelRequest.estimated_cost), 0),
                ).where(ModelRequest.tenant_id == ctx.tenant_id, ModelRequest.created_at >= month)
            ).one()
            out["model_usage_month"] = {
                "requests": int(usage[0]),
                "tokens": int(usage[1]),
                "estimated_cost": str(usage[2]),
            }
            out["model_requests_by_status"] = _counts(
                s,
                select(ModelRequest.status, func.count())
                .where(ModelRequest.tenant_id == ctx.tenant_id, ModelRequest.created_at >= month)
                .group_by(ModelRequest.status),
            )
        return out


@ops.get("/metrics", tags=["health"], include_in_schema=False)
def metrics(ctx: Principal, factory: Factory) -> Response:
    """Prometheus exposition for the operator's tenant (safe enum labels and counts only)."""
    require(ctx, Permission.PLATFORM_OPERATE)
    lines: list[str] = []
    with transaction(factory) as s:

        def gauge(name: str, help_text: str, rows: dict[str, int], label: str) -> None:
            lines.extend([f"# HELP {name} {help_text}", f"# TYPE {name} gauge"])
            lines.extend(f'{name}{{{label}="{k}"}} {v}' for k, v in sorted(rows.items()))

        gauge(
            "finagent_cases",
            "Cases by status",
            _counts(
                s,
                select(Case.status, func.count())
                .where(Case.tenant_id == ctx.tenant_id)
                .group_by(Case.status),
            ),
            "status",
        )
        gauge(
            "finagent_workflow_runs",
            "Workflow runs by status",
            _counts(
                s,
                select(WorkflowRun.status, func.count())
                .where(WorkflowRun.tenant_id == ctx.tenant_id)
                .group_by(WorkflowRun.status),
            ),
            "status",
        )
        gauge(
            "finagent_external_operations",
            "External operations by status",
            _counts(
                s,
                select(ExternalOperation.status, func.count())
                .where(ExternalOperation.tenant_id == ctx.tenant_id)
                .group_by(ExternalOperation.status),
            ),
            "status",
        )
        gauge(
            "finagent_model_requests",
            "Model requests by status (all time)",
            _counts(
                s,
                select(ModelRequest.status, func.count())
                .where(ModelRequest.tenant_id == ctx.tenant_id)
                .group_by(ModelRequest.status),
            ),
            "status",
        )
        gauge(
            "finagent_dead_letters",
            "Dead letters by status",
            _counts(
                s,
                select(DeadLetter.status, func.count())
                .where(DeadLetter.tenant_id == ctx.tenant_id)
                .group_by(DeadLetter.status),
            ),
            "status",
        )
    return Response("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")


# ------------------------------------------------------------------ LOCAL/TEST development sign-in
def _dev_enabled(settings: Any) -> bool:
    return settings.environment in LOCAL_ONLY_ENVIRONMENTS and settings.auth_mode is AuthMode.LOCAL


@ops.get("/dev/identities", tags=["dev"], include_in_schema=False)
def dev_identities(factory: Factory, settings: AppSettings) -> dict[str, Any]:
    if not _dev_enabled(settings):
        raise not_found()
    with transaction(factory) as s:
        rows = s.execute(
            select(PrincipalRow.subject, PrincipalRow.display_name, func.array_agg(RoleAssignment.role))
            .join(
                RoleAssignment,
                and_(
                    RoleAssignment.tenant_id == PrincipalRow.tenant_id,
                    RoleAssignment.principal_id == PrincipalRow.id,
                ),
            )
            .where(
                PrincipalRow.kind == "USER",
                PrincipalRow.status == "ACTIVE",
                PrincipalRow.issuer == LOCAL_ISSUER,
            )
            .group_by(PrincipalRow.subject, PrincipalRow.display_name)
            .order_by(PrincipalRow.subject)
        ).all()
    return {
        "items": [{"subject": r[0], "display_name": r[1], "roles": sorted(r[2])} for r in rows],
        "notice": "SYNTHETIC local identities - development only",
    }


class DevSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    subject: Annotated[str, StringConstraints(pattern=r"^[a-z]\.[a-z0-9-]{2,40}@synthetic$")]
    mfa: bool = True


@ops.post("/dev/session", tags=["dev"], include_in_schema=False)
def dev_session(
    body: DevSessionRequest, request: Request, factory: Factory, settings: AppSettings
) -> dict[str, Any]:
    if not _dev_enabled(settings) or settings.dev_jwt_secret is None:
        raise not_found()
    with transaction(factory) as s:
        exists = s.scalar(
            select(PrincipalRow.id).where(
                PrincipalRow.issuer == LOCAL_ISSUER,
                PrincipalRow.subject == body.subject,
                PrincipalRow.kind == "USER",
                PrincipalRow.status == "ACTIVE",
            )
        )
    if exists is None:
        raise AppError(ErrorCode.UNAUTHENTICATED)
    issuer = LocalDevTokenValidator(settings.dev_jwt_secret.get_secret_value(), settings.api_audience)
    return {
        "access_token": issuer.issue(body.subject, ttl_seconds=8 * 3600, mfa=body.mfa),
        "expires_in": 8 * 3600,
        "token_type": "Bearer",  # nosec B105 - OAuth token-type label, not a credential
        "notice": "LOCAL DEVELOPMENT TOKEN - not valid outside local/test",
    }


@ops.get("/reporting/dashboard", tags=["reporting"])
def operations_dashboard(
    ctx: Principal, factory: Factory, settings: AppSettings, days: int = Query(default=7, ge=1, le=28)
) -> dict[str, Any]:
    with transaction(factory) as session:
        session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        return dashboard(session, ctx, days=days, source_max_age_hours=settings.source_max_age_hours)


# BEGIN WORKFLOW DIAGNOSTICS V1
@ops.get("/workflows/{run_id}/diagnostics", tags=["workflows"])
def workflow_diagnostics(
    run_id: str,
    ctx: Principal,
    factory: Factory,
    response: Response,
) -> dict[str, Any]:
    with transaction(factory) as session:
        session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))

        result = get_diagnostics(
            session,
            ctx,
            run_id,
        )

    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Pragma"] = "no-cache"

    return result


# END WORKFLOW DIAGNOSTICS V1
