"""Read-only promotion preflight; the existing promotion command remains authoritative."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from finagent.core.errors import AppError, ErrorCode, not_found
from finagent.database.models import ConfigurationVersion, EvaluationRun
from finagent.security.authorization.policy import Permission, PrincipalContext, PrincipalKind, require
from finagent.services.governance_service import config as config_service


def require_preview_access(ctx: PrincipalContext) -> None:
    if ctx.kind is not PrincipalKind.USER:
        raise AppError(ErrorCode.UNAUTHENTICATED)
    # Combined-role administrators must not bypass their admin MFA requirement.
    permission = (
        Permission.TENANT_ADMIN if Permission.TENANT_ADMIN in ctx.permissions else Permission.EVALUATION_RUN
    )
    require(ctx, permission)


def get_readiness(
    session: Session,
    ctx: PrincipalContext,
    version_id: UUID,
    evaluation_id: UUID,
) -> dict[str, Any]:
    require_preview_access(ctx)
    with session.no_autoflush:
        target = session.scalar(
            select(ConfigurationVersion).where(
                ConfigurationVersion.tenant_id == ctx.tenant_id,
                ConfigurationVersion.id == version_id,
            )
        )
        if target is None or target.tenant_id != ctx.tenant_id or target.id != version_id:
            raise not_found()
        evidence = session.scalar(
            select(EvaluationRun).where(
                EvaluationRun.tenant_id == ctx.tenant_id,
                EvaluationRun.id == evaluation_id,
            )
        )
        if evidence is None or evidence.tenant_id != ctx.tenant_id or evidence.id != evaluation_id:
            raise not_found()

        blockers: list[str] = []
        if target.status == "PROMOTED":
            blockers.append("CONFIGURATION_ALREADY_PROMOTED")
        elif target.status not in {"CANDIDATE", "SUPERSEDED", "ROLLED_BACK"}:
            blockers.append("CONFIGURATION_STATUS_UNRECOGNIZED")
        matches = evidence.config_hash == target.config_hash
        if not matches:
            blockers.append("CONFIGURATION_HASH_MISMATCH")
        if evidence.gate != "PASS_PROPOSED":
            blockers.append("EVALUATION_GATE_NOT_PASS_PROPOSED")

        release_check = "VALID"
        try:
            # Reuse the same dataset/version/catalog validator as promote().
            config_service.require_release_evidence(evidence)
        except AppError as exc:
            if exc.code is not ErrorCode.SEMANTIC_VALIDATION_FAILED:
                raise
            release_check = "REJECTED"
            blockers.append("RELEASE_EVIDENCE_REJECTED")

        technical_checks_satisfied = not blockers
        promotion_gate = "SATISFIED"
        try:
            require(ctx, Permission.TENANT_ADMIN)
        except AppError as exc:
            if exc.code not in {ErrorCode.FORBIDDEN, ErrorCode.MFA_REQUIRED}:
                raise
            promotion_gate = exc.code.value
            blockers.append("PROMOTION_" + promotion_gate)

        return {
            "schema_version": 1,
            "config_version_id": str(target.id),
            "evaluation_id": str(evidence.id),
            "configuration_status": target.status,
            "evaluation_gate": evidence.gate,
            "configuration_hash_matches": matches,
            "release_evidence_check": release_check,
            "technical_checks_satisfied": technical_checks_satisfied,
            "promotion_permission_gate": promotion_gate,
            "readiness": "BLOCKED" if blockers else "CHECKS_SATISFIED_PROPOSED_ONLY",
            "blocking_reasons": blockers,
            "checked_at_utc": datetime.now(UTC).isoformat(),
            "read_only": True,
            "promotion_executed": False,
            "execution_authorized": False,
            "change_reason": "NOT_EVALUATED",
            "production_acceptance": "NOT_ASSESSED",
            "notice": (
                "Advisory snapshot for the selected pair, not approval or deployment. "
                "PASS_PROPOSED remains a proposed-threshold result on synthetic data. "
                "The existing promotion command must re-check current state, evidence, "
                "permissions, MFA and the supplied change reason."
            ),
        }
