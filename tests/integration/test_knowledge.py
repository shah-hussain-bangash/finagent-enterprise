"""Policy knowledge: lifecycle, applicability, ACL, conflicts, citations (P7).

FR-017..020, AER-011..013, SPR-011, BR-005, MST:SEC-001.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import select

from finagent.database.models import KnowledgeChunk
from finagent.database.session import session_factory, transaction
from tests.helpers import assigned_analyst, ingest, subject

pytestmark = pytest.mark.integration
PADMIN = subject("a", "policyadmin1")
QUERY = "repeated cash deposits below the reporting threshold"


def policy(key: str = "pol-test-structuring", version: str = "1.0", **over: Any) -> dict[str, Any]:
    body = {
        "policy_key": key,
        "title": "SYNTHETIC test policy: cash structuring",
        "department": "AML",
        "owner_label": "AML policy owner (synthetic)",
        "classification": "INTERNAL",
        "alert_types": ["TM_STRUCTURING"],
        "version_label": version,
        "effective_from": "2026-01-01",
        "sections": [
            {"section": "1", "heading": "Scope", "text": "Applies to cash deposits reviewed in AML alerts."},
            {
                "section": "2",
                "heading": "Indicators",
                "text": "Repeated cash deposits just below the reporting threshold within 30 days are an "
                "indicator requiring analyst review. An indicator is not a determination.",
            },
        ],
    }
    body.update(over)
    return body


def create(client: Any, token: Any, body: dict[str, Any]) -> dict[str, Any]:
    r = client.post("/api/v1/knowledge/policies", json=body, headers=token(PADMIN))
    assert r.status_code == 201, r.text
    out: dict[str, Any] = r.json()
    return out


def act(client: Any, token: Any, version_id: str, action: str, who: str = PADMIN) -> Any:
    return client.post(
        f"/api/v1/knowledge/policy-versions/{version_id}/{action}",
        json={"reason": f"synthetic test {action}"},
        headers=token(who),
    )


def search(client: Any, token: Any, who: str, **body: Any) -> dict[str, Any]:
    r = client.post("/api/v1/knowledge/search", json={"query": QUERY, **body}, headers=token(who))
    assert r.status_code == 200, r.text
    out: dict[str, Any] = r.json()
    return out


ANALYST = subject("a", "analyst1")


@pytest.mark.req("FR-017", "FR-018", "FR-020", "AER-012", "BR-005", "FR-036")
@pytest.mark.release_blocker
def test_only_approved_versions_are_retrievable_and_lifecycle_is_enforced(client, token) -> None:  # type: ignore[no-untyped-def]
    v = create(client, token, policy())
    assert v["approval_status"] == "DRAFT" and v["ingestion_status"] == "INDEXED" and v["active_chunks"] == 2
    draft = search(client, token, ANALYST, as_of="2026-06-01")
    assert draft["outcome"] == "INSUFFICIENT_EVIDENCE" and draft["abstained"] and draft["citations"] == []
    assert "general model knowledge is not substituted" in draft["message"]
    assert act(client, token, v["policy_version_id"], "approve", who=ANALYST).status_code == 403
    assert act(client, token, v["policy_version_id"], "approve").json()["approval_status"] == "APPROVED"
    ans = search(client, token, ANALYST, as_of="2026-06-01")
    assert ans["outcome"] == "ANSWERED" and not ans["abstained"]
    top = ans["citations"][0]
    assert top["section"] == "2" and top["version_label"] == "1.0" and top["historical"] is False
    assert top["policy_version_id"] == v["policy_version_id"] and len(top["text_hash"]) == 64
    assert act(client, token, v["policy_version_id"], "approve").status_code == 409  # already approved
    act(client, token, v["policy_version_id"], "retire")
    assert search(client, token, ANALYST, as_of="2026-06-01")["outcome"] == "INSUFFICIENT_EVIDENCE"
    opened = client.get(f"/api/v1/knowledge/chunks/{top['chunk_id']}", headers=token(ANALYST)).json()
    assert opened["approval_status"] == "RETIRED" and opened["text"]  # history stays readable
    act(client, token, v["policy_version_id"], "revoke")
    opened = client.get(f"/api/v1/knowledge/chunks/{top['chunk_id']}", headers=token(ANALYST)).json()
    assert opened["text"] is None and "revoked" in opened["notice"]
    dup = client.post(
        "/api/v1/knowledge/policies", json=policy(sections=policy()["sections"][:1]), headers=token(PADMIN)
    )
    assert dup.status_code == 409  # same label, different content


@pytest.mark.req("FR-018", "AER-011")
def test_effective_dates_select_the_applicable_version_and_label_history(client, token) -> None:  # type: ignore[no-untyped-def]
    old = create(client, token, policy(version="1.0", effective_from="2024-01-01", effective_to="2025-12-31"))
    new = create(client, token, policy(version="2.0", effective_from="2026-01-01"))
    for v in (old, new):
        act(client, token, v["policy_version_id"], "approve")
    past = search(client, token, ANALYST, as_of="2025-06-01")
    assert {c["version_label"] for c in past["citations"]} == {"1.0"}
    assert all(c["historical"] for c in past["citations"])  # not today's guidance: labelled
    now = search(client, token, ANALYST, as_of=datetime.now(UTC).date().isoformat())
    assert {c["version_label"] for c in now["citations"]} == {"2.0"} and not now["citations"][0]["historical"]
    assert search(client, token, ANALYST, as_of="2023-06-01")["outcome"] == "INSUFFICIENT_EVIDENCE"


@pytest.mark.req("FR-020", "AER-013")
def test_two_effective_versions_are_conflicting_guidance(client, token) -> None:  # type: ignore[no-untyped-def]
    for label, start in (("1.0", "2026-01-01"), ("1.1", "2026-03-01")):
        act(
            client,
            token,
            create(client, token, policy(version=label, effective_from=start))["policy_version_id"],
            "approve",
        )
    out = search(client, token, ANALYST, as_of="2026-06-01")
    assert out["outcome"] == "CONFLICTING_GUIDANCE" and out["abstained"]
    assert out["evidence_gaps"][0]["code"] == "CONFLICTING_POLICY_VERSIONS"
    assert out["evidence_gaps"][0]["owner_role"] == "POLICY_ADMIN"


@pytest.mark.req("FR-018", "SPR-011", "MST:SEC-001", "FR-002", "MST:RAG-001")
@pytest.mark.release_blocker
def test_acl_tenant_and_applicability_filter_before_ranking(client, token) -> None:  # type: ignore[no-untyped-def]
    admin_only = create(client, token, policy(key="pol-admin-only", allowed_roles=["POLICY_ADMIN"]))
    other_type = create(client, token, policy(key="pol-other-alert", alert_types=["OTHER_ALERT"]))
    for v in (admin_only, other_type):
        act(client, token, v["policy_version_id"], "approve")
    case_id = ingest(client, token)["case_id"]
    analyst = assigned_analyst(client, token, case_id)
    as_analyst = search(client, token, analyst, case_id=case_id)
    assert as_analyst["outcome"] == "INSUFFICIENT_EVIDENCE"  # ACL + applicability exclude both
    as_admin = search(client, token, PADMIN, as_of="2026-06-01", alert_type="TM_STRUCTURING")
    assert {c["policy_key"] for c in as_admin["citations"]} == {"pol-admin-only"}
    chunk_id = as_admin["citations"][0]["chunk_id"]
    absent = client.get(f"/api/v1/knowledge/chunks/{uuid.uuid4()}", headers=token(analyst))
    for who in (analyst, "b.analyst1@synthetic", "b.policyadmin1@synthetic"):
        r = client.get(f"/api/v1/knowledge/chunks/{chunk_id}", headers=token(who))
        assert r.status_code == absent.status_code == 404
        assert r.json()["error"]["message"] == absent.json()["error"]["message"]
    b = search(client, token, "b.policyadmin1@synthetic", as_of="2026-06-01")
    assert b["outcome"] == "INSUFFICIENT_EVIDENCE"  # tenant A's corpus never reaches tenant B
    b_case = client.post(
        "/api/v1/knowledge/search",
        json={"query": QUERY, "case_id": case_id},
        headers=token("b.analyst1@synthetic"),
    )
    assert b_case.status_code == 404


@pytest.mark.req("FR-019", "AER-013", "DIR-017", "SPR-011", "MST:RAG-002")
@pytest.mark.release_blocker
def test_citation_validation_reports_each_check(client, token, db_url) -> None:  # type: ignore[no-untyped-def]
    v = create(client, token, policy())
    act(client, token, v["policy_version_id"], "approve")
    cite = search(client, token, ANALYST, as_of="2026-06-01")["citations"][0]

    def validate(c: dict[str, Any], claim: str | None) -> dict[str, Any]:
        r = client.post(
            "/api/v1/knowledge/citations/validate",
            json={"citation": c, "claim_text": claim},
            headers=token(ANALYST),
        )
        out: dict[str, Any] = r.json()
        return out

    good = validate(cite, "Repeated cash deposits just below the reporting threshold require analyst review.")
    assert good["valid"] and good["support_method"] == "lexical-support-v1"
    unsupported = validate(cite, "The customer is guilty of money laundering and the account must be frozen.")
    assert not unsupported["valid"] and unsupported["checks"]["support"] is False
    assert "CLAIM_NOT_SUPPORTED_BY_CITATION" in unsupported["reasons"]
    assert validate({**cite, "version_label": "9.9"}, None)["checks"]["version"] is False
    assert validate({**cite, "section": "7"}, None)["checks"]["location"] is False
    assert validate({**cite, "chunk_id": str(uuid.uuid4())}, None)["reasons"] == ["CITATION_NOT_FOUND"]
    act(client, token, v["policy_version_id"], "revoke")
    revoked = validate(cite, None)
    assert revoked["checks"]["current_access"] is False and not revoked["valid"]
    # An ACTIVE chunk of a version that is not APPROVED (a draft) must also fail the access check.
    draft = create(client, token, policy(key="pol-draft-only"))
    with transaction(session_factory(db_url)) as s:
        chunk = s.scalar(
            select(KnowledgeChunk).where(
                KnowledgeChunk.policy_version_id == uuid.UUID(draft["policy_version_id"]),
                KnowledgeChunk.active.is_(True),
            )
        )
        assert chunk is not None
        draft_cite = {
            "chunk_id": str(chunk.id),
            "policy_version_id": draft["policy_version_id"],
            "section": chunk.section,
        }
    unapproved = validate(draft_cite, None)
    assert unapproved["checks"]["current_access"] is False and not unapproved["valid"]


@pytest.mark.req("FR-017", "FR-018", "DIR-001", "MST:RAG-003")
def test_sync_from_policy_repository_then_safe_reindex(make_client, token, bank) -> None:  # type: ignore[no-untyped-def]
    client = make_client(bank_base_url=bank.url)
    first = client.post("/api/v1/knowledge/sync", headers=token(PADMIN)).json()
    assert first["created"] == 3
    assert client.post("/api/v1/knowledge/sync", headers=token(PADMIN)).json() == {
        **first,
        "created": 0,
        "unchanged": 3,
    }
    versions = client.get("/api/v1/knowledge/policies", headers=token(PADMIN)).json()["items"]
    assert {v["approval_status"] for v in versions} == {"DRAFT"}  # never auto-approved
    v2 = next(v for v in versions if v["policy_key"] == "pol-aml-structuring" and v["version_label"] == "2.0")
    act(client, token, v2["policy_version_id"], "approve")
    case_id = ingest(client, token)["case_id"]
    analyst = assigned_analyst(client, token, case_id)
    ans = search(client, token, analyst, case_id=case_id)
    assert ans["outcome"] == "ANSWERED" and ans["citations"][0]["policy_key"] == "pol-aml-structuring"
    before = {c["chunk_id"] for c in ans["citations"]}
    r = client.post(
        f"/api/v1/knowledge/policy-versions/{v2['policy_version_id']}/reindex", headers=token(PADMIN)
    )
    assert r.status_code == 200 and r.json()["index_version"] == v2["index_version"] + 1
    after = search(client, token, analyst, case_id=case_id)
    assert after["outcome"] == "ANSWERED" and before.isdisjoint({c["chunk_id"] for c in after["citations"]})
    assert (
        client.get(f"/api/v1/knowledge/chunks/{next(iter(before))}", headers=token(analyst)).json()["active"]
        is False
    )
    listed = client.get("/api/v1/knowledge/policies", headers=token(analyst)).json()["items"]
    assert [v["approval_status"] for v in listed] == ["APPROVED"]  # searchers never see drafts
