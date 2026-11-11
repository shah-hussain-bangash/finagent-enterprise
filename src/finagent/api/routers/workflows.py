"""Workflow and external-operation endpoints (SRS §12; prompt §22; FR-005, FR-032, FR-035, REC-003)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from finagent.api.deps import AppSettings, Factory, IdempotencyKey, Principal
from finagent.core.config import LOCAL_ONLY_ENVIRONMENTS
from finagent.database.session import transaction
from finagent.services.workflow_service import external_ops, runs
from finagent.services.workflow_service.external_ops import ReconcileRequest
from finagent.services.workflow_service.runs import CancelRequest, ResumeRequest, StartRequest

workflows = APIRouter(prefix="/api/v1", tags=["workflows"])


@workflows.post("/workflows", status_code=202)
def start_workflow(
    body: StartRequest, ctx: Principal, factory: Factory, key: IdempotencyKey, settings: AppSettings
) -> JSONResponse:
    """Accepts the request (202) and returns a status URL; a worker executes the run."""
    with transaction(factory) as session:
        status, result = runs.start_run(
            session,
            ctx,
            body,
            key,
            settings.idempotency_retention_days,
            settings.run_max_attempts,
            allow_local_only=settings.environment in LOCAL_ONLY_ENVIRONMENTS,
        )
    return JSONResponse(status_code=status, content=result, headers={"Location": result["status_url"]})


@workflows.get("/workflows/{run_id}")
def get_workflow(run_id: str, ctx: Principal, factory: Factory) -> dict[str, Any]:
    with transaction(factory) as session:
        return runs.get_run(session, ctx, run_id)


@workflows.post("/workflows/{run_id}/cancel", status_code=202)
def cancel_workflow(run_id: str, body: CancelRequest, ctx: Principal, factory: Factory) -> dict[str, Any]:
    """Stops new work; committed effects are preserved (FR-035). A RUNNING run stops at its next
    step boundary, so the response may still show RUNNING with cancel_requested=true."""
    with transaction(factory) as session:
        return runs.cancel_run(session, ctx, run_id, body)


@workflows.post("/workflows/{run_id}/resume", status_code=202)
def resume_workflow(run_id: str, body: ResumeRequest, ctx: Principal, factory: Factory) -> dict[str, Any]:
    """Deterministic human resume: evidence supplied, re-analysis, or retry after human review."""
    with transaction(factory) as session:
        return runs.human_resume(session, ctx, run_id, body)


@workflows.get("/operations/{operation_id}", tags=["operations"])
def get_operation(operation_id: str, ctx: Principal, factory: Factory) -> dict[str, Any]:
    with transaction(factory) as session:
        return external_ops.get_operation(session, ctx, operation_id)


@workflows.post("/operations/{operation_id}/reconcile", tags=["operations"])
def reconcile_operation(
    operation_id: str, body: ReconcileRequest, ctx: Principal, factory: Factory
) -> dict[str, Any]:
    """Operator records the verified outcome of an OUTCOME_UNCERTAIN operation (MST:REC-003)."""
    with transaction(factory) as session:
        return external_ops.reconcile(session, ctx, operation_id, body)
