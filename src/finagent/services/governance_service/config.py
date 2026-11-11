"""Configuration versions: register, promote behind an evaluation gate, roll back (AER-031..034).

The configuration hash covers everything that changes AI behaviour: capability versions, prompt hashes,
limits and statuses, provider and model alias, retrieval backend, calculation/checklist/indicator rule
versions, validator and threshold versions. Promotion requires an evaluation of the SAME hash whose
gate is PASS_PROPOSED; rollback re-promotes an earlier version. Every change keeps lineage and is
audited. When `require_promoted_configuration` is on (mandatory outside local/test), the model gateway
refuses to run an unpromoted configuration.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, StringConstraints
from sqlalchemy import select
from sqlalchemy.orm import Session

from finagent.agents.prep import CHECKLIST_VERSION, INDICATOR_VERSION
from finagent.agents.registry import CAPABILITIES, CONFIG_VERSION
from finagent.core.config import Settings
from finagent.core.errors import AppError, ErrorCode, not_found
from finagent.database.models import ConfigurationVersion, EvaluationRun
from finagent.domain.aml_calculations import RULE_VERSION
from finagent.domain.fingerprint import fingerprint
from finagent.evaluation.dataset_control import DatasetControlError, read_dataset
from finagent.security.authorization.policy import Permission, PrincipalContext, require
from finagent.services.audit_service import writer as audit

_EVALUATION_DATASETS = Path(__file__).resolve().parents[2] / "evaluation" / "datasets"

_THRESHOLDS = json.loads(
    (Path(__file__).resolve().parents[2] / "evaluation" / "thresholds.json").read_text(encoding="utf-8")
)


def current_configuration(settings: Settings) -> dict[str, Any]:
    content = {
        "config_version": CONFIG_VERSION,
        "capabilities": {
            c.capability_id: {
                "version": c.version,
                "status": c.status,
                "prompt_version": c.prompt_version,
                "prompt_hash": c.prompt_hash,
                "model_alias": c.model_alias,
                "limits": [c.max_model_calls, c.token_budget, str(c.cost_budget), c.timeout_s],
            }
            for c in CAPABILITIES.values()
        },
        "model_provider": settings.model_provider,
        "model_deployment": settings.azure_openai_deployment
        if settings.model_provider == "azure_openai"
        else "mock",
        "retrieval_backend": settings.retrieval_backend,
        "calculation_rules": RULE_VERSION,
        "checklist_version": CHECKLIST_VERSION,
        "indicator_version": INDICATOR_VERSION,
        "validator": "deterministic-validator-1",
        "thresholds_version": _THRESHOLDS["version"],
        "aml_threshold": [
            settings.aml_threshold_amount,
            settings.aml_threshold_currency,
            settings.aml_threshold_band_pct,
        ],
    }
    return {"config_hash": fingerprint(content), "content": content}


def is_promoted(session: Session, tenant_id: uuid.UUID, config_hash: str) -> bool:
    evidence = session.scalar(
        select(EvaluationRun)
        .join(ConfigurationVersion, ConfigurationVersion.evaluation_id == EvaluationRun.id)
        .where(
            ConfigurationVersion.tenant_id == tenant_id,
            EvaluationRun.tenant_id == tenant_id,
            ConfigurationVersion.config_hash == config_hash,
            EvaluationRun.config_hash == config_hash,
            ConfigurationVersion.status == "PROMOTED",
            EvaluationRun.gate == "PASS_PROPOSED",
        )
    )
    if evidence is None:
        return False
    try:
        require_release_evidence(evidence)
    except AppError:
        return False
    return True


def _view(v: ConfigurationVersion) -> dict[str, Any]:
    return {
        "config_version_id": str(v.id),
        "config_hash": v.config_hash,
        "status": v.status,
        "evaluation_id": str(v.evaluation_id) if v.evaluation_id else None,
        "previous_promoted_id": str(v.previous_promoted_id) if v.previous_promoted_id else None,
        "change_reason": v.change_reason,
        "created_at": v.created_at.isoformat(),
        "updated_at": v.updated_at.isoformat(),
        "content": v.content,
    }


def list_versions(session: Session, ctx: PrincipalContext, settings: Settings) -> dict[str, Any]:
    if not ({Permission.TENANT_ADMIN, Permission.EVALUATION_RUN} & ctx.permissions):
        raise AppError(ErrorCode.FORBIDDEN)
    rows = session.scalars(
        select(ConfigurationVersion)
        .where(ConfigurationVersion.tenant_id == ctx.tenant_id)
        .order_by(ConfigurationVersion.created_at.desc())
    ).all()
    current = current_configuration(settings)
    return {
        "current_config_hash": current["config_hash"],
        "current_is_promoted": is_promoted(session, ctx.tenant_id, current["config_hash"]),
        "require_promoted_configuration": settings.require_promoted_configuration,
        "items": [_view(v) for v in rows],
    }


def register_current(session: Session, ctx: PrincipalContext, settings: Settings) -> dict[str, Any]:
    require(ctx, Permission.TENANT_ADMIN)
    current = current_configuration(settings)
    existing = session.scalar(
        select(ConfigurationVersion).where(
            ConfigurationVersion.tenant_id == ctx.tenant_id,
            ConfigurationVersion.config_hash == current["config_hash"],
        )
    )
    if existing is not None:
        return _view(existing)
    row = ConfigurationVersion(
        tenant_id=ctx.tenant_id,
        config_hash=current["config_hash"],
        content=current["content"],
        status="CANDIDATE",
        changed_by=ctx.principal_id,
        change_reason="registered",
    )
    session.add(row)
    session.flush()
    audit.record(
        session,
        tenant_id=ctx.tenant_id,
        action="CONFIG_VERSION_REGISTERED",
        object_type="configuration_version",
        object_id=str(row.id),
        outcome="SUCCESS",
        actor_principal_id=ctx.principal_id,
        actor_kind=ctx.kind.value,
        details={"config_hash": row.config_hash},
    )
    return _view(row)


class ChangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=10, max_length=300)]
    evaluation_id: uuid.UUID | None = None


def _load(session: Session, ctx: PrincipalContext, raw_id: str) -> ConfigurationVersion:
    try:
        vid = uuid.UUID(raw_id)
    except ValueError as exc:
        raise not_found() from exc
    row = session.scalar(
        select(ConfigurationVersion)
        .where(ConfigurationVersion.tenant_id == ctx.tenant_id, ConfigurationVersion.id == vid)
        .with_for_update()
    )
    if row is None:
        raise not_found()
    return row


def _switch(
    session: Session,
    ctx: PrincipalContext,
    target: ConfigurationVersion,
    reason: str,
    action: str,
    previous_status: str,
) -> None:
    current = session.scalar(
        select(ConfigurationVersion)
        .where(ConfigurationVersion.tenant_id == ctx.tenant_id, ConfigurationVersion.status == "PROMOTED")
        .with_for_update()
    )
    now = datetime.now(UTC)
    if current is not None and current.id != target.id:
        current.status, current.updated_at = previous_status, now
        session.flush()  # release the one-promoted unique index
    target.previous_promoted_id = (
        current.id if current is not None and current.id != target.id else target.previous_promoted_id
    )
    target.status, target.changed_by, target.change_reason, target.updated_at = (
        "PROMOTED",
        ctx.principal_id,
        reason,
        now,
    )
    audit.record(
        session,
        tenant_id=ctx.tenant_id,
        action=action,
        object_type="configuration_version",
        object_id=str(target.id),
        outcome="SUCCESS",
        actor_principal_id=ctx.principal_id,
        actor_kind=ctx.kind.value,
        details={
            "config_hash": target.config_hash,
            "replaced": str(current.id) if current else None,
            "reason": reason,
        },
    )


def require_release_evidence(run: EvaluationRun) -> None:
    """Only current, uncontaminated locked-release evidence can authorize promotion."""
    message = (
        "Promotion requires uncontaminated locked release evidence; "
        "development and validation runs cannot authorize promotion."
    )
    context = run.results[0].get("evaluation_context", {}) if run.results else {}
    if context.get("evaluation_purpose") != "release":
        raise AppError(ErrorCode.SEMANTIC_VALIDATION_FAILED, message)
    try:
        data, control = read_dataset(_EVALUATION_DATASETS, run.dataset, purpose="release")
    except DatasetControlError as exc:
        raise AppError(ErrorCode.SEMANTIC_VALIDATION_FAILED, message) from exc
    expected = {**data, "_dataset_control": control}
    if (
        run.dataset_version != data["version"]
        or run.dataset_hash != fingerprint(expected)
        or context.get("dataset_control") != control
    ):
        raise AppError(
            ErrorCode.SEMANTIC_VALIDATION_FAILED,
            "Dataset or catalog changed after evaluation; new locked release evidence is required.",
        )


def promote(session: Session, ctx: PrincipalContext, raw_id: str, body: ChangeRequest) -> dict[str, Any]:
    require(ctx, Permission.TENANT_ADMIN)  # MFA step-up
    target = _load(session, ctx, raw_id)
    if target.status == "PROMOTED":
        raise AppError(ErrorCode.CASE_INVALID_STATE, "This configuration is already promoted.")
    if body.evaluation_id is None:
        raise AppError(
            ErrorCode.SEMANTIC_VALIDATION_FAILED,
            "Promotion requires an evaluation run of this configuration.",
        )
    run = session.scalar(
        select(EvaluationRun).where(
            EvaluationRun.tenant_id == ctx.tenant_id, EvaluationRun.id == body.evaluation_id
        )
    )
    if run is None or run.config_hash != target.config_hash:
        raise AppError(
            ErrorCode.SEMANTIC_VALIDATION_FAILED, "The evaluation must be of exactly this configuration."
        )
    if run.gate != "PASS_PROPOSED":
        raise AppError(
            ErrorCode.SEMANTIC_VALIDATION_FAILED,
            f"Evaluation gate is {run.gate}; promotion refused.",
            {"gate": run.gate},
        )
    require_release_evidence(run)
    target.evaluation_id = run.id
    _switch(session, ctx, target, body.reason, "CONFIG_VERSION_PROMOTED", "SUPERSEDED")
    session.flush()
    return _view(target)


def rollback(session: Session, ctx: PrincipalContext, raw_id: str, body: ChangeRequest) -> dict[str, Any]:
    """Re-promote an EARLIER version that was promoted before (lineage preserved)."""
    require(ctx, Permission.TENANT_ADMIN)
    target = _load(session, ctx, raw_id)
    if target.status not in ("SUPERSEDED", "ROLLED_BACK") or target.evaluation_id is None:
        raise AppError(
            ErrorCode.CASE_INVALID_STATE, "Only a previously promoted configuration can be restored."
        )
    evidence = session.scalar(
        select(EvaluationRun).where(
            EvaluationRun.tenant_id == ctx.tenant_id,
            EvaluationRun.id == target.evaluation_id,
        )
    )
    if evidence is None:
        raise AppError(ErrorCode.SEMANTIC_VALIDATION_FAILED, "Rollback requires locked release evidence.")
    require_release_evidence(evidence)
    _switch(session, ctx, target, body.reason, "CONFIG_VERSION_ROLLED_BACK", "ROLLED_BACK")
    session.flush()
    return _view(target)
