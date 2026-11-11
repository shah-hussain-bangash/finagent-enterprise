"""Required end-to-end acceptance journey (synthetic; local adapters for external systems only).

Internal workflow, validation, authorization, reports and approval logic all execute for real:
API (FastAPI TestClient) + PostgreSQL as finagent_app + the real worker loop + the SYNTHETIC mock bank
over HTTP + the isolated document parser + the deterministic MockModelProvider behind the model gateway.

Numbered comments map to the 20 journey steps in docs/testing/acceptance-journey.md.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select, update

from finagent.database.models import ExternalOperation, Lease, WorkflowRun
from finagent.database.seeds import sid
from finagent.database.session import session_factory, transaction
from finagent.orchestration.faults import FaultInjector, SimulatedCrash
from finagent.services.audit_service import writer as audit_writer
from tests.agent_helpers import approve_policies
from tests.helpers import CONNECTOR, alert_envelope, assigned_analyst, audit_actions, principal, subject

pytestmark = [pytest.mark.integration, pytest.mark.release_blocker]
REVIEWER = subject("a", "reviewer1")
TADMIN = subject("a", "tenantadmin1")
B_ANALYST, B_REVIEWER = subject("b", "analyst1"), subject("b", "reviewer1")


def drain(worker: Any, db_url: str, run_id: str, until: set[str], ticks: int = 20) -> str:
    for _ in range(ticks):
        worker.tick()
        with transaction(session_factory(db_url)) as s:
            run = s.get(WorkflowRun, uuid.UUID(run_id))
            assert run is not None
            if run.status in until:
                return run.status
    raise AssertionError(f"run did not reach {until}; last status {run.status} ({run.status_reason})")


def ok(r: Any, code: int = 200) -> dict[str, Any]:
    assert r.status_code == code, r.text
    out: dict[str, Any] = r.json()
    return out


@pytest.mark.req(
    "BR-001",
    "BR-002",
    "BR-003",
    "BR-004",
    "BR-005",
    "BR-006",
    "BR-007",
    "BR-008",
    "FR-006",
    "FR-008",
    "FR-012",
    "FR-013",
    "FR-016",
    "FR-018",
    "FR-026",
    "FR-027",
    "FR-028",
    "FR-029",
    "FR-030",
    "FR-031",
    "FR-034",
    "FR-036",
    "AER-023",
    "SPR-033",
    "MST:SEC-001",
    "NQ-019",
    "NQ-020",
)
def test_full_human_reviewed_investigation_journey(make_client, make_worker, token, bank, db_url) -> None:  # type: ignore[no-untyped-def]
    over = {"bank_base_url": bank.url, "case_status_write_enabled": True, "lease_ttl_seconds": 5}
    client = make_client(**over)
    approve_policies(client, token)

    # 1. connector authenticates and ingests an alert ------------------------------------------------
    env = alert_envelope("AML-E2E-001")
    hdr = {**token(CONNECTOR["a"]), "Idempotency-Key": "e2e-intake-0001"}
    created = ok(client.post("/api/v1/alerts", json=env, headers=hdr), 201)
    case_id = created["case_id"]
    # 2. replay creates no duplicate case ------------------------------------------------------------
    again = client.post("/api/v1/alerts", json=env, headers=hdr)
    assert again.status_code in (200, 201) and again.json()["case_id"] == case_id
    replay = client.post(
        "/api/v1/alerts",
        json={**env, "event_id": f"evt-{uuid.uuid4()}"},
        headers={**token(CONNECTOR["a"]), "Idempotency-Key": "e2e-intake-0002"},
    )
    assert replay.status_code == 200 and replay.json()["case_id"] == case_id
    # 3. a changed payload under the same source alert id is rejected ---------------------------------
    changed = alert_envelope("AML-E2E-001", severity="LOW")
    conflict = client.post(
        "/api/v1/alerts",
        json=changed,
        headers={**token(CONNECTOR["a"]), "Idempotency-Key": "e2e-intake-0003"},
    )
    assert conflict.status_code == 409 and conflict.json()["error"]["code"] == "ALERT_PAYLOAD_CONFLICT"
    # 4. assigned to an authorized analyst -----------------------------------------------------------
    analyst = assigned_analyst(client, token, case_id)
    detail = ok(client.get(f"/api/v1/cases/{case_id}", headers=token(analyst)))
    assert detail["status"] == "ASSIGNED"

    # 5-11. evidence, documents, snapshot, policy, specialists, validation, draft --------------------
    run = ok(
        client.post(
            "/api/v1/workflows",
            json={"case_id": case_id},
            headers={**token(analyst), "Idempotency-Key": "e2e-run-0001"},
        ),
        202,
    )
    run_id = run["run_id"]
    worker = make_worker(**over)
    assert drain(worker, db_url, run_id, {"WAITING_REVIEW"}) == "WAITING_REVIEW"
    docs = ok(client.get(f"/api/v1/cases/{case_id}/documents", headers=token(analyst)))["items"]
    assert {d["document_type"]: d["status"] for d in docs} == {
        "PASSPORT": "PARSED",
        "PROOF_OF_ADDRESS": "PARSED",
        "BANK_STATEMENT": "PARSED",
    }  # 6
    passport = ok(
        client.get(
            f"/api/v1/documents/{next(d['document_id'] for d in docs if d['document_type'] == 'PASSPORT')}",
            headers=token(analyst),
        )
    )
    assert all(f["page"] == 1 for f in passport["fields"])  # provenance retained
    snaps = ok(client.get(f"/api/v1/cases/{case_id}/snapshots", headers=token(analyst)))["items"]
    assert snaps and snaps[0]["completeness_status"] == "CONFLICTED"  # 7 (DOB differs: both values kept)
    outputs = ok(client.get(f"/api/v1/cases/{case_id}/agent-outputs", headers=token(analyst)))["items"]
    assert {o["capability_id"] for o in outputs} == {
        "AG-KYC",
        "AG-AML",
        "CAP-DOCUMENT",
        "AG-COMPLIANCE",
        "AG-REPORT",
    }  # 9
    assert all(not o["validation"].get("boundary_violations") for o in outputs)  # 10
    cmp_ = next(o for o in outputs if o["capability_id"] == "AG-COMPLIANCE")
    assert cmp_["lineage"]["retrieval_outcome"] == "ANSWERED" and cmp_["lineage"]["policy_versions"]  # 8
    blocked = client.post(
        f"/api/v1/cases/{case_id}/reports/submit",
        json={"version_no": 1},
        headers={**token(analyst), "Idempotency-Key": "e2e-submit-early"},
    )
    assert blocked.status_code == 422  # FR-015: an open contradiction blocks submission
    assert "OPEN_EVIDENCE_CONFLICTS" in {p["code"] for p in blocked.json()["error"]["details"]["problems"]}
    # the analyst resolves the conflict with a reason and asks for re-analysis on fresh evidence
    ev = ok(client.get(f"/api/v1/cases/{case_id}/evidence", headers=token(analyst)))
    conflict_id = next(c["conflict_id"] for c in ev["conflicts"] if c["status"] == "OPEN")
    ok(
        client.post(
            f"/api/v1/cases/{case_id}/evidence-conflicts/{conflict_id}/resolution",
            json={"resolution": "USE_A", "rationale": "core banking DOB verified at onboarding; scan typo"},
            headers=token(analyst),
        )
    )
    ok(
        client.post(
            f"/api/v1/workflows/{run_id}/resume",
            json={"action": "REANALYZE", "reason": "conflict resolved"},
            headers=token(analyst),
        ),
        202,
    )
    assert drain(worker, db_url, run_id, {"WAITING_REVIEW"}) == "WAITING_REVIEW"
    reports = ok(client.get(f"/api/v1/cases/{case_id}/reports", headers=token(analyst)))
    assert reports["report"]["current_version_no"] == 2
    v2 = ok(client.get(f"/api/v1/cases/{case_id}/reports/versions/2", headers=token(analyst)))
    assert (
        v2["origin"] == "AI_DRAFT" and v2["validation"]["valid"] and v2["watermark"] == "DRAFT - NOT APPROVED"
    )
    assert any(
        c["claim_type"] == "DETERMINISTIC_RESULT" and c["evidence_refs"][0].startswith("calc:")
        for c in v2["claims"]
    )
    latest = ok(client.get(f"/api/v1/cases/{case_id}/snapshots", headers=token(analyst)))["items"][0]
    assert latest["completeness_status"] == "COMPLETE" and latest["snapshot_id"] == v2["snapshot_id"]
    # 11. the analyst edits the draft: a new version with author and time; stale base -> 409
    edit = {
        "base_version_no": 2,
        "edit_note": "analyst questions refined",
        "sections": [
            {
                "section_id": "analyst_questions",
                "text": "- Obtain source-of-funds evidence for the cash deposits.",
            }
        ],
    }
    v3 = ok(client.post(f"/api/v1/cases/{case_id}/reports/edits", json=edit, headers=token(analyst)), 201)
    assert v3["version_no"] == 3 and v3["origin"] == "ANALYST_EDIT" and v3["diff_from_previous"]
    assert (
        client.post(f"/api/v1/cases/{case_id}/reports/edits", json=edit, headers=token(analyst)).status_code
        == 409
    )

    # 12. submission freezes report v3 + snapshot -----------------------------------------------------
    sub = ok(
        client.post(
            f"/api/v1/cases/{case_id}/reports/submit",
            json={"version_no": 3},
            headers={**token(analyst), "Idempotency-Key": "e2e-submit-0001"},
        ),
        201,
    )
    req1 = sub["approval_request_id"]
    assert (
        sub["reviewer_id"] == str(principal("a", "reviewer1"))
        and sub["report_version_id"] == v3["version_id"]
    )
    assert ok(client.get(f"/api/v1/cases/{case_id}", headers=token(analyst)))["status"] == "AWAITING_REVIEW"
    decision = {
        "report_version_id": sub["report_version_id"],
        "snapshot_id": sub["snapshot_id"],
        "rationale": "Evidence and policy citations reviewed; calculations match the ledger.",
    }

    def decide(who: str, req: str, kind: str, key: str) -> Any:
        return client.post(
            f"/api/v1/approvals/{req}/decision",
            json={**decision, "decision": kind},
            headers={**token(who), "Idempotency-Key": key},
        )

    # 13. self-approval (even through a delegation) and unauthorized review are denied -------------------
    deleg = ok(
        client.post(
            "/api/v1/admin/delegations",
            headers=token(TADMIN),
            json={
                "delegator_id": str(principal("a", "reviewer1")),
                "delegate_id": str(principal("a", analyst.split(".")[1].split("@")[0])),
                "role": "REVIEWER",
                "reason": "holiday cover (synthetic test)",
                "valid_to": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
            },
        ),
        201,
    )
    self_try = decide(analyst, req1, "APPROVE", "e2e-dec-self")
    assert self_try.status_code == 403 and "Separation of duties" in self_try.json()["error"]["message"]
    ok(client.post(f"/api/v1/admin/delegations/{deleg['delegation_id']}/revoke", headers=token(TADMIN)))
    other = "a.analyst2@synthetic" if analyst == "a.analyst1@synthetic" else "a.analyst1@synthetic"
    assert decide(other, req1, "APPROVE", "e2e-dec-other").status_code == 403
    assert decide(B_REVIEWER, req1, "APPROVE", "e2e-dec-b").status_code == 404
    stale = client.post(
        f"/api/v1/approvals/{req1}/decision",
        headers={**token(REVIEWER), "Idempotency-Key": "e2e-dec-stale"},
        json={**decision, "decision": "APPROVE", "report_version_id": v2["version_id"]},
    )
    assert stale.status_code == 409  # superseded content cannot be approved (UI-002)

    # 14 + 16. independent decision: return for rework, analyst reworks and resubmits -------------------
    returned = ok(decide(REVIEWER, req1, "RETURN_FOR_REWORK", "e2e-dec-0001"))
    assert returned["status"] == "RETURNED" and returned["decision"]["mfa_satisfied"] is True
    assert ok(decide(REVIEWER, req1, "RETURN_FOR_REWORK", "e2e-dec-0001")) == returned  # idempotent replay
    assert ok(client.get(f"/api/v1/cases/{case_id}", headers=token(analyst)))["status"] == "REWORK"
    v4 = ok(
        client.post(
            f"/api/v1/cases/{case_id}/reports/edits",
            headers=token(analyst),
            json={
                "base_version_no": 3,
                "edit_note": "rework per reviewer",
                "sections": [
                    {
                        "section_id": "analyst_questions",
                        "text": "- Obtain source-of-funds evidence.\n- Confirm the wire purpose.",
                    }
                ],
            },
        ),
        201,
    )
    sub2 = ok(
        client.post(
            f"/api/v1/cases/{case_id}/reports/submit",
            json={"version_no": v4["version_no"]},
            headers={**token(analyst), "Idempotency-Key": "e2e-submit-0002"},
        ),
        201,
    )
    decision.update(report_version_id=sub2["report_version_id"], snapshot_id=sub2["snapshot_id"])
    approved = ok(decide(REVIEWER, sub2["approval_request_id"], "APPROVE", "e2e-dec-0002"))
    assert approved["status"] == "APPROVED"

    # 15. a material change after approval invalidates it; the finalization gate refuses ----------------
    v5 = ok(
        client.post(
            f"/api/v1/cases/{case_id}/reports/edits",
            headers=token(analyst),
            json={
                "base_version_no": v4["version_no"],
                "edit_note": "late change",
                "sections": [
                    {
                        "section_id": "analyst_questions",
                        "text": "- Obtain source-of-funds evidence and the wire purpose.",
                    }
                ],
            },
        ),
        201,
    )
    req2 = ok(client.get(f"/api/v1/approvals/{sub2['approval_request_id']}", headers=token(REVIEWER)))
    assert req2["status"] == "INVALIDATED"
    assert ok(client.get(f"/api/v1/cases/{case_id}", headers=token(analyst)))["status"] == "REWORK"
    assert (
        drain(worker, db_url, run_id, {"WAITING_REVIEW"}) == "WAITING_REVIEW"
    )  # finalize refused, waits again
    with transaction(session_factory(db_url)) as s:
        assert (
            s.scalar(
                select(func.count())
                .select_from(ExternalOperation)
                .where(ExternalOperation.operation_type == "CASE_STATUS_UPDATE")
            )
            == 0
        )
    assert ok(client.get(f"/api/v1/cases/{case_id}", headers=token(analyst)))["status"] == "REWORK"

    # 17. resubmit, approve, finalize under the configured policy (write flag ON) ----------------------
    ok(
        client.put(
            "/api/v1/admin/feature-flags/case_status_write",
            headers=token(TADMIN),
            json={"enabled": True, "reason": "synthetic acceptance test enables the single write"},
        )
    )
    sub3 = ok(
        client.post(
            f"/api/v1/cases/{case_id}/reports/submit",
            json={"version_no": v5["version_no"]},
            headers={**token(analyst), "Idempotency-Key": "e2e-submit-0003"},
        ),
        201,
    )
    decision.update(report_version_id=sub3["report_version_id"], snapshot_id=sub3["snapshot_id"])
    ok(decide(REVIEWER, sub3["approval_request_id"], "APPROVE", "e2e-dec-0003"))
    draft_export = ok(
        client.post("/api/v1/exports", json={"case_id": case_id, "version_no": 3}, headers=token(REVIEWER)),
        201,
    )
    assert draft_export["kind"] == "DRAFT"
    # 20. the worker is killed right after the downstream write; a second worker takes over ------------
    crashing = make_worker(
        faults=FaultInjector.parse("after_external_apply:finalize:crash"), worker_id="worker-A", **over
    )
    with pytest.raises(SimulatedCrash):
        for _ in range(5):
            crashing.tick()
    with transaction(session_factory(db_url)) as s:
        s.execute(
            update(Lease)
            .where(Lease.resource_id == uuid.UUID(run_id))
            .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
    assert (
        ok(client.get(f"/api/v1/cases/{case_id}", headers=token(analyst)))["status"] == "FINALIZING"
    )  # never early
    survivor = make_worker(worker_id="worker-B", **over)
    assert drain(survivor, db_url, run_id, {"SUCCEEDED"}) == "SUCCEEDED"
    final = ok(client.get(f"/api/v1/cases/{case_id}", headers=token(analyst)))
    assert final["status"] == "COMPLETED"
    assert bank.calls("/status") == 1  # exactly one downstream case-status write despite the crash
    with transaction(session_factory(db_url)) as s:
        op = s.scalar(
            select(ExternalOperation).where(ExternalOperation.operation_type == "CASE_STATUS_UPDATE")
        )
        assert op is not None and op.status == "CONFIRMED" and op.attempts == 1
    export = ok(client.post("/api/v1/exports", json={"case_id": case_id}, headers=token(REVIEWER)), 201)
    assert export["kind"] == "APPROVED"
    content = client.get(export["download_url"], headers=token(REVIEWER)).text
    assert "Review status: APPROVED" in content and "DRAFT" not in content.split("## Claims")[0]
    assert (
        client.post("/api/v1/exports", json={"case_id": case_id}, headers=token(analyst)).status_code == 403
    )

    # 18. audit history preserved and tamper-evident; visible status is correct ------------------------
    actions = audit_actions(db_url, case_id=case_id)
    for a in (
        "CASE_CREATED",
        "EVIDENCE_SNAPSHOT_CREATED",
        "AGENT_OUTPUT_RECORDED",
        "REPORT_SUBMITTED",
        "APPROVAL_DECIDED",
        "APPROVAL_INVALIDATED",
        "FINALIZATION_REFUSED",
        "CASE_FINALIZED",
        "EXPORT_CREATED",
    ):
        assert a in actions, a
    with transaction(session_factory(db_url)) as s:
        assert audit_writer.verify_chain(s, sid("tenant:a"))
    timeline = [t["to"] for t in final["timeline"]]
    assert timeline[-2:] == ["FINALIZING", "COMPLETED"] and "REWORK" in timeline

    # 19. another tenant cannot reach any of it (identical 404s) ---------------------------------------
    absent_case = str(uuid.uuid4())
    for path in (
        f"/api/v1/cases/{case_id}",
        f"/api/v1/cases/{case_id}/reports",
        f"/api/v1/cases/{case_id}/evidence",
        f"/api/v1/cases/{case_id}/documents",
        f"/api/v1/approvals/{sub3['approval_request_id']}",
        f"/api/v1/workflows/{run_id}",
        f"/api/v1/cases/{case_id}/agent-outputs",
    ):
        r = client.get(path, headers=token(B_ANALYST))
        a = client.get(
            path.replace(case_id, absent_case)
            .replace(sub3["approval_request_id"], absent_case)
            .replace(run_id, absent_case),
            headers=token(B_ANALYST),
        )
        assert r.status_code == a.status_code == 404, path
        assert r.json()["error"]["message"] == a.json()["error"]["message"]
    assert client.get(export["download_url"], headers=token(B_REVIEWER)).status_code == 404
