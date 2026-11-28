"""Release-1 REST endpoints implemented so far (SRS §12 canonical paths, ADR-0005).

Remaining endpoints from prompt §22 are added in later phases.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from finagent.api.deps import AppSettings, Factory, IdempotencyKey, IfMatch, Principal
from finagent.core.errors import AppError, ErrorCode, not_found
from finagent.database.session import transaction
from finagent.security.authorization.policy import Permission
from finagent.services.audit_service import queries as audit_queries
from finagent.services.case_service import commands as case_commands
from finagent.services.case_service import escalation, reopening
from finagent.services.case_service import queries as case_queries
from finagent.services.case_service.commands import AssignmentRequest, CasePatch
from finagent.services.case_service.contracts import AlertEnvelope
from finagent.services.case_service.intake import ingest_alert

health = APIRouter(tags=["health"])
api = APIRouter(prefix="/api/v1")


@health.get("/health/live")
def live() -> dict[str, str]:
    return {"status": "ok"}


@health.get("/health/ready")
def ready(factory: Factory) -> dict[str, str]:
    try:
        with transaction(factory) as session:
            session.execute(text("SELECT 1"))
    except Exception as exc:  # readiness must not leak dependency details
        raise AppError(ErrorCode.DEPENDENCY_UNAVAILABLE, "A required dependency is unavailable.") from exc
    return {"status": "ready"}


@api.post("/alerts", tags=["alerts"], status_code=201)
def post_alert(
    envelope: AlertEnvelope, ctx: Principal, factory: Factory, key: IdempotencyKey, settings: AppSettings
) -> JSONResponse:
    with transaction(factory) as session:
        result = ingest_alert(session, ctx, envelope, key, settings.idempotency_retention_days)
    return JSONResponse(status_code=result.status_code, content=result.body)


@api.get("/cases", tags=["cases"])
def get_cases(
    ctx: Principal,
    factory: Factory,
    settings: AppSettings,
    status: str | None = None,
    priority: str | None = None,
    case_ref: str | None = None,
    cursor: str | None = None,
    limit: Annotated[int | None, Query(ge=1)] = None,
) -> dict[str, Any]:
    page = min(limit or settings.page_size_default, settings.page_size_max)
    with transaction(factory) as session:
        result = case_queries.list_cases(
            session, ctx, limit=page, status=status, priority=priority, case_ref=case_ref, cursor=cursor
        )
    if priority is not None or case_ref is not None:
        result["applied_filters"] = {"priority": priority, "case_ref": case_ref}
    return result


@api.get("/cases/{case_id}", tags=["cases"])
def get_case(case_id: str, ctx: Principal, factory: Factory, response: Response) -> dict[str, Any]:
    with transaction(factory) as session:
        detail = case_queries.get_case(session, ctx, case_id)
    if detail is None:  # audit of the denied/absent read is already committed
        raise not_found()
    # UI hints come from the authenticated principal, never a submitted role or subject.
    can_reopen = detail["status"] == "COMPLETED" and Permission.CASE_ASSIGN in ctx.permissions
    detail["can_reopen"] = can_reopen
    detail["can_reassign"] = (
        detail["status"] not in {"COMPLETED", "CANCELLED"} and Permission.CASE_ASSIGN in ctx.permissions
    )
    detail["reassignment_requires_mfa"] = (
        detail["can_reassign"] and Permission.CASE_ASSIGN in ctx.step_up and not ctx.mfa_satisfied
    )
    detail["can_change_block_status"] = (
        detail["status"] in {"INVESTIGATING", "BLOCKED"} and Permission.CASE_UPDATE in ctx.permissions
    )
    detail["block_status_requires_mfa"] = (
        detail["can_change_block_status"] and Permission.CASE_UPDATE in ctx.step_up and not ctx.mfa_satisfied
    )
    detail["reopening_requires_mfa"] = (
        can_reopen and Permission.CASE_ASSIGN in ctx.step_up and not ctx.mfa_satisfied
    )
    response.headers["ETag"] = f'"{detail["record_version"]}"'
    return detail


@api.patch("/cases/{case_id}", tags=["cases"])
def patch_case(
    case_id: str, body: CasePatch, ctx: Principal, factory: Factory, if_match: IfMatch, response: Response
) -> dict[str, Any]:
    """Guarded update: send the record_version you read (If-Match or body). Stale -> 409."""
    with transaction(factory) as session:
        result = case_commands.update_case(session, ctx, case_id, body, if_match)
    response.headers["ETag"] = f'"{result["record_version"]}"'
    return result


@api.post("/cases/{case_id}/reopen", tags=["cases"])
def reopen_case(
    case_id: str,
    body: reopening.ReopenRequest,
    ctx: Principal,
    factory: Factory,
    if_match: IfMatch,
    response: Response,
) -> dict[str, Any]:
    """Supervisor reopening with a reason and optimistic version precondition (FR-006)."""
    with transaction(factory) as session:
        result = reopening.reopen_case(session, ctx, case_id, body, if_match)
    response.headers["ETag"] = f'"{result["record_version"]}"'
    return result


class AssigneeSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["ANALYST", "REVIEWER"]
    q: str = Field(min_length=2, max_length=100)


class AssigneeSearchItem(BaseModel):
    id: str
    display_name: str


class AssigneeSearchResponse(BaseModel):
    items: list[AssigneeSearchItem]


@api.post("/cases/{case_id}/eligible-assignees", tags=["cases"])
def get_eligible_assignees(
    case_id: str,
    body: AssigneeSearchRequest,
    ctx: Principal,
    factory: Factory,
) -> AssigneeSearchResponse:
    term = body.q.strip()
    if len(term) < 2:
        raise AppError(ErrorCode.SCHEMA_VALIDATION_FAILED, "Enter at least two characters to search.")
    with transaction(factory) as session:
        people = case_commands.search_assignees(session, ctx, case_id, body.role, term)
        return AssigneeSearchResponse(items=[AssigneeSearchItem(**person) for person in people])


@api.post("/cases/{case_id}/assignment", tags=["cases"])
def post_assignment(
    case_id: str, body: AssignmentRequest, ctx: Principal, factory: Factory, if_match: IfMatch
) -> dict[str, Any]:
    """Supervisor reassignment with a mandatory reason (FR-009)."""
    with transaction(factory) as session:
        return case_commands.reassign(session, ctx, case_id, body, if_match)


@api.get("/escalations", tags=["cases"])
def get_escalations(
    ctx: Principal,
    factory: Factory,
    settings: AppSettings,
    include_resolved: bool = False,
    limit: Annotated[int | None, Query(ge=1)] = None,
) -> dict[str, Any]:
    page = min(limit or settings.page_size_default, settings.page_size_max)
    with transaction(factory) as session:
        return escalation.list_escalations(session, ctx, limit=page, include_resolved=include_resolved)


@api.get("/audit-events", tags=["audit"])
def get_audit_events(
    ctx: Principal,
    factory: Factory,
    settings: AppSettings,
    after_seq: Annotated[int, Query(ge=0)] = 0,
    case_id: str | None = None,
    limit: Annotated[int | None, Query(ge=1)] = None,
) -> dict[str, Any]:
    page = min(limit or settings.page_size_default, settings.page_size_max)
    with transaction(factory) as session:
        return audit_queries.list_events(session, ctx, limit=page, after_seq=after_seq, case_id=case_id)
