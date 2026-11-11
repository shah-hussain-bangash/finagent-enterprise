"""Reports, review and export endpoints (SRS §12; prompt §22; FR-026..030)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import select

from finagent.agents.registry import registry_view
from finagent.api.deps import AppSettings, Factory, IdempotencyKey, Principal, charge
from finagent.core.errors import AppError, ErrorCode, not_found
from finagent.database.models import AgentOutput
from finagent.database.session import transaction
from finagent.security.authorization.policy import Permission, require
from finagent.services.case_service.queries import load_scoped_case
from finagent.services.review_service import exports, reports, review
from finagent.services.review_service.exports import ExportRequest
from finagent.services.review_service.reports import ReportEdit
from finagent.services.review_service.review import DecisionRequest, SubmitRequest, WithdrawRequest

review_api = APIRouter(prefix="/api/v1")
EXPORT_COST = 20


@review_api.get("/cases/{case_id}/reports", tags=["reports"])
def get_reports(case_id: str, ctx: Principal, factory: Factory) -> dict[str, Any]:
    with transaction(factory) as s:
        return reports.get_reports(s, ctx, case_id)


@review_api.get("/cases/{case_id}/reports/versions/{version_no}", tags=["reports"])
def get_report_version(case_id: str, version_no: int, ctx: Principal, factory: Factory) -> dict[str, Any]:
    with transaction(factory) as s:
        return reports.get_version(s, ctx, case_id, version_no)


@review_api.post("/cases/{case_id}/reports/edits", tags=["reports"], status_code=201)
def edit_report(case_id: str, body: ReportEdit, ctx: Principal, factory: Factory) -> dict[str, Any]:
    """New analyst revision based on `base_version_no` (stale base -> 409)."""
    with transaction(factory) as s:
        return reports.edit_report(s, ctx, case_id, body)


@review_api.post("/cases/{case_id}/reports/submit", tags=["reports"], status_code=201)
def submit_report(
    case_id: str,
    body: SubmitRequest,
    ctx: Principal,
    factory: Factory,
    key: IdempotencyKey,
    settings: AppSettings,
) -> JSONResponse:
    with transaction(factory) as s:
        status, out = review.submit(s, ctx, case_id, body, key, settings.idempotency_retention_days)
    return JSONResponse(status_code=status, content=out)


@review_api.post("/cases/{case_id}/reports/withdraw", tags=["reports"])
def withdraw_report(case_id: str, body: WithdrawRequest, ctx: Principal, factory: Factory) -> dict[str, Any]:
    with transaction(factory) as s:
        return review.withdraw(s, ctx, case_id, body)


@review_api.get("/approvals", tags=["approvals"])
def list_approvals(ctx: Principal, factory: Factory, status: str | None = None) -> dict[str, Any]:
    with transaction(factory) as s:
        return review.list_requests(s, ctx, status)


@review_api.get("/approvals/{approval_id}", tags=["approvals"])
def get_approval(approval_id: str, ctx: Principal, factory: Factory) -> dict[str, Any]:
    with transaction(factory) as s:
        return review.get_request(s, ctx, approval_id)


@review_api.post("/approvals/{approval_id}/decision", tags=["approvals"])
def decide(
    approval_id: str,
    body: DecisionRequest,
    ctx: Principal,
    factory: Factory,
    key: IdempotencyKey,
    settings: AppSettings,
) -> JSONResponse:
    """FR-028: APPROVE / RETURN_FOR_REWORK / REQUEST_INFORMATION (+ REJECT, DEC-11). MFA step-up."""
    with transaction(factory) as s:
        status, out = review.decide(s, ctx, approval_id, body, key, settings)
    return JSONResponse(status_code=status, content=out)


@review_api.post("/exports", tags=["exports"], status_code=201)
def create_export(body: ExportRequest, request: Request, ctx: Principal, factory: Factory) -> dict[str, Any]:
    charge(request, ctx, EXPORT_COST)
    with transaction(factory) as s:
        return exports.create_export(s, ctx, body, request.app.state.document_ports.store)


@review_api.get("/exports/{export_id}/content", tags=["exports"])
def export_content(export_id: str, request: Request, ctx: Principal, factory: Factory) -> Response:
    with transaction(factory) as s:
        data, media = exports.download(s, ctx, export_id, request.app.state.document_ports.store)
    return Response(
        content=data,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="report-export-{export_id[:8]}.md"'},
    )


@review_api.get("/agents", tags=["agents"])
def list_agents(ctx: Principal) -> dict[str, Any]:
    """Permitted registry view (prompt §22): metadata only, no prompts' text."""
    if not (
        {
            Permission.CASE_READ,
            Permission.PLATFORM_OPERATE,
            Permission.POLICY_PUBLISH,
            Permission.TENANT_ADMIN,
        }
        & ctx.permissions
    ):
        raise AppError(ErrorCode.FORBIDDEN)
    return {"items": registry_view()}


@review_api.get("/cases/{case_id}/agent-outputs", tags=["agents"])
def case_agent_outputs(case_id: str, ctx: Principal, factory: Factory) -> dict[str, Any]:
    require(ctx, Permission.CASE_READ)
    with transaction(factory) as s:
        case = load_scoped_case(s, ctx, case_id)
        if case is None:
            raise not_found()
        rows = s.scalars(
            select(AgentOutput)
            .where(AgentOutput.tenant_id == ctx.tenant_id, AgentOutput.case_id == case.id)
            .order_by(AgentOutput.created_at)
        ).all()
        return {
            "items": [
                {
                    "output_id": str(r.id),
                    "capability_id": r.capability_id,
                    "capability_version": r.capability_version,
                    "status": r.status,
                    "snapshot_id": str(r.snapshot_id),
                    "run_id": str(r.run_id) if r.run_id else None,
                    "findings": r.output.get("findings", []),
                    "limitations": r.output.get("limitations", {}),
                    "recommendations": r.output.get("recommendations", []),
                    "validation": r.validation,
                    "lineage": {k: v for k, v in r.lineage.items() if k != "usage"},
                    "usage": r.lineage.get("usage", {}),
                    "created_at": r.created_at.isoformat(),
                }
                for r in rows
            ]
        }
