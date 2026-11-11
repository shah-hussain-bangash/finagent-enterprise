"""Read-only recovery guidance derived from an already-scoped workflow view."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from finagent.core.errors import AppError, ErrorCode
from finagent.domain.states import RUN_TERMINAL, RunStatus
from finagent.security.authorization.policy import (
    Permission,
    PrincipalContext,
    PrincipalKind,
    require,
)
from finagent.services.workflow_service import runs


class _Blocker(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    code: str
    operation_id: UUID | None = None


class _Facts(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    run_id: UUID
    status: RunStatus
    attempt: int = Field(ge=0, strict=True)
    max_attempts: int = Field(ge=1, strict=True)
    cancel_requested: bool = Field(strict=True)
    blockers: list[_Blocker]


_GUIDANCE = {
    RunStatus.CREATED: "Run created; scheduling progress is not confirmed by this snapshot.",
    RunStatus.QUEUED: "Waiting for a worker; worker availability has not been measured here.",
    RunStatus.RUNNING: "Recorded as running; this is not a worker-heartbeat or progress check.",
    RunStatus.RETRYING: "Recorded as automatic retry; do not infer permission for a manual retry.",
    RunStatus.WAITING_EVIDENCE: "Supply or resolve the missing evidence before requesting resume.",
    RunStatus.WAITING_REVIEW: "A review-stage decision or authorized re-analysis requires further checks.",
    RunStatus.HUMAN_REVIEW: "Review the recovery reason before considering a manual retry.",
    RunStatus.OUTCOME_UNCERTAIN: "Reconcile the external outcome; do not blindly retry or cancel it away.",
    RunStatus.SUCCEEDED: "The technical run succeeded; case and report completion are not assessed here.",
    RunStatus.FAILED: "The technical run failed; this diagnostic does not authorize reopening or retry.",
    RunStatus.CANCELLED: "The run is cancelled; previously committed effects are not undone.",
}


def _hint(
    ctx: PrincipalContext,
    action: str,
    permission: Permission,
) -> dict[str, Any]:
    try:
        require(ctx, permission)
        gate = "SATISFIED"
    except AppError as exc:
        if exc.code not in {ErrorCode.FORBIDDEN, ErrorCode.MFA_REQUIRED}:
            raise
        gate = exc.code.value

    return {
        "action": action,
        "permission": permission.name,
        "permission_gate": gate,
        "business_preconditions": "NOT_EVALUATED",
        "execution_authorized": False,
    }


def summarize_run(
    view: dict[str, object],
    ctx: PrincipalContext,
) -> dict[str, Any]:
    """Internal formatter; callers must authorize and scope the run first."""

    facts = _Facts.model_validate(view)

    uncertain = sum(b.code == "OUTCOME_UNCERTAIN" and b.operation_id is not None for b in facts.blockers)

    reconcile = facts.status is RunStatus.OUTCOME_UNCERTAIN or uncertain > 0

    terminal = facts.status in RUN_TERMINAL

    hints: list[dict[str, Any]] = []

    guidance = _GUIDANCE[facts.status]
    attention = facts.status.value

    if reconcile:
        attention = "RECONCILIATION_REQUIRED"
        guidance = _GUIDANCE[RunStatus.OUTCOME_UNCERTAIN]

        hints.append(
            _hint(
                ctx,
                "RECONCILE_EXTERNAL_OPERATION",
                Permission.OPERATION_RECONCILE,
            )
        )

    elif terminal:
        attention = "TERMINAL"

    elif facts.cancel_requested:
        attention = "CANCELLATION_REQUESTED"
        guidance = "Cancellation was requested; do not infer that in-flight work has already stopped."

    else:
        if facts.status is RunStatus.WAITING_EVIDENCE:
            hints.append(
                _hint(
                    ctx,
                    "EVIDENCE_SUPPLIED",
                    Permission.WORKFLOW_START,
                )
            )

        elif facts.status is RunStatus.WAITING_REVIEW:
            hints.append(
                _hint(
                    ctx,
                    "REANALYZE",
                    Permission.WORKFLOW_START,
                )
            )

        elif facts.status is RunStatus.HUMAN_REVIEW:
            permission = (
                Permission.OPERATION_RECONCILE
                if Permission.OPERATION_RECONCILE in ctx.permissions
                else Permission.WORKFLOW_START
            )

            hints.append(
                _hint(
                    ctx,
                    "RETRY",
                    permission,
                )
            )

        hints.append(
            _hint(
                ctx,
                "CANCEL",
                Permission.WORKFLOW_CANCEL,
            )
        )

    return {
        "schema_version": 1,
        "run_id": str(facts.run_id),
        "generated_at": datetime.now(UTC).isoformat(),
        "status": facts.status.value,
        "terminal": terminal,
        "attention": attention,
        "guidance": guidance,
        "cancel_requested": facts.cancel_requested,
        "attempts": {
            "recorded": facts.attempt,
            "configured_max": facts.max_attempts,
            "at_or_above_configured_max": facts.attempt >= facts.max_attempts,
        },
        "uncertain_operation_count": uncertain,
        "recovery_hints": hints,
        "read_only": True,
        "resource_authorization_required": True,
        "worker_liveness": "NOT_ASSESSED",
        "case_completion": "NOT_ASSESSED",
        "notice": (
            "Advice only, not an authorization decision. Existing commands "
            "re-check permissions, MFA, row scope, current state, cancellation "
            "and required reasons. Re-analysis also checks case state and "
            "pending/approved reviews. Counts do not grant a retry or reset "
            "any budget."
        ),
    }


def get_diagnostics(
    session: Session,
    ctx: PrincipalContext,
    run_id: str,
) -> dict[str, Any]:
    """Preserve existing run visibility; emit only whitelisted operational facts."""

    if ctx.kind is not PrincipalKind.USER:
        raise AppError(ErrorCode.UNAUTHENTICATED)

    permission = (
        Permission.CASE_READ if Permission.CASE_READ in ctx.permissions else Permission.PLATFORM_OPERATE
    )

    require(ctx, permission)

    with session.no_autoflush:
        view = runs.get_run(session, ctx, run_id)

    return summarize_run(view, ctx)
