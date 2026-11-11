"""Evaluation, configuration promotion, retention and legal-hold endpoints (prompt §22, §29; FR-039)."""

from __future__ import annotations

from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict, StringConstraints
from sqlalchemy import select, text

from finagent.agents.registry import CapabilityUnavailableError
from finagent.api.deps import AppSettings, Factory, Principal, charge
from finagent.core.errors import AppError, ErrorCode, not_found
from finagent.database.models import EvaluationRun
from finagent.database.session import transaction
from finagent.evaluation import runner as evaluation
from finagent.orchestration.services import build_provider
from finagent.security.authorization.policy import Permission, require
from finagent.services.governance_service import capability_incidents as incidents
from finagent.services.governance_service import config as config_service
from finagent.services.governance_service import promotion_readiness, retention
from finagent.services.governance_service.config import ChangeRequest
from finagent.services.governance_service.retention import HoldRequest, ReleaseRequest

governance = APIRouter(prefix="/api/v1")
EVALUATION_COST = 50


class EvaluationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    dataset: Annotated[str, StringConstraints(pattern=r"^[a-z0-9-]{3,100}$")]
    purpose: Literal["development", "validation", "release"] = "development"


@governance.get("/evaluations", tags=["evaluations"])
def list_evaluations(ctx: Principal, factory: Factory) -> dict[str, Any]:
    require(ctx, Permission.EVALUATION_RUN)
    with transaction(factory) as s:
        rows = s.scalars(
            select(EvaluationRun)
            .where(EvaluationRun.tenant_id == ctx.tenant_id)
            .order_by(EvaluationRun.created_at.desc())
            .limit(50)
        ).all()
        return {
            "items": [evaluation.view(r) for r in rows],
            "datasets": evaluation.list_datasets(),
            "thresholds": evaluation.THRESHOLDS,
        }


@governance.post("/evaluations", tags=["evaluations"], status_code=201)
def run_evaluation(
    body: EvaluationRequest,
    request: Request,
    ctx: Principal,
    factory: Factory,
    settings: AppSettings,
) -> dict[str, Any]:
    require(ctx, Permission.EVALUATION_RUN)
    charge(request, ctx, EVALUATION_COST)
    if body.dataset not in evaluation.list_datasets():
        raise AppError(ErrorCode.SCHEMA_VALIDATION_FAILED, "Unknown dataset.", {"field": "dataset"})
    live = build_provider(settings) if settings.model_provider != "mock" else None
    try:
        return evaluation.run(
            factory,
            settings,
            ctx,
            body.dataset,
            request.app.state.retriever,
            live_provider=live,
            purpose=body.purpose,
        )
    except CapabilityUnavailableError as exc:
        raise AppError(
            ErrorCode.SEMANTIC_VALIDATION_FAILED,
            f"Evaluation capability unavailable: {exc.reason}",
        ) from exc


@governance.get("/evaluations/{evaluation_id}", tags=["evaluations"])
def get_evaluation(evaluation_id: str, ctx: Principal, factory: Factory) -> dict[str, Any]:
    import uuid

    require(ctx, Permission.EVALUATION_RUN)
    try:
        eid = uuid.UUID(evaluation_id)
    except ValueError as exc:
        raise not_found() from exc
    with transaction(factory) as s:
        row = s.scalar(
            select(EvaluationRun).where(EvaluationRun.tenant_id == ctx.tenant_id, EvaluationRun.id == eid)
        )
        if row is None:
            raise not_found()
        return evaluation.view(row)


@governance.get("/admin/config-versions", tags=["admin"])
def list_config_versions(ctx: Principal, factory: Factory, settings: AppSettings) -> dict[str, Any]:
    with transaction(factory) as s:
        return config_service.list_versions(s, ctx, settings)


@governance.post("/admin/config-versions", tags=["admin"], status_code=201)
def register_config_version(ctx: Principal, factory: Factory, settings: AppSettings) -> dict[str, Any]:
    with transaction(factory) as s:
        return config_service.register_current(s, ctx, settings)


@governance.post("/admin/config-versions/{version_id}/promote", tags=["admin"])
def promote_config(version_id: str, body: ChangeRequest, ctx: Principal, factory: Factory) -> dict[str, Any]:
    with transaction(factory) as s:
        return config_service.promote(s, ctx, version_id, body)


@governance.post("/admin/config-versions/{version_id}/rollback", tags=["admin"])
def rollback_config(version_id: str, body: ChangeRequest, ctx: Principal, factory: Factory) -> dict[str, Any]:
    with transaction(factory) as s:
        return config_service.rollback(s, ctx, version_id, body)


@governance.get("/admin/retention-rules", tags=["admin"])
def retention_rules(ctx: Principal, factory: Factory) -> dict[str, Any]:
    with transaction(factory) as s:
        return retention.list_rules(s, ctx)


@governance.post("/admin/retention/evaluate", tags=["admin"])
def retention_evaluate(ctx: Principal, factory: Factory) -> dict[str, Any]:
    with transaction(factory) as s:
        return retention.evaluate(s, ctx)


@governance.post("/admin/retention/execute", tags=["admin"])
def retention_execute(
    request: Request,
    ctx: Principal,
    factory: Factory,
    settings: AppSettings,
) -> dict[str, Any]:
    with transaction(factory) as s:
        return retention.execute_document_content(
            s,
            ctx,
            request.app.state.document_ports.store,
            enabled=settings.retention_execution_enabled,
        )


@governance.get("/admin/legal-holds", tags=["admin"])
def legal_holds(ctx: Principal, factory: Factory) -> dict[str, Any]:
    with transaction(factory) as s:
        return retention.list_holds(s, ctx)


@governance.post("/admin/legal-holds", tags=["admin"], status_code=201)
def place_legal_hold(body: HoldRequest, ctx: Principal, factory: Factory) -> dict[str, Any]:
    with transaction(factory) as s:
        return retention.place_hold(s, ctx, body)


@governance.post("/admin/legal-holds/{hold_id}/release", tags=["admin"])
def release_legal_hold(
    hold_id: str, body: ReleaseRequest, ctx: Principal, factory: Factory
) -> dict[str, Any]:
    with transaction(factory) as s:
        return retention.release_hold(s, ctx, hold_id, body)


@governance.get("/admin/capability-incidents", tags=["admin"])
def list_capability_incidents(ctx: Principal, factory: Factory) -> dict[str, Any]:
    with transaction(factory) as session:
        return incidents.list_incidents(session, ctx)


@governance.post("/admin/capability-incidents", tags=["admin"], status_code=201)
def declare_capability_incident(
    body: incidents.IncidentRequest, ctx: Principal, factory: Factory
) -> dict[str, Any]:
    with transaction(factory) as session:
        return incidents.declare(session, ctx, body)


@governance.post("/admin/capability-incidents/{incident_id}/investigate", tags=["admin"])
def investigate_capability_incident(
    incident_id: UUID,
    body: incidents.InvestigationRequest,
    ctx: Principal,
    factory: Factory,
) -> dict[str, Any]:
    with transaction(factory) as session:
        return incidents.investigate(session, ctx, incident_id, body)


@governance.post("/admin/capability-incidents/{incident_id}/restore", tags=["admin"])
def restore_capability_incident(
    incident_id: UUID, body: incidents.RestoreRequest, ctx: Principal, factory: Factory
) -> dict[str, Any]:
    with transaction(factory) as session:
        return incidents.restore(session, ctx, incident_id, body)


@governance.get("/admin/capability-incidents/{incident_id}", tags=["admin"])
def get_capability_incident(incident_id: UUID, ctx: Principal, factory: Factory) -> dict[str, Any]:
    with transaction(factory) as session:
        return incidents.detail(session, ctx, incident_id)


@governance.post("/admin/capability-incidents/{incident_id}/retry-notification", tags=["admin"])
def retry_capability_incident_notification(
    incident_id: UUID, body: incidents.RestoreRequest, ctx: Principal, factory: Factory
) -> dict[str, Any]:
    with transaction(factory) as session:
        return incidents.requeue_notification(session, ctx, incident_id, body)


@governance.post(
    "/admin/retention/execute/evidence-and-snapshots",
    tags=["admin"],
)
def retention_execute_evidence_and_snapshots(
    ctx: Principal,
    factory: Factory,
    settings: AppSettings,
) -> dict[str, Any]:
    """Execute feature-gated evidence payload retention."""

    with transaction(factory) as s:
        return retention.execute_evidence_and_snapshots(
            s,
            ctx,
            enabled=settings.retention_execution_enabled,
        )


# BEGIN PROMOTION READINESS V1
@governance.get("/admin/config-versions/{version_id}/promotion-readiness", tags=["admin"])
def get_promotion_readiness(
    version_id: UUID,
    evaluation_id: UUID,
    ctx: Principal,
    factory: Factory,
    response: Response,
) -> dict[str, Any]:
    promotion_readiness.require_preview_access(ctx)
    with transaction(factory) as session:
        session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        result = promotion_readiness.get_readiness(session, ctx, version_id, evaluation_id)
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Pragma"] = "no-cache"
    return result


# END PROMOTION READINESS V1
