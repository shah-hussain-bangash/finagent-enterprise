"""Operational reporting (FR-037/038, AER-035), with explicit proxy and scope definitions."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, case, cast, extract, func, select
from sqlalchemy.dialects.postgresql import JSONPATH
from sqlalchemy.orm import Session, load_only

from finagent.core.errors import AppError, ErrorCode
from finagent.database.models import (
    AgentOutput,
    Alert,
    ApprovalDecision,
    ApprovalRequest,
    Case,
    CaseStatusHistory,
    ConnectorObservation,
    EvidenceItem,
    ExternalOperation,
    ModelRequest,
    Report,
    ReportVersion,
    ToolInvocation,
    WorkflowRun,
    WorkflowRunHistory,
    WorkflowStep,
)
from finagent.security.authorization.policy import (
    CaseScope,
    Permission,
    PrincipalContext,
    case_scope,
)
from finagent.services.case_service.queries import scope_filter

RECENT_SECONDS = 300
SAFE_CODE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")


def safe_code(value: str | None) -> str:
    return value if value and SAFE_CODE.fullmatch(value) else "OTHER"


def ratio(numerator: int, denominator: int) -> dict[str, Any]:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "value": numerator / denominator if denominator else None,
    }


def connector_state(observed_at: datetime | None, outcome: str | None, now: datetime) -> str:
    if observed_at is None or observed_at > now:
        return "NO_OBSERVATIONS"
    if now - observed_at > timedelta(seconds=RECENT_SECONDS):
        return "STALE"
    return "LAST_CALL_SUCCEEDED" if outcome == "SUCCEEDED" else "LAST_CALL_FAILED"


def _counts(session: Session, statement: Any) -> dict[str, int]:
    result: dict[str, int] = {}
    for key, value in session.execute(statement):
        code = safe_code(str(key))
        result[code] = result.get(code, 0) + int(value)
    return result


def _count(session: Session, statement: Any) -> int:
    return int(session.scalar(statement) or 0)


def _duration(session: Session, statement: Any) -> dict[str, Any]:
    samples = statement.subquery()
    seconds = samples.c.seconds
    valid = case((seconds >= 0, seconds), else_=None)
    # A separate statement keeps every aggregate over the exact same sample cohort.
    row = session.execute(
        select(
            func.count(valid),
            func.avg(valid),
            func.min(valid),
            func.max(valid),
            func.count(case((seconds < 0, 1), else_=None)),
        ).select_from(samples)
    ).one()
    return {
        "sample_count": int(row[0]),
        "mean_seconds": round(float(row[1]), 3) if row[1] is not None else None,
        "min_seconds": round(float(row[2]), 3) if row[2] is not None else None,
        "max_seconds": round(float(row[3]), 3) if row[3] is not None else None,
        "invalid_negative_samples": int(row[4]),
    }


def _connector(session: Session, ctx: PrincipalContext, since: datetime, now: datetime) -> dict[str, Any]:
    filters = (
        ConnectorObservation.tenant_id == ctx.tenant_id,
        ConnectorObservation.created_at >= since,
        ConnectorObservation.created_at <= now,
        ConnectorObservation.source == "core-banking",
    )
    row = session.execute(
        select(
            func.count(),
            func.count(case((ConnectorObservation.outcome == "FAILED", 1), else_=None)),
            func.coalesce(
                func.sum(
                    case((ConnectorObservation.attempts > 1, ConnectorObservation.attempts - 1), else_=0)
                ),
                0,
            ),
            func.avg(ConnectorObservation.latency_ms),
        ).where(*filters)
    ).one()
    latest = session.scalar(
        select(ConnectorObservation)
        .where(*filters)
        .order_by(ConnectorObservation.created_at.desc(), ConnectorObservation.id.desc())
        .limit(1)
    )
    observed = latest.created_at if latest else None
    if observed is not None and observed.tzinfo is None:
        observed = observed.replace(tzinfo=UTC)
    return {
        "source": "core-banking",
        "state": connector_state(observed, latest.outcome if latest else None, now),
        "last_observed_at": observed.isoformat() if observed else None,
        "last_error_code": safe_code(latest.error_code) if latest and latest.error_code else None,
        "logical_calls": int(row[0]),
        "failed_calls": int(row[1]),
        "retry_attempts": int(row[2]),
        "mean_latency_ms": round(float(row[3]), 3) if row[3] is not None else None,
        "failure_rate": ratio(int(row[1]), int(row[0])),
        "recent_observation_seconds": RECENT_SECONDS,
    }


def dashboard(
    session: Session,
    ctx: PrincipalContext,
    *,
    days: int = 7,
    source_max_age_hours: float = 24,
    now: datetime | None = None,
) -> dict[str, Any]:
    operator = Permission.PLATFORM_OPERATE in ctx.permissions
    if not operator and Permission.CASE_READ not in ctx.permissions:
        raise AppError(ErrorCode.FORBIDDEN)
    if not 1 <= days <= 28:
        raise AppError(ErrorCode.SEMANTIC_VALIDATION_FAILED, "Reporting window must be 1 to 28 days.")
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        raise ValueError("Reporting clock must be timezone-aware.")
    since = now - timedelta(days=days)
    scoped = and_(Case.tenant_id == ctx.tenant_id, Case.created_at <= now)
    if not operator:
        scoped = and_(scoped, scope_filter(ctx))
    ids = select(Case.id).where(scoped)
    scope = (
        "tenant-metadata"
        if operator
        else ("tenant-cases" if case_scope(ctx) is CaseScope.TENANT else "assigned-cases")
    )
    out: dict[str, Any] = {
        "generated_at": now.isoformat(),
        "window_start": since.isoformat(),
        "window_days": days,
        "scope": scope,
        "can_reconcile": Permission.OPERATION_RECONCILE in ctx.permissions,
        "cases_by_status": _counts(
            session, select(Case.status, func.count()).where(scoped).group_by(Case.status)
        ),
        "cases_created": _count(session, select(func.count(Case.id)).where(scoped, Case.created_at >= since)),
    }
    # First completion per case avoids treating a reopened case as another new case.
    completed = (
        select(
            CaseStatusHistory.tenant_id,
            CaseStatusHistory.case_id,
            func.min(CaseStatusHistory.occurred_at).label("finished"),
        )
        .where(
            CaseStatusHistory.to_status == "COMPLETED",
            CaseStatusHistory.tenant_id == ctx.tenant_id,
            CaseStatusHistory.occurred_at <= now,
        )
        .group_by(CaseStatusHistory.tenant_id, CaseStatusHistory.case_id)
        .subquery()
    )
    out["time_to_first_completion"] = _duration(
        session,
        select((extract("epoch", completed.c.finished) - extract("epoch", Case.created_at)).label("seconds"))
        .select_from(Case)
        .join(completed, and_(completed.c.case_id == Case.id, completed.c.tenant_id == Case.tenant_id))
        .where(scoped, completed.c.finished >= since),
    )
    out["review_turnaround"] = _duration(
        session,
        select(
            (
                extract("epoch", ApprovalDecision.decided_at) - extract("epoch", ApprovalRequest.created_at)
            ).label("seconds")
        )
        .select_from(ApprovalDecision)
        .join(
            ApprovalRequest,
            and_(
                ApprovalRequest.id == ApprovalDecision.request_id,
                ApprovalRequest.tenant_id == ApprovalDecision.tenant_id,
            ),
        )
        .where(
            ApprovalDecision.tenant_id == ctx.tenant_id,
            ApprovalRequest.case_id.in_(ids),
            ApprovalDecision.decided_at >= since,
            ApprovalDecision.decided_at <= now,
        ),
    )
    out["review_decisions"] = _counts(
        session,
        select(ApprovalDecision.decision, func.count())
        .join(
            ApprovalRequest,
            and_(
                ApprovalRequest.id == ApprovalDecision.request_id,
                ApprovalRequest.tenant_id == ApprovalDecision.tenant_id,
            ),
        )
        .where(
            ApprovalDecision.tenant_id == ctx.tenant_id,
            ApprovalRequest.case_id.in_(ids),
            ApprovalDecision.decided_at >= since,
            ApprovalDecision.decided_at <= now,
        )
        .group_by(ApprovalDecision.decision),
    )
    model_filters = [
        ModelRequest.tenant_id == ctx.tenant_id,
        ModelRequest.created_at >= since,
        ModelRequest.created_at <= now,
    ]
    if not operator:
        model_filters.append(ModelRequest.case_id.in_(ids))
    out["model_requests_by_status"] = _counts(
        session, select(ModelRequest.status, func.count()).where(*model_filters).group_by(ModelRequest.status)
    )
    out["model_requests_by_purpose"] = _counts(
        session,
        select(ModelRequest.purpose, func.count()).where(*model_filters).group_by(ModelRequest.purpose),
    )
    usage = session.execute(
        select(
            func.count(), func.coalesce(func.sum(ModelRequest.input_tokens + ModelRequest.output_tokens), 0)
        ).where(*model_filters)
    ).one()
    out["model_requests"], out["model_tokens"] = int(usage[0]), int(usage[1])
    out["estimated_model_cost_by_currency"] = {
        safe_code(currency): str(cost)
        for currency, cost in session.execute(
            select(ModelRequest.cost_currency, func.sum(ModelRequest.estimated_cost))
            .where(*model_filters)
            .group_by(ModelRequest.cost_currency)
        )
    }
    out["model_latency"] = _duration(
        session,
        select((ModelRequest.latency_ms / 1000.0).label("seconds")).where(
            *model_filters,
            ModelRequest.status.not_in(["BUDGET_DENIED", "POLICY_DENIED"]),
            func.coalesce(ModelRequest.error_code, "") != "CRITICAL_INCIDENT_ACTIVE",
        ),
    )
    slices = session.execute(
        select(
            func.coalesce(Alert.alert_type, "UNLINKED"),
            ModelRequest.config_version,
            ModelRequest.cost_currency,
            func.count(),
            func.sum(ModelRequest.input_tokens + ModelRequest.output_tokens),
            func.sum(ModelRequest.estimated_cost),
        )
        .select_from(ModelRequest)
        .outerjoin(Case, and_(Case.id == ModelRequest.case_id, Case.tenant_id == ModelRequest.tenant_id))
        .outerjoin(Alert, and_(Alert.id == Case.alert_id, Alert.tenant_id == Case.tenant_id))
        .where(*model_filters)
        .group_by(Alert.alert_type, ModelRequest.config_version, ModelRequest.cost_currency)
        .order_by(func.count().desc())
        .limit(101)
    ).all()
    out["model_slices"] = [
        {
            "case_type": safe_code(row[0]),
            "config_version": safe_code(row[1]),
            "currency": safe_code(row[2]),
            "requests": int(row[3]),
            "tokens": int(row[4]),
            "estimated_cost": str(row[5]),
        }
        for row in slices[:100]
    ]
    out["model_slices_truncated"] = len(slices) > 100
    a_filters = (
        AgentOutput.tenant_id == ctx.tenant_id,
        AgentOutput.case_id.in_(ids),
        AgentOutput.created_at >= since,
        AgentOutput.created_at <= now,
    )
    out["agent_outputs_by_status"] = _counts(
        session, select(AgentOutput.status, func.count()).where(*a_filters).group_by(AgentOutput.status)
    )
    rejected = AgentOutput.validation["rejected"]
    rejection_count = case(
        (func.jsonb_typeof(rejected) == "array", func.jsonb_array_length(rejected)), else_=0
    )
    out["outputs_with_rejections"] = _count(
        session, select(func.count(AgentOutput.id)).where(*a_filters, rejection_count > 0)
    )
    finding = AgentOutput.output["findings"]
    # Counting GAP findings is a proxy; a gap may appear in several capability outputs.
    out["outputs_with_gaps"] = _count(
        session,
        select(func.count(AgentOutput.id)).where(
            *a_filters, func.jsonb_path_exists(finding, cast('$[*] ? (@.claim_type == "GAP")', JSONPATH))
        ),
    )
    outputs = sum(out["agent_outputs_by_status"].values())
    out["output_rejection_rate"] = ratio(out["outputs_with_rejections"], outputs)
    out["output_gap_rate"] = ratio(out["outputs_with_gaps"], outputs)
    out["output_escalation_rate"] = ratio(out["agent_outputs_by_status"].get("ESCALATED", 0), outputs)
    tool_filters = [
        ToolInvocation.tenant_id == ctx.tenant_id,
        ToolInvocation.created_at >= since,
        ToolInvocation.created_at <= now,
    ]
    if not operator:
        tool_filters.append(ToolInvocation.case_id.in_(ids))
    out["tool_outcomes"] = _counts(
        session,
        select(ToolInvocation.outcome, func.count()).where(*tool_filters).group_by(ToolInvocation.outcome),
    )
    out["analyst_edit_origin_versions"] = _count(
        session,
        select(func.count(ReportVersion.id))
        .join(Report, and_(Report.id == ReportVersion.report_id, Report.tenant_id == ReportVersion.tenant_id))
        .where(
            ReportVersion.tenant_id == ctx.tenant_id,
            Report.case_id.in_(ids),
            ReportVersion.origin == "ANALYST_EDIT",
            ReportVersion.created_at >= since,
            ReportVersion.created_at <= now,
        ),
    )
    freshness = case(
        (EvidenceItem.as_of.is_(None), "UNKNOWN"),
        (EvidenceItem.as_of > now, "UNKNOWN"),
        (EvidenceItem.freshness == "UNKNOWN", "UNKNOWN"),
        (EvidenceItem.freshness == "STALE", "STALE"),
        (EvidenceItem.as_of < now - timedelta(hours=source_max_age_hours), "STALE"),
        else_="FRESH",
    )
    out["current_evidence_freshness"] = _counts(
        session,
        select(freshness, func.count())
        .where(
            EvidenceItem.tenant_id == ctx.tenant_id,
            EvidenceItem.case_id.in_(ids),
            EvidenceItem.status == "CURRENT",
            EvidenceItem.retrieved_at <= now,
        )
        .group_by(freshness),
    )
    run_filters = (WorkflowRun.tenant_id == ctx.tenant_id, WorkflowRun.case_id.in_(ids))
    out["runs_by_status"] = _counts(
        session,
        select(WorkflowRun.status, func.count())
        .where(*run_filters, WorkflowRun.created_at <= now)
        .group_by(WorkflowRun.status),
    )
    out["run_recovery_transitions"] = _count(
        session,
        select(func.count(WorkflowRunHistory.id))
        .join(
            WorkflowRun,
            and_(
                WorkflowRun.id == WorkflowRunHistory.run_id,
                WorkflowRun.tenant_id == WorkflowRunHistory.tenant_id,
            ),
        )
        .where(
            *run_filters,
            WorkflowRunHistory.to_status.in_(["RETRYING", "QUEUED"]),
            WorkflowRunHistory.from_status.is_not(None),
            WorkflowRunHistory.from_status != "CREATED",
            WorkflowRunHistory.occurred_at >= since,
            WorkflowRunHistory.occurred_at <= now,
        ),
    )
    external_filters = [
        ExternalOperation.tenant_id == ctx.tenant_id,
        ExternalOperation.updated_at >= since,
        ExternalOperation.updated_at <= now,
    ]
    if not operator:
        external_filters.append(ExternalOperation.case_id.in_(ids))
    out["external_operations"] = _counts(
        session,
        select(ExternalOperation.status, func.count())
        .where(*external_filters)
        .group_by(ExternalOperation.status),
    )
    runs = session.scalars(
        select(WorkflowRun)
        .options(
            load_only(
                WorkflowRun.id,
                WorkflowRun.case_id,
                WorkflowRun.graph_version,
                WorkflowRun.status,
                WorkflowRun.started_at,
                WorkflowRun.finished_at,
                WorkflowRun.attempt,
                WorkflowRun.last_error_code,
            )
        )
        .where(*run_filters, WorkflowRun.created_at <= now)
        .order_by(WorkflowRun.updated_at.desc(), WorkflowRun.id)
        .limit(21)
    ).all()
    selected = runs[:20]
    run_ids = [run.id for run in selected]
    costs: dict[Any, dict[str, str]] = {}
    if run_ids:
        for run_id, currency, amount in session.execute(
            select(ModelRequest.run_id, ModelRequest.cost_currency, func.sum(ModelRequest.estimated_cost))
            .where(
                ModelRequest.tenant_id == ctx.tenant_id,
                ModelRequest.run_id.in_(run_ids),
                ModelRequest.created_at <= now,
            )
            .group_by(ModelRequest.run_id, ModelRequest.cost_currency)
        ):
            costs.setdefault(run_id, {})[safe_code(currency)] = str(amount)
    links: set[Any] = set()
    if Permission.CASE_READ in ctx.permissions and run_ids:
        links = set(
            session.scalars(
                select(Case.id).where(
                    Case.tenant_id == ctx.tenant_id,
                    scope_filter(ctx),
                    Case.id.in_([r.case_id for r in selected]),
                )
            )
        )
    steps = (
        session.scalars(
            select(WorkflowStep)
            .options(
                load_only(
                    WorkflowStep.id,
                    WorkflowStep.run_id,
                    WorkflowStep.node,
                    WorkflowStep.status,
                    WorkflowStep.attempt,
                    WorkflowStep.started_at,
                    WorkflowStep.finished_at,
                )
            )
            .where(
                WorkflowStep.tenant_id == ctx.tenant_id,
                WorkflowStep.run_id.in_(run_ids),
                WorkflowStep.started_at <= now,
            )
            .order_by(WorkflowStep.started_at.desc(), WorkflowStep.id)
            .limit(201)
        ).all()
        if run_ids
        else []
    )

    def seconds(start: datetime | None, end: datetime | None) -> float | None:
        if start is None:
            return None
        if start.tzinfo is None:
            start = start.replace(tzinfo=UTC)
        if end is not None and end.tzinfo is None:
            end = end.replace(tzinfo=UTC)
        value = ((end or now) - start).total_seconds()
        return round(value, 3) if value >= 0 else None

    out["recent_runs"] = [
        {
            "run_id": str(run.id),
            "graph_version": safe_code(run.graph_version),
            "estimated_model_cost_by_currency": costs.get(run.id, {}),
            "status": run.status,
            "case_href": f"/cases/{run.case_id}" if run.case_id in links else None,
            "elapsed_seconds": seconds(run.started_at, run.finished_at),
            "attempt": run.attempt,
            "blocker_code": safe_code(run.last_error_code) if run.last_error_code else None,
            "steps": [
                {
                    "node": safe_code(step.node),
                    "status": step.status,
                    "attempt": step.attempt,
                    "elapsed_seconds": seconds(step.started_at, step.finished_at),
                }
                for step in steps[:200]
                if step.run_id == run.id
            ],
        }
        for run in selected
    ]
    out["recent_runs_truncated"], out["recent_steps_truncated"] = len(runs) > 20, len(steps) > 200
    out["connector_health"] = [_connector(session, ctx, since, now)] if operator else None
    out["unavailable"] = {
        "active_human_handling_time": "No active-work timer is recorded.",
        "tool_fees": "No monetary tool-fee ledger is recorded.",
        "reviewer_corrections": "No independently labelled correction events are recorded.",
        "policy_freshness": (
            "Policy applicability and review freshness need scoped evaluation; "
            "this report does not infer universal policy expiry."
        ),
    }
    return out
