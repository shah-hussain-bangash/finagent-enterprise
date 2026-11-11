"""Structured investigation reports: AI drafts, analyst edits, revision history (FR-026, FR-027, AER-016).

Every change is a NEW immutable ReportVersion with author, time, base version, snapshot binding and
lineage; claims are stored per version with their evidence links and support status. An analyst
edit after submission or approval is a material change (FR-006): affected approvals are invalidated.
Edited text is re-validated: a number that is not backed by a validated claim or the case record is
flagged and blocks submission.
"""

from __future__ import annotations

import difflib
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from finagent.agents.validation import numbers, numbers_in
from finagent.core.correlation import get_correlation_id
from finagent.core.errors import AppError, ErrorCode, not_found
from finagent.database.models import (
    Alert,
    Case,
    EvidenceSnapshot,
    OutboxEvent,
    Report,
    ReportClaim,
    ReportVersion,
)
from finagent.domain.fingerprint import fingerprint
from finagent.domain.states import CASE_TERMINAL, CaseStatus
from finagent.security.authorization.policy import Permission, PrincipalContext, require
from finagent.services.audit_service import writer as audit
from finagent.services.case_service.queries import load_scoped_case
from finagent.services.review_service.invalidation import material_change

REQUIRED_SECTIONS = ("alert_context", "evidence_scope")


def _case_numbers(alert: Alert) -> set[Decimal]:
    return numbers_in(
        {
            "a": alert.source_alert_id,
            "s": list(alert.subject_refs),
            "w": [alert.window_start.date().isoformat(), alert.window_end.date().isoformat()],
        }
    )


def validate_content(content: dict[str, Any], alert: Alert, snapshot_version: int) -> dict[str, Any]:
    """Deterministic checks on a report version's content (used on every save and at submission)."""
    claims = content.get("findings", [])
    allowed = _case_numbers(alert) | {Decimal(snapshot_version)}
    for c in claims:
        allowed |= numbers(c.get("claim", ""))
    allowed |= numbers_in(content.get("limitations", {}))
    problems = []
    ids = {c["claim_id"] for c in claims}
    present = {s["section_id"] for s in content.get("sections", [])}
    for req in REQUIRED_SECTIONS:
        if req not in present:
            problems.append({"section": req, "code": "REQUIRED_SECTION_MISSING"})
    for s in content.get("sections", []):
        if not str(s.get("text", "")).strip():
            problems.append({"section": s["section_id"], "code": "EMPTY_SECTION"})
        if set(s.get("claim_ids", [])) - ids:
            problems.append({"section": s["section_id"], "code": "UNKNOWN_CLAIM_REFERENCE"})
        stray = numbers(str(s.get("text", ""))) - allowed
        if stray:
            problems.append({"section": s["section_id"], "code": "NUMBER_NOT_BACKED_BY_VALIDATED_CLAIM"})
    for c in claims:
        if c.get("claim_type") != "GAP" and not c.get("evidence_refs"):
            problems.append({"claim": c["claim_id"], "code": "CLAIM_WITHOUT_EVIDENCE"})
    if not claims:
        problems.append({"code": "NO_VALIDATED_CLAIMS"})
    return {"valid": not problems, "problems": problems, "validator": "report-validator-1"}


def _claim_rows(tenant_id: uuid.UUID, version_id: uuid.UUID, content: dict[str, Any]) -> list[ReportClaim]:
    section_of: dict[str, str] = {}
    for s in content.get("sections", []):
        for cid in s.get("claim_ids", []):
            section_of.setdefault(cid, s["section_id"])
    rows = []
    for c in content.get("findings", []):
        refs = list(c.get("evidence_refs", []))
        rows.append(
            ReportClaim(
                tenant_id=tenant_id,
                report_version_id=version_id,
                claim_key=c["claim_id"][:64],
                section=section_of.get(c["claim_id"], "unassigned")[:64],
                claim_type=c["claim_type"],
                text=c["claim"],
                support_status=c["support_status"],
                evidence_refs=[{"ref": r} for r in refs if not r.startswith("cite:")],
                citation_refs=[{"ref": r} for r in refs if r.startswith("cite:")],
            )
        )
    return rows


def _event(session: Session, report: Report, version: ReportVersion) -> None:
    session.add(
        OutboxEvent(
            tenant_id=report.tenant_id,
            event_type="report.version_created.v1",
            schema_version="1",
            resource_id=str(version.id),
            correlation_id=get_correlation_id(),
            payload={
                "report_id": str(report.id),
                "case_id": str(report.case_id),
                "version_no": version.version_no,
            },
        )
    )


def create_ai_draft(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    case_id: uuid.UUID,
    snapshot_id: uuid.UUID,
    snapshot_version: int,
    content: dict[str, Any],
    lineage: dict[str, Any],
) -> ReportVersion:
    """AG-REPORT output -> new version. A new AI draft never marks itself approved (AER-016)."""
    case = session.scalar(
        select(Case).where(Case.tenant_id == tenant_id, Case.id == case_id).with_for_update()
    )
    alert = (
        session.scalar(select(Alert).where(Alert.tenant_id == tenant_id, Alert.id == case.alert_id))
        if case
        else None
    )
    if case is None or alert is None:
        raise not_found()
    report = session.scalar(
        select(Report).where(Report.tenant_id == tenant_id, Report.case_id == case_id).with_for_update()
    )
    if report is None:
        report = Report(tenant_id=tenant_id, case_id=case_id, status="DRAFT", current_version_no=0)
        session.add(report)
        session.flush()
    was_reviewed = report.status in ("SUBMITTED", "APPROVED")
    report.current_version_no += 1
    report.record_version += 1
    report.updated_at = datetime.now(UTC)
    version = ReportVersion(
        tenant_id=tenant_id,
        report_id=report.id,
        version_no=report.current_version_no,
        snapshot_id=snapshot_id,
        content=content,
        content_hash=fingerprint(content),
        origin="AI_DRAFT",
        author_principal_id=None,
        author_label="capability:AG-REPORT (draft, not approved)",
        base_version_no=report.current_version_no - 1 or None,
        lineage=lineage,
        validation=validate_content(content, alert, snapshot_version),
    )
    session.add(version)
    session.flush()
    session.add_all(_claim_rows(tenant_id, version.id, content))
    audit.record(
        session,
        tenant_id=tenant_id,
        action="REPORT_VERSION_CREATED",
        object_type="report_version",
        object_id=str(version.id),
        outcome="SUCCESS",
        actor_principal_id=None,
        actor_kind="SYSTEM",
        case_id=case_id,
        details={
            "version_no": version.version_no,
            "origin": "AI_DRAFT",
            "snapshot_id": str(snapshot_id),
            "valid": version.validation["valid"],
        },
    )
    _event(session, report, version)
    if was_reviewed:
        material_change(
            session,
            tenant_id=tenant_id,
            case_id=case_id,
            kind="REPORT_REDRAFTED_AFTER_SUBMISSION",
            actor_principal_id=None,
            actor_kind="SYSTEM",
            refs={"report_version_id": str(version.id)},
        )
    session.flush()
    return version


class SectionEdit(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    section_id: str = Field(pattern=r"^[a-z_]{3,40}$")
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=6000)]


class ReportEdit(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    base_version_no: int = Field(ge=1)
    sections: list[SectionEdit] = Field(min_length=1, max_length=8)
    edit_note: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=500)]


def load_report(
    session: Session, ctx: PrincipalContext, raw_case_id: str, *, for_update: bool = False
) -> tuple[Case, Report]:
    case = load_scoped_case(session, ctx, raw_case_id, for_update=for_update)
    if case is None:
        raise not_found()
    stmt = select(Report).where(Report.tenant_id == ctx.tenant_id, Report.case_id == case.id)
    report = session.scalar(stmt.with_for_update() if for_update else stmt)
    if report is None:
        raise not_found()
    return case, report


def current_version(session: Session, report: Report) -> ReportVersion:
    v = session.scalar(
        select(ReportVersion).where(
            ReportVersion.tenant_id == report.tenant_id,
            ReportVersion.report_id == report.id,
            ReportVersion.version_no == report.current_version_no,
        )
    )
    if v is None:
        raise not_found()
    return v


def edit_report(
    session: Session, ctx: PrincipalContext, raw_case_id: str, body: ReportEdit
) -> dict[str, Any]:
    require(ctx, Permission.REPORT_EDIT)
    case, report = load_report(session, ctx, raw_case_id, for_update=True)
    if CaseStatus(case.status) in CASE_TERMINAL or report.status == "FINALIZED":
        raise AppError(ErrorCode.CASE_INVALID_STATE, "A finalized report cannot be edited.")
    if body.base_version_no != report.current_version_no:
        raise AppError(
            ErrorCode.VERSION_CONFLICT,
            "The report changed since you opened it. Reload, review the differences and re-apply your edit.",
            {"current_version_no": report.current_version_no},
        )
    base = current_version(session, report)
    content = dict(base.content)
    sections = {s["section_id"]: dict(s) for s in content.get("sections", [])}
    for edit in body.sections:
        if edit.section_id in sections:
            sections[edit.section_id]["text"] = edit.text
        else:
            sections[edit.section_id] = {
                "section_id": edit.section_id,
                "title": edit.section_id.replace("_", " ").title(),
                "text": edit.text,
                "claim_ids": [],
            }
    content["sections"] = list(sections.values())
    alert = session.scalar(select(Alert).where(Alert.tenant_id == ctx.tenant_id, Alert.id == case.alert_id))
    snap = session.scalar(
        select(EvidenceSnapshot).where(
            EvidenceSnapshot.tenant_id == ctx.tenant_id, EvidenceSnapshot.id == base.snapshot_id
        )
    )
    was_reviewed = report.status in ("SUBMITTED", "APPROVED")
    report.current_version_no += 1
    report.record_version += 1
    report.updated_at = datetime.now(UTC)
    version = ReportVersion(
        tenant_id=ctx.tenant_id,
        report_id=report.id,
        version_no=report.current_version_no,
        snapshot_id=base.snapshot_id,
        content=content,
        content_hash=fingerprint(content),
        origin="ANALYST_EDIT",
        author_principal_id=ctx.principal_id,
        author_label=f"user:{ctx.principal_id}",
        base_version_no=base.version_no,
        edit_note=body.edit_note,
        lineage={**base.lineage, "edited_from_version": base.version_no},
        validation=validate_content(content, alert, snap.version_no if snap else 0)
        if alert
        else {"valid": False},
    )
    session.add(version)
    session.flush()
    session.add_all(_claim_rows(ctx.tenant_id, version.id, content))
    audit.record(
        session,
        tenant_id=ctx.tenant_id,
        action="REPORT_EDITED",
        object_type="report_version",
        object_id=str(version.id),
        outcome="SUCCESS",
        actor_principal_id=ctx.principal_id,
        actor_kind=ctx.kind.value,
        case_id=case.id,
        details={
            "version_no": version.version_no,
            "base": base.version_no,
            "sections": [s.section_id for s in body.sections],
            "valid": version.validation.get("valid"),
        },
    )
    _event(session, report, version)
    if was_reviewed:
        material_change(
            session,
            tenant_id=ctx.tenant_id,
            case_id=case.id,
            kind="REPORT_EDITED_AFTER_SUBMISSION",
            actor_principal_id=ctx.principal_id,
            actor_kind=ctx.kind.value,
            refs={"report_version_id": str(version.id)},
        )
    session.flush()
    return version_view(session, report, version)


def version_view(
    session: Session, report: Report, v: ReportVersion, *, with_diff: bool = True
) -> dict[str, Any]:
    claims = session.scalars(
        select(ReportClaim)
        .where(ReportClaim.tenant_id == v.tenant_id, ReportClaim.report_version_id == v.id)
        .order_by(ReportClaim.section, ReportClaim.claim_key)
    ).all()
    out: dict[str, Any] = {
        "report_id": str(report.id),
        "case_id": str(report.case_id),
        "report_status": report.status,
        "current_version_no": report.current_version_no,
        "version_id": str(v.id),
        "version_no": v.version_no,
        "snapshot_id": str(v.snapshot_id),
        "origin": v.origin,
        "author_label": v.author_label,
        "author_principal_id": str(v.author_principal_id) if v.author_principal_id else None,
        "base_version_no": v.base_version_no,
        "edit_note": v.edit_note,
        "created_at": v.created_at.isoformat(),
        "content_hash": v.content_hash,
        "sections": v.content.get("sections", []),
        "limitations": v.content.get("limitations", {}),
        "claims": [
            {
                "claim_id": c.claim_key,
                "section": c.section,
                "claim_type": c.claim_type,
                "text": c.text,
                "support_status": c.support_status,
                "evidence_refs": [r["ref"] for r in c.evidence_refs],
                "citation_refs": [r["ref"] for r in c.citation_refs],
            }
            for c in claims
        ],
        "validation": v.validation,
        "lineage": v.lineage,
        "watermark": None
        if report.status in ("APPROVED", "FINALIZED") and v.version_no == report.current_version_no
        else "DRAFT - NOT APPROVED",
    }
    if with_diff and v.base_version_no:
        prev = session.scalar(
            select(ReportVersion).where(
                ReportVersion.tenant_id == v.tenant_id,
                ReportVersion.report_id == report.id,
                ReportVersion.version_no == v.base_version_no,
            )
        )
        if prev is not None:
            out["diff_from_previous"] = section_diff(prev.content, v.content)
    return out


def section_diff(old: dict[str, Any], new: dict[str, Any]) -> list[dict[str, Any]]:
    """Reviewer-facing diff vs the previous version (prompt §19)."""
    o = {s["section_id"]: s.get("text", "") for s in old.get("sections", [])}
    n = {s["section_id"]: s.get("text", "") for s in new.get("sections", [])}
    out = []
    for sid in sorted(set(o) | set(n)):
        if o.get(sid) != n.get(sid):
            lines = list(
                difflib.unified_diff(
                    o.get(sid, "").splitlines(), n.get(sid, "").splitlines(), lineterm="", n=0
                )
            )
            out.append(
                {
                    "section_id": sid,
                    "change": "added" if sid not in o else "removed" if sid not in n else "modified",
                    "diff": lines[2:][:200],
                }
            )
    return out


def get_reports(session: Session, ctx: PrincipalContext, raw_case_id: str) -> dict[str, Any]:
    require(ctx, Permission.CASE_READ)
    case = load_scoped_case(session, ctx, raw_case_id)
    if case is None:
        raise not_found()
    report = session.scalar(
        select(Report).where(Report.tenant_id == ctx.tenant_id, Report.case_id == case.id)
    )
    if report is None:
        return {"report": None, "versions": []}
    versions = session.scalars(
        select(ReportVersion)
        .where(ReportVersion.tenant_id == ctx.tenant_id, ReportVersion.report_id == report.id)
        .order_by(ReportVersion.version_no.desc())
    ).all()
    return {
        "report": {
            "report_id": str(report.id),
            "status": report.status,
            "current_version_no": report.current_version_no,
            "record_version": report.record_version,
        },
        "versions": [
            {
                "version_no": v.version_no,
                "version_id": str(v.id),
                "origin": v.origin,
                "author_label": v.author_label,
                "created_at": v.created_at.isoformat(),
                "snapshot_id": str(v.snapshot_id),
                "valid": v.validation.get("valid"),
                "edit_note": v.edit_note,
            }
            for v in versions
        ],
    }


def get_version(session: Session, ctx: PrincipalContext, raw_case_id: str, version_no: int) -> dict[str, Any]:
    require(ctx, Permission.CASE_READ)
    case = load_scoped_case(session, ctx, raw_case_id)
    if case is None:
        raise not_found()
    row = session.execute(
        select(Report, ReportVersion)
        .join(
            ReportVersion,
            and_(ReportVersion.tenant_id == Report.tenant_id, ReportVersion.report_id == Report.id),
        )
        .where(
            Report.tenant_id == ctx.tenant_id,
            Report.case_id == case.id,
            ReportVersion.version_no == version_no,
        )
    ).first()
    if row is None:
        raise not_found()
    return version_view(session, row[0], row[1])
