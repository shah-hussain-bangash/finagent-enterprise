"""Completed-case reopening preserves history and requires a fresh review (FR-006)."""

from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from typing import Any

import pytest
from sqlalchemy import select

from finagent.core.errors import AppError
from finagent.database.models import (
    ApprovalDecision,
    ApprovalRequest,
    Case,
    CaseStatusHistory,
    ExternalOperation,
    Finalization,
    OutboxEvent,
    Report,
    ReportClaim,
    ReportVersion,
    WorkflowRun,
)
from finagent.database.seeds import sid
from finagent.database.session import session_factory, transaction
from finagent.security.authentication.resolver import resolve_principal_by_id
from finagent.services.case_service import reopening
from tests.helpers import audit_actions, ingest, principal, subject
from tests.integration.test_aml_workflow import Env, drain, ok

pytestmark = pytest.mark.integration
SUP = subject("a", "supervisor1")


@pytest.fixture
def completed(make_client, make_worker, token, bank, db_url):  # type: ignore[no-untyped-def]
    env = Env(make_client, make_worker, token, bank, db_url)
    case_id, analyst = env.case()
    run_id = env.start(case_id, analyst)
    worker = make_worker(**env.over)
    assert drain(worker, db_url, run_id, {"WAITING_REVIEW"}) == "WAITING_REVIEW"
    submission = env.submit(case_id, analyst)
    ok(env.decide(submission, "APPROVE"))
    assert drain(worker, db_url, run_id, {"SUCCEEDED"}) == "SUCCEEDED"
    detail = ok(env.client.get(f"/api/v1/cases/{case_id}", headers=token(SUP)))
    assert detail["status"] == "COMPLETED"
    return env, case_id, analyst, run_id, worker, detail["record_version"], submission


def reopen(env: Env, case_id: str, version: int, actor: str = SUP) -> Any:
    return env.client.post(
        f"/api/v1/cases/{case_id}/reopen",
        json={"reason": "New information requires renewed investigation"},
        headers={**env.token(actor), "If-Match": str(version)},
    )


def historical_rows(db_url: str, case_id: str) -> dict[str, Any]:
    """Capture all immutable content and completion records before reopening."""
    case_uuid = uuid.UUID(case_id)
    with transaction(session_factory(db_url)) as s:
        report = s.scalar(select(Report).where(Report.case_id == case_uuid))
        assert report is not None
        models_and_filters = (
            (CaseStatusHistory, CaseStatusHistory.case_id == case_uuid),
            (ApprovalRequest, ApprovalRequest.case_id == case_uuid),
            (
                ApprovalDecision,
                ApprovalDecision.request_id.in_(
                    select(ApprovalRequest.id).where(ApprovalRequest.case_id == case_uuid)
                ),
            ),
            (Finalization, Finalization.case_id == case_uuid),
            (ReportVersion, ReportVersion.report_id == report.id),
            (
                ReportClaim,
                ReportClaim.report_version_id.in_(
                    select(ReportVersion.id).where(ReportVersion.report_id == report.id)
                ),
            ),
            (WorkflowRun, WorkflowRun.case_id == case_uuid),
        )
        return {
            model.__tablename__: {
                str(row.id): deepcopy(
                    {column.name: getattr(row, column.name) for column in model.__table__.columns}
                )
                for row in s.scalars(select(model).where(condition))
            }
            for model, condition in models_and_filters
        }


@pytest.mark.req("FR-006")
@pytest.mark.release_blocker
def test_reopen_preserves_history_and_new_work_can_complete(completed) -> None:  # type: ignore[no-untyped-def]
    env, case_id, analyst, old_run, worker, version, old_submission = completed
    before = historical_rows(env.db, case_id)
    response = reopen(env, case_id, version)
    result = ok(response)
    assert result["status"] == "REWORK" and result["record_version"] == version + 1
    assert response.headers["ETag"] == f'"{version + 1}"'
    assert result["report_status"] == "DRAFT"
    assert result["report_version_id"] != old_submission["report_version_id"]
    after = historical_rows(env.db, case_id)
    for table, rows in before.items():
        for row_id, row in rows.items():
            assert after[table][row_id] == row, (table, row_id)
    old_version = before["report_versions"][old_submission["report_version_id"]]
    new_version = after["report_versions"][result["report_version_id"]]
    assert new_version["version_no"] == old_version["version_no"] + 1
    assert new_version["base_version_no"] == old_version["version_no"]
    for field in ("content", "content_hash", "validation", "snapshot_id"):
        assert new_version[field] == old_version[field]
    assert (
        new_version["lineage"]["reopening"]["consumed_approval_request_id"]
        == old_submission["approval_request_id"]
    )
    old_claims = [
        row
        for row in before["report_claims"].values()
        if str(row["report_version_id"]) == old_submission["report_version_id"]
    ]
    new_claims = [
        row
        for row in after["report_claims"].values()
        if str(row["report_version_id"]) == result["report_version_id"]
    ]
    fields = (
        "claim_key",
        "section",
        "claim_type",
        "text",
        "support_status",
        "evidence_refs",
        "citation_refs",
    )
    assert sorted([tuple(str(row[f]) for f in fields) for row in old_claims]) == sorted(
        [tuple(str(row[f]) for f in fields) for row in new_claims]
    )
    view = ok(
        env.client.get(
            f"/api/v1/cases/{case_id}/reports/versions/{result['report_version_no']}",
            headers=env.token(analyst),
        )
    )
    assert view["watermark"] == "DRAFT - NOT APPROVED"
    assert audit_actions(env.db, case_id=case_id).count("CASE_REOPENED") == 1
    with transaction(session_factory(env.db)) as s:
        events = s.scalars(
            select(OutboxEvent).where(
                OutboxEvent.resource_id == case_id,
                OutboxEvent.event_type == "case.status_changed.v1",
            )
        ).all()
        assert any(
            event.payload == {"case_id": case_id, "from": "COMPLETED", "to": "REWORK"} for event in events
        )
    # A fresh run, report and independent approval can complete a second episode.
    new_run = env.start(case_id, analyst)
    assert new_run != old_run
    assert drain(worker, env.db, new_run, {"WAITING_REVIEW"}) == "WAITING_REVIEW"
    submission = env.submit(case_id, analyst)
    assert submission["approval_request_id"] != old_submission["approval_request_id"]
    assert submission["report_version_id"] != old_submission["report_version_id"]
    ok(env.decide(submission, "APPROVE"))
    assert drain(worker, env.db, new_run, {"SUCCEEDED"}) == "SUCCEEDED"
    with transaction(session_factory(env.db)) as s:
        old_request = s.get(ApprovalRequest, uuid.UUID(old_submission["approval_request_id"]))
        assert old_request is not None and old_request.status == "CONSUMED"
        assert (
            len(
                s.scalars(
                    select(CaseStatusHistory).where(
                        CaseStatusHistory.case_id == uuid.UUID(case_id),
                        CaseStatusHistory.to_status == "COMPLETED",
                    )
                ).all()
            )
            == 2
        )


@pytest.mark.parametrize("actor_key,expected", [("analyst", 403), ("reviewer", 403), ("other_tenant", 404)])
def test_reopening_permissions_and_concealment(completed, actor_key: str, expected: int) -> None:  # type: ignore[no-untyped-def]
    env, case_id, analyst, _, _, version, _ = completed
    actor = {
        "analyst": analyst,
        "reviewer": subject("a", "reviewer1"),
        "other_tenant": subject("b", "supervisor1"),
    }[actor_key]
    before = historical_rows(env.db, case_id)
    assert reopen(env, case_id, version, actor).status_code == expected
    assert historical_rows(env.db, case_id) == before
    if expected == 404:
        assert reopen(env, str(uuid.uuid4()), version, actor).status_code == 404


def test_reopen_requires_reason_and_matching_version(completed) -> None:  # type: ignore[no-untyped-def]
    env, case_id, _, _, _, version, _ = completed
    before = historical_rows(env.db, case_id)
    stale = reopen(env, case_id, version - 1)
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "VERSION_CONFLICT"
    url = f"/api/v1/cases/{case_id}/reopen"
    missing = env.client.post(url, json={"reason": "Renewed investigation"}, headers=env.token(SUP))
    assert missing.status_code == 400
    for reason in ("", " ", "x"):
        response = env.client.post(
            url,
            json={"reason": reason},
            headers={**env.token(SUP), "If-Match": str(version)},
        )
        assert response.status_code == 400
    mismatch = env.client.post(
        url,
        json={"reason": "Renewed investigation", "record_version": version + 1},
        headers={**env.token(SUP), "If-Match": str(version)},
    )
    assert mismatch.status_code == 400
    assert historical_rows(env.db, case_id) == before


def test_concurrent_reopening_only_creates_one_new_episode(completed) -> None:  # type: ignore[no-untyped-def]
    env, case_id, _, _, _, version, _ = completed
    with ThreadPoolExecutor(max_workers=2) as pool:
        codes = sorted(pool.map(lambda _: reopen(env, case_id, version).status_code, range(2)))
    assert codes == [200, 409]
    assert audit_actions(env.db, case_id=case_id).count("CASE_REOPENED") == 1


def test_supervisor_reopening_uses_existing_mfa_step_up(completed) -> None:  # type: ignore[no-untyped-def]
    env, case_id, _, _, _, version, _ = completed
    before = historical_rows(env.db, case_id)
    with transaction(session_factory(env.db)) as s:
        ctx = resolve_principal_by_id(s, sid("tenant:a"), principal("a", "supervisor1"))
    ctx = replace(ctx, mfa_satisfied=False)
    with pytest.raises(AppError), transaction(session_factory(env.db)) as s:
        reopening.reopen_case(
            s,
            ctx,
            case_id,
            reopening.ReopenRequest(reason="Renewed investigation"),
            version,
        )
    assert historical_rows(env.db, case_id) == before


def test_failed_reopening_audit_rolls_back_every_change(completed, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    env, case_id, _, _, _, version, _ = completed
    before = historical_rows(env.db, case_id)
    with transaction(session_factory(env.db)) as s:
        ctx = resolve_principal_by_id(s, sid("tenant:a"), principal("a", "supervisor1"))
    ctx = replace(ctx, mfa_satisfied=True)
    original = reopening.audit.record

    def fail(session, **kwargs):  # type: ignore[no-untyped-def]
        if kwargs.get("action") == "CASE_REOPENED":
            raise RuntimeError("synthetic reopening audit failure")
        return original(session, **kwargs)

    monkeypatch.setattr(reopening.audit, "record", fail)
    with (
        pytest.raises(RuntimeError, match="synthetic reopening audit failure"),
        transaction(session_factory(env.db)) as s,
    ):
        reopening.reopen_case(
            s,
            ctx,
            case_id,
            reopening.ReopenRequest(reason="Renewed investigation"),
            version,
        )
    assert historical_rows(env.db, case_id) == before
    detail = ok(env.client.get(f"/api/v1/cases/{case_id}", headers=env.token(SUP)))
    assert detail["status"] == "COMPLETED" and detail["record_version"] == version


@pytest.mark.parametrize("status", ["ASSIGNED", "CANCELLED", "COMPLETED"])
def test_other_statuses_cannot_be_reopened(client, token, db_url, status: str) -> None:  # type: ignore[no-untyped-def]
    case_id = ingest(client, token)["case_id"]
    with transaction(session_factory(db_url)) as s:
        case = s.get(Case, uuid.UUID(case_id))
        assert case is not None
        case.status = status
    detail = ok(client.get(f"/api/v1/cases/{case_id}", headers=token(SUP)))
    response = client.post(
        f"/api/v1/cases/{case_id}/reopen",
        json={"reason": "Attempt invalid reopening"},
        headers={**token(SUP), "If-Match": str(detail["record_version"])},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CASE_INVALID_STATE"


@pytest.mark.parametrize("obstacle", ["active_run", "uncertain_effect"])
def test_reopening_refuses_unresolved_work(completed, obstacle: str) -> None:  # type: ignore[no-untyped-def]
    env, case_id, _, old_run, _, version, _ = completed
    with transaction(session_factory(env.db)) as s:
        if obstacle == "active_run":
            run = s.get(WorkflowRun, uuid.UUID(old_run))
            assert run is not None
            run.status = "RUNNING"  # Arrange a completion/run inconsistency.
        else:
            s.add(
                ExternalOperation(
                    tenant_id=sid("tenant:a"),
                    case_id=uuid.UUID(case_id),
                    operation_type="CASE_STATUS_UPDATE",
                    operation_key=f"test:{uuid.uuid4()}",
                    target_system="mock-bank",
                    request_hash="a" * 64,
                    status="OUTCOME_UNCERTAIN",
                )
            )
    before = historical_rows(env.db, case_id)
    response = reopen(env, case_id, version)
    assert response.status_code == 409
    assert historical_rows(env.db, case_id) == before
