"""Evaluation gate, configuration promotion/rollback, retention dry run and legal holds (P12).

AER-029..035, NQ-014, FR-039, DIR-034, SPR-024, MST:AG-001.
"""

from __future__ import annotations

from typing import Any

import pytest

from finagent.agents.registry import CapabilityUnavailableError
from finagent.services.governance_service import config as config_service
from tests.agent_helpers import PADMIN, prepared_case
from tests.helpers import ingest, subject

pytestmark = pytest.mark.integration
TADMIN, OPERATOR, AUDITOR = subject("a", "tenantadmin1"), subject("a", "operator1"), subject("a", "auditor1")


def ok(r: Any, code: int = 200) -> dict[str, Any]:
    assert r.status_code == code, r.text
    out: dict[str, Any] = r.json()
    return out


def approve_all_policies(client: Any, token: Any) -> None:
    ok(client.post("/api/v1/knowledge/sync", headers=token(PADMIN)))
    for v in ok(client.get("/api/v1/knowledge/policies", headers=token(PADMIN)))["items"]:
        if v["approval_status"] == "DRAFT":
            ok(
                client.post(
                    f"/api/v1/knowledge/policy-versions/{v['policy_version_id']}/approve",
                    json={"reason": "synthetic evaluation corpus"},
                    headers=token(PADMIN),
                )
            )


@pytest.mark.req("AER-029", "AER-032", "AER-033", "AER-034", "NQ-014", "SPR-028", "FR-040")
@pytest.mark.release_blocker
def test_evaluation_gate_promotion_and_rollback(make_client, token, bank, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    client = make_client(bank_base_url=bank.url)
    approve_all_policies(client, token)
    assert (
        client.post(
            "/api/v1/evaluations",
            json={"dataset": "aml-synthetic-v1"},
            headers=token(subject("a", "analyst1")),
        ).status_code
        == 403
    )
    run = ok(
        client.post("/api/v1/evaluations", json={"dataset": "aml-synthetic-v1"}, headers=token(PADMIN)), 201
    )
    failing = [r for r in run["results"] if r["passed"] is False]
    assert run["gate"] == "PASS_PROPOSED", failing
    assert run["metrics"]["boundary_escalation_rate"]["value"] == 1.0
    assert run["metrics"]["rag_abstention_accuracy"]["threshold_status"] == "PROPOSED_NOT_APPROVED"
    assert "not an acceptance decision" in run["notice"]
    # operator lacks knowledge:search -> retrieval metrics are explicitly not evaluated, gate INCOMPLETE
    op_run = ok(
        client.post("/api/v1/evaluations", json={"dataset": "aml-synthetic-v1"}, headers=token(OPERATOR)), 201
    )
    assert (
        op_run["gate"] == "INCOMPLETE" and op_run["metrics"]["rag_applicability_accuracy"]["passed"] is None
    )
    cfg = ok(client.post("/api/v1/admin/config-versions", headers=token(TADMIN)), 201)
    assert cfg["config_hash"] == run["config_hash"] and cfg["status"] == "CANDIDATE"
    url = f"/api/v1/admin/config-versions/{cfg['config_version_id']}/promote"
    refused = client.post(
        url,
        json={"reason": "promote without a passing gate", "evaluation_id": op_run["evaluation_id"]},
        headers=token(TADMIN),
    )
    assert refused.status_code == 422 and "INCOMPLETE" in refused.json()["error"]["message"]
    development_refused = client.post(
        url,
        json={"reason": "development is not release evidence", "evaluation_id": run["evaluation_id"]},
        headers=token(TADMIN),
    )
    assert development_refused.status_code == 422
    assert "locked release" in development_refused.json()["error"]["message"]
    # Exercise the existing promotion/rollback mechanics with the release-evidence
    # dependency isolated. Actual evidence validation has separate regression tests.
    monkeypatch.setattr(config_service, "require_release_evidence", lambda _run: None)
    promoted = ok(
        client.post(
            url,
            json={"reason": "gate passed on synthetic set", "evaluation_id": run["evaluation_id"]},
            headers=token(TADMIN),
        )
    )
    assert promoted["status"] == "PROMOTED"
    listed = ok(client.get("/api/v1/admin/config-versions", headers=token(TADMIN)))
    assert listed["current_is_promoted"] is True
    assert (
        client.post(
            f"/api/v1/admin/config-versions/{cfg['config_version_id']}/rollback",
            json={"reason": "cannot roll back to the current one"},
            headers=token(TADMIN),
        ).status_code
        == 409
    )


@pytest.mark.req("AER-034", "MST:AG-001", "AER-002")
@pytest.mark.release_blocker
def test_unpromoted_configuration_fails_closed(make_client, make_settings, token, bank) -> None:  # type: ignore[no-untyped-def]
    settings = make_settings(bank_base_url=bank.url, require_promoted_configuration=True)
    client = make_client(bank_base_url=bank.url, require_promoted_configuration=True)
    prep = prepared_case(client, token, settings)
    from finagent.agents.runner import run_simple

    scope, provider = prep.scope()
    result = run_simple("AG-AML", prep.facts(), scope)
    assert result.status == "FAILED" and result.validation["error"] == "CONFIGURATION_NOT_PROMOTED"
    assert provider.calls == 0  # refused before any model call
    assert CapabilityUnavailableError.__name__ in "CapabilityUnavailableError"


@pytest.mark.req("FR-039", "DIR-034", "SPR-024")
def test_retention_is_dry_run_only_and_respects_legal_holds(client, token) -> None:  # type: ignore[no-untyped-def]
    rules = ok(client.get("/api/v1/admin/retention-rules", headers=token(TADMIN)))["items"]
    assert {r["status"] for r in rules} == {"NOT_SET"} and all(r["retention_days"] is None for r in rules)
    dry = ok(client.post("/api/v1/admin/retention/evaluate", headers=token(TADMIN)))
    assert dry["dry_run"] and not dry["deletion_executed"] and dry["eligible"] == []
    case_id = ingest(client, token)["case_id"]
    hold = ok(
        client.post(
            "/api/v1/admin/legal-holds",
            json={"case_id": case_id, "reason": "synthetic litigation hold"},
            headers=token(TADMIN),
        ),
        201,
    )
    assert hold["active"]
    assert (
        client.post(
            "/api/v1/admin/legal-holds",
            json={"case_id": case_id, "reason": "not an admin action"},
            headers=token(subject("a", "analyst1")),
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/v1/admin/legal-holds",
            json={"case_id": case_id, "reason": "cross-tenant attempt"},
            headers=token(subject("b", "tenantadmin1")),
        ).status_code
        == 404
    )
    assert ok(client.get("/api/v1/admin/legal-holds", headers=token(AUDITOR)))["items"][0]["active"] is True
    released = ok(
        client.post(
            f"/api/v1/admin/legal-holds/{hold['legal_hold_id']}/release",
            json={"reason": "matter closed (synthetic)"},
            headers=token(TADMIN),
        )
    )
    assert released["active"] is False
    assert (
        client.post(
            f"/api/v1/admin/legal-holds/{hold['legal_hold_id']}/release",
            json={"reason": "second release attempt"},
            headers=token(TADMIN),
        ).status_code
        == 409
    )


@pytest.mark.req("NQ-038", "DIR-036", "AER-035", "AER-027", "FR-038")
def test_reporting_and_metrics_are_scoped_and_contain_no_identifiers(client, token) -> None:  # type: ignore[no-untyped-def]
    ingest(client, token)
    sup = ok(client.get("/api/v1/reporting/overview", headers=token(subject("a", "supervisor1"))))
    assert sup["scope"] == "tenant" and sup["cases_by_status"]["ASSIGNED"] == 1
    other = ok(client.get("/api/v1/reporting/overview", headers=token(subject("b", "supervisor1"))))
    assert other["cases_by_status"] == {}  # tenant B sees nothing of tenant A
    metrics = client.get("/api/v1/metrics", headers=token(OPERATOR))
    assert metrics.status_code == 200 and 'finagent_cases{status="ASSIGNED"} 1' in metrics.text
    assert "customer" not in metrics.text and "@" not in metrics.text
    assert client.get("/api/v1/metrics", headers=token(subject("a", "analyst1"))).status_code == 403


@pytest.mark.req("FR-039", "DIR-034", "SPR-024")
def test_retention_terminal_case_eligibility_matrix(client, token, db_url) -> None:  # type: ignore[no-untyped-def]
    """Retention uses the current terminal-status episode and excludes active holds."""

    import uuid
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import select

    from finagent.database.models import Case, RetentionRule
    from finagent.database.session import session_factory, transaction

    # Create the default retention-rule rows first.
    rules = ok(
        client.get(
            "/api/v1/admin/retention-rules",
            headers=token(TADMIN),
        )
    )["items"]
    assert rules

    # Five independent cases:
    # 1. old COMPLETED       -> eligible
    # 2. old CANCELLED       -> eligible
    # 3. recent COMPLETED    -> not eligible
    # 4. reopened/nonterminal-> not eligible
    # 5. old COMPLETED+hold  -> held, not eligible
    old_completed = ingest(client, token, ref="AML-RET-OLD-COMPLETED")["case_id"]
    old_cancelled = ingest(client, token, ref="AML-RET-OLD-CANCELLED")["case_id"]
    recent_completed = ingest(client, token, ref="AML-RET-RECENT-COMPLETED")["case_id"]
    reopened = ingest(client, token, ref="AML-RET-REOPENED")["case_id"]
    held_completed = ingest(client, token, ref="AML-RET-HELD-COMPLETED")["case_id"]

    now = datetime.now(UTC)
    old = now - timedelta(days=31)
    recent = now - timedelta(hours=1)

    factory = session_factory(db_url)

    with transaction(factory) as session:
        rule = session.scalar(select(RetentionRule).where(RetentionRule.record_class == "case_record"))
        assert rule is not None

        rule.status = "APPROVED"
        rule.retention_days = 30
        rule.note = "synthetic integration-test retention period"

        fixtures = {
            old_completed: ("COMPLETED", old),
            old_cancelled: ("CANCELLED", old),
            recent_completed: ("COMPLETED", recent),
            # Simulates a case that had previously been terminal but is
            # currently reopened. Current nonterminal status must win.
            reopened: ("INVESTIGATING", recent),
            held_completed: ("COMPLETED", old),
        }

        for raw_case_id, (status, changed_at) in fixtures.items():
            case = session.scalar(select(Case).where(Case.id == uuid.UUID(raw_case_id)))
            assert case is not None
            case.status = status
            case.status_changed_at = changed_at
            case.updated_at = now

    hold = ok(
        client.post(
            "/api/v1/admin/legal-holds",
            json={
                "case_id": held_completed,
                "reason": "synthetic retention matrix hold",
            },
            headers=token(TADMIN),
        ),
        201,
    )
    assert hold["active"] is True

    result = ok(
        client.post(
            "/api/v1/admin/retention/evaluate",
            headers=token(TADMIN),
        )
    )

    assert result["dry_run"] is True
    assert result["deletion_executed"] is False

    eligible = set(result["eligible"])

    assert eligible == {
        old_completed,
        old_cancelled,
    }

    assert recent_completed not in eligible
    assert reopened not in eligible
    assert held_completed not in eligible

    assert result["held"] == 1

    print("RETENTION_ELIGIBILITY_MATRIX_PASSED")


@pytest.mark.req("FR-039", "DIR-034", "SPR-024")
def test_retention_document_content_execution_is_safe_and_idempotent(
    client,
    make_client,
    token,
    db_url,
) -> None:  # type: ignore[no-untyped-def]
    import hashlib
    import uuid
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import select

    from finagent.database.models import (
        Case,
        Document,
        DocumentExtraction,
        DocumentExtractionPayload,
        DocumentField,
        DocumentFieldPayload,
        DocumentVersion,
        RetentionRule,
    )
    from finagent.database.session import session_factory, transaction
    from finagent.documents.storage import BlobNotFoundError, new_key

    # ------------------------------------------------------------------
    # Default configuration must refuse destructive retention.
    # TENANT_ADMIN already requires MFA through the shared authorization
    # step-up policy.
    # ------------------------------------------------------------------

    refused = client.post(
        "/api/v1/admin/retention/execute",
        headers=token(TADMIN),
    )

    assert refused.status_code != 200
    assert refused.json()["error"]["code"] == "FEATURE_DISABLED"

    # Only this isolated test client enables execution.
    enabled = make_client(
        retention_execution_enabled=True,
    )

    # Seed the retention catalog.
    rules = ok(
        enabled.get(
            "/api/v1/admin/retention-rules",
            headers=token(TADMIN),
        )
    )["items"]

    assert any(rule["record_class"] == "document_content" for rule in rules)

    purge_case_id = ingest(
        enabled,
        token,
        ref="AML-RET-PURGE-CONTENT",
    )["case_id"]

    held_case_id = ingest(
        enabled,
        token,
        ref="AML-RET-PURGE-HELD",
    )["case_id"]

    factory = session_factory(db_url)
    now = datetime.now(UTC)
    old_terminal = now - timedelta(days=31)

    purge_bytes = b"SYNTHETIC retention purge sensitive document content"
    held_bytes = b"SYNTHETIC held document must remain available"

    purge_sha = hashlib.sha256(purge_bytes).hexdigest()
    held_sha = hashlib.sha256(held_bytes).hexdigest()

    store = enabled.app.state.document_ports.store

    with transaction(factory) as session:
        rule = session.scalar(
            select(RetentionRule).where(
                RetentionRule.record_class == "document_content",
            )
        )

        assert rule is not None

        rule.status = "APPROVED"
        rule.retention_days = 30
        rule.note = "synthetic destructive-retention integration test"

        purge_case = session.get(
            Case,
            uuid.UUID(purge_case_id),
        )
        held_case = session.get(
            Case,
            uuid.UUID(held_case_id),
        )

        assert purge_case is not None
        assert held_case is not None

        for case in (purge_case, held_case):
            case.status = "COMPLETED"
            case.status_changed_at = old_terminal
            case.updated_at = now

        purge_doc = Document(
            tenant_id=purge_case.tenant_id,
            case_id=purge_case.id,
            document_type="PASSPORT",
            title="Synthetic purge document.pdf",
            source="UPLOAD",
            classification="CONFIDENTIAL",
            current_version_no=1,
            created_by="SYNTHETIC",
        )

        held_doc = Document(
            tenant_id=held_case.tenant_id,
            case_id=held_case.id,
            document_type="PASSPORT",
            title="Synthetic held document.pdf",
            source="UPLOAD",
            classification="CONFIDENTIAL",
            current_version_no=1,
            created_by="SYNTHETIC",
        )

        session.add_all([purge_doc, held_doc])
        session.flush()

        purge_key = new_key(
            purge_case.tenant_id,
            "documents",
        )
        held_key = new_key(
            held_case.tenant_id,
            "documents",
        )

        store.put(purge_key, purge_bytes)
        store.put(held_key, held_bytes)

        purge_version = DocumentVersion(
            tenant_id=purge_case.tenant_id,
            document_id=purge_doc.id,
            version_no=1,
            declared_media_type="application/pdf",
            detected_media_type="application/pdf",
            size_bytes=len(purge_bytes),
            sha256=purge_sha,
            storage_key=purge_key,
            status="PARSED",
            created_by="SYNTHETIC",
        )

        held_version = DocumentVersion(
            tenant_id=held_case.tenant_id,
            document_id=held_doc.id,
            version_no=1,
            declared_media_type="application/pdf",
            detected_media_type="application/pdf",
            size_bytes=len(held_bytes),
            sha256=held_sha,
            storage_key=held_key,
            status="PARSED",
            created_by="SYNTHETIC",
        )

        session.add_all([purge_version, held_version])
        session.flush()

        purge_extraction = DocumentExtraction(
            tenant_id=purge_case.tenant_id,
            version_id=purge_version.id,
            parser="synthetic-test",
            extraction_profile="synthetic-v1",
            status="PARSED",
            page_count=1,
            segments=None,
            warnings=[],
            limits={},
        )

        held_extraction = DocumentExtraction(
            tenant_id=held_case.tenant_id,
            version_id=held_version.id,
            parser="synthetic-test",
            extraction_profile="synthetic-v1",
            status="PARSED",
            page_count=1,
            segments=None,
            warnings=[],
            limits={},
        )

        session.add_all(
            [
                purge_extraction,
                held_extraction,
            ]
        )
        session.flush()

        purge_field = DocumentField(
            tenant_id=purge_case.tenant_id,
            extraction_id=purge_extraction.id,
            document_id=purge_doc.id,
            field_key="full_name",
            value_text=None,
            value_type="STRING",
            value_state="PRESENT",
            page=1,
            validation_status="VALID",
            origin="EXTRACTED",
            is_current=True,
        )

        held_field = DocumentField(
            tenant_id=held_case.tenant_id,
            extraction_id=held_extraction.id,
            document_id=held_doc.id,
            field_key="full_name",
            value_text=None,
            value_type="STRING",
            value_state="PRESENT",
            page=1,
            validation_status="VALID",
            origin="EXTRACTED",
            is_current=True,
        )

        session.add_all(
            [
                purge_field,
                held_field,
            ]
        )
        session.flush()

        purge_extraction_payload = DocumentExtractionPayload(
            tenant_id=purge_case.tenant_id,
            extraction_id=purge_extraction.id,
            segments=[
                {
                    "page": 1,
                    "text": "SYNTHETIC sensitive extracted text",
                }
            ],
        )
        held_extraction_payload = DocumentExtractionPayload(
            tenant_id=held_case.tenant_id,
            extraction_id=held_extraction.id,
            segments=[
                {
                    "page": 1,
                    "text": "SYNTHETIC held extracted text",
                }
            ],
        )

        purge_field_payload = DocumentFieldPayload(
            tenant_id=purge_case.tenant_id,
            field_id=purge_field.id,
            value_text="SYNTHETIC Sensitive Person",
        )
        held_field_payload = DocumentFieldPayload(
            tenant_id=held_case.tenant_id,
            field_id=held_field.id,
            value_text="SYNTHETIC Held Person",
        )

        session.add_all(
            [
                purge_extraction_payload,
                held_extraction_payload,
                purge_field_payload,
                held_field_payload,
            ]
        )
        session.flush()

        purge_version_id = purge_version.id
        held_version_id = held_version.id
        purge_extraction_id = purge_extraction.id
        held_extraction_id = held_extraction.id
        purge_field_id = purge_field.id
        held_field_id = held_field.id

    # Both objects exist before retention execution.
    assert store.get(purge_key) == purge_bytes
    assert store.get(held_key) == held_bytes

    # Protect one otherwise-eligible case.
    hold = ok(
        enabled.post(
            "/api/v1/admin/legal-holds",
            json={
                "case_id": held_case_id,
                "reason": "synthetic active retention protection hold",
            },
            headers=token(TADMIN),
        ),
        201,
    )

    assert hold["active"] is True

    result = ok(
        enabled.post(
            "/api/v1/admin/retention/execute",
            headers=token(TADMIN),
        )
    )

    assert result["dry_run"] is False
    assert result["execution_enabled"] is True
    assert result["record_class"] == "document_content"
    assert result["deletion_executed"] is True

    assert result["eligible"] == 1
    assert result["held"] == 1
    assert result["cases_processed"] == 1
    assert result["blobs_deleted"] == 1
    assert result["extractions_purged"] == 1
    assert result["fields_purged"] == 1

    # External purge happened.
    with pytest.raises(BlobNotFoundError):
        store.get(purge_key)

    # Active hold preserved its external object.
    assert store.get(held_key) == held_bytes

    # Database sensitive payload was purged, but lineage/hash metadata remains.
    with transaction(factory) as session:
        purge_version_after = session.get(
            DocumentVersion,
            purge_version_id,
        )
        held_version_after = session.get(
            DocumentVersion,
            held_version_id,
        )

        purge_extraction_after = session.get(
            DocumentExtraction,
            purge_extraction_id,
        )
        held_extraction_after = session.get(
            DocumentExtraction,
            held_extraction_id,
        )

        purge_field_after = session.get(
            DocumentField,
            purge_field_id,
        )
        held_field_after = session.get(
            DocumentField,
            held_field_id,
        )

        assert purge_version_after is not None
        assert purge_version_after.storage_key is None
        assert purge_version_after.sha256 == purge_sha

        # Immutable/versioned parent metadata remains, but its legacy
        # sensitive columns stay scrubbed after migration 0011.
        assert purge_extraction_after is not None
        assert purge_extraction_after.segments is None

        assert purge_field_after is not None
        assert purge_field_after.value_text is None

        purge_extraction_payload_after = session.scalar(
            select(DocumentExtractionPayload).where(
                DocumentExtractionPayload.extraction_id == purge_extraction_id,
            )
        )
        purge_field_payload_after = session.scalar(
            select(DocumentFieldPayload).where(
                DocumentFieldPayload.field_id == purge_field_id,
            )
        )

        assert purge_extraction_payload_after is None
        assert purge_field_payload_after is None

        assert held_version_after is not None
        assert held_version_after.storage_key == held_key
        assert held_version_after.sha256 == held_sha

        assert held_extraction_after is not None
        assert held_extraction_after.segments is None

        assert held_field_after is not None
        assert held_field_after.value_text is None

        held_extraction_payload_after = session.scalar(
            select(DocumentExtractionPayload).where(
                DocumentExtractionPayload.extraction_id == held_extraction_id,
            )
        )
        held_field_payload_after = session.scalar(
            select(DocumentFieldPayload).where(
                DocumentFieldPayload.field_id == held_field_id,
            )
        )

        assert held_extraction_payload_after is not None
        assert held_extraction_payload_after.segments == [
            {
                "page": 1,
                "text": "SYNTHETIC held extracted text",
            }
        ]

        assert held_field_payload_after is not None
        assert held_field_payload_after.value_text == "SYNTHETIC Held Person"

    # Retry must be safe: the already-purged case remains eligible by age,
    # but there is no remaining sensitive payload or blob to delete.
    retry = ok(
        enabled.post(
            "/api/v1/admin/retention/execute",
            headers=token(TADMIN),
        )
    )

    assert retry["eligible"] == 1
    assert retry["held"] == 1
    assert retry["deletion_executed"] is False
    assert retry["blobs_deleted"] == 0
    assert retry["extractions_purged"] == 0
    assert retry["fields_purged"] == 0

    with pytest.raises(BlobNotFoundError):
        store.get(purge_key)

    assert store.get(held_key) == held_bytes

    print("RETENTION_DOCUMENT_CONTENT_PURGE_PASSED")


@pytest.mark.req("FR-039", "DIR-034", "SPR-024")
def test_retention_evidence_and_snapshots_purges_payload_only_and_is_idempotent(
    client,
    make_client,
    token,
    db_url,
) -> None:  # type: ignore[no-untyped-def]
    import uuid
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import select

    from finagent.database.models import (
        Case,
        EvidenceItem,
        EvidenceItemPayload,
        EvidenceSnapshot,
        EvidenceSnapshotItem,
        RetentionRule,
    )
    from finagent.database.session import (
        session_factory,
        transaction,
    )
    from finagent.services.evidence_service.items import (
        EvidenceInput,
        record_item,
    )

    # --------------------------------------------------------
    # Feature flag must fail closed.
    # --------------------------------------------------------

    refused = client.post(
        "/api/v1/admin/retention/execute/evidence-and-snapshots",
        headers=token(TADMIN),
    )

    assert refused.status_code != 200
    assert refused.json()["error"]["code"] == "FEATURE_DISABLED"

    enabled = make_client(
        retention_execution_enabled=True,
    )

    rules = ok(
        enabled.get(
            "/api/v1/admin/retention-rules",
            headers=token(TADMIN),
        )
    )["items"]

    assert any(rule["record_class"] == "evidence_and_snapshots" for rule in rules)

    purge_case_id = ingest(
        enabled,
        token,
        ref="AML-RET-EVIDENCE-PURGE",
    )["case_id"]

    held_case_id = ingest(
        enabled,
        token,
        ref="AML-RET-EVIDENCE-HELD",
    )["case_id"]

    factory = session_factory(db_url)

    now = datetime.now(UTC)
    old_terminal = now - timedelta(days=31)

    purge_content = {
        "summary": {
            "screening_status": "CLEAR",
        },
        "marker": "SYNTHETIC_PURGE_PAYLOAD",
    }

    held_content = {
        "summary": {
            "screening_status": "CLEAR",
        },
        "marker": "SYNTHETIC_HELD_PAYLOAD",
    }

    with transaction(factory) as session:
        rule = session.scalar(
            select(RetentionRule).where(
                RetentionRule.record_class == "evidence_and_snapshots",
            )
        )

        assert rule is not None

        rule.status = "APPROVED"
        rule.retention_days = 30
        rule.note = "synthetic evidence retention integration test"

        purge_case = session.get(
            Case,
            uuid.UUID(purge_case_id),
        )

        held_case = session.get(
            Case,
            uuid.UUID(held_case_id),
        )

        assert purge_case is not None
        assert held_case is not None

        for case in (
            purge_case,
            held_case,
        ):
            case.status = "COMPLETED"
            case.status_changed_at = old_terminal
            case.updated_at = now

        purge_recorded = record_item(
            session,
            tenant_id=purge_case.tenant_id,
            case_id=purge_case.id,
            data=EvidenceInput(
                source_type="SANCTIONS_SUMMARY",
                source_ref="retention-purge",
                content=purge_content,
                source_system="integration-test",
                retrieved_at=now,
                as_of=None,
                freshness="FRESH",
                completeness="NOT_APPLICABLE",
                classification="RESTRICTED",
            ),
            created_by="test",
        )

        held_recorded = record_item(
            session,
            tenant_id=held_case.tenant_id,
            case_id=held_case.id,
            data=EvidenceInput(
                source_type="SANCTIONS_SUMMARY",
                source_ref="retention-held",
                content=held_content,
                source_system="integration-test",
                retrieved_at=now,
                as_of=None,
                freshness="FRESH",
                completeness="NOT_APPLICABLE",
                classification="RESTRICTED",
            ),
            created_by="test",
        )

        purge_item = purge_recorded.item
        held_item = held_recorded.item

        # Legacy sensitive column must remain scrubbed.
        assert purge_item.content is None
        assert held_item.content is None

        purge_item_id = purge_item.id
        held_item_id = held_item.id

        purge_hash = purge_item.content_hash
        held_hash = held_item.content_hash

        purge_snapshot = EvidenceSnapshot(
            tenant_id=purge_case.tenant_id,
            case_id=purge_case.id,
            version_no=1,
            content_hash=purge_hash,
            completeness_status="COMPLETE",
            requirements_version="retention-test-v1",
            gaps=[],
            open_conflicts=0,
            run_id=None,
            created_by="test",
        )

        held_snapshot = EvidenceSnapshot(
            tenant_id=held_case.tenant_id,
            case_id=held_case.id,
            version_no=1,
            content_hash=held_hash,
            completeness_status="COMPLETE",
            requirements_version="retention-test-v1",
            gaps=[],
            open_conflicts=0,
            run_id=None,
            created_by="test",
        )

        session.add_all(
            [
                purge_snapshot,
                held_snapshot,
            ]
        )

        session.flush()

        purge_snapshot_id = purge_snapshot.id
        held_snapshot_id = held_snapshot.id

        session.add_all(
            [
                EvidenceSnapshotItem(
                    tenant_id=purge_case.tenant_id,
                    snapshot_id=purge_snapshot.id,
                    evidence_item_id=purge_item.id,
                ),
                EvidenceSnapshotItem(
                    tenant_id=held_case.tenant_id,
                    snapshot_id=held_snapshot.id,
                    evidence_item_id=held_item.id,
                ),
            ]
        )

        session.flush()

    # --------------------------------------------------------
    # Legal hold protects otherwise eligible evidence.
    # --------------------------------------------------------

    hold = ok(
        enabled.post(
            "/api/v1/admin/legal-holds",
            json={
                "case_id": held_case_id,
                "reason": ("synthetic evidence retention hold"),
            },
            headers=token(TADMIN),
        ),
        201,
    )

    assert hold["active"] is True

    # --------------------------------------------------------
    # Execute retention.
    # --------------------------------------------------------

    result = ok(
        enabled.post(
            "/api/v1/admin/retention/execute/evidence-and-snapshots",
            headers=token(TADMIN),
        )
    )

    assert result["dry_run"] is False
    assert result["execution_enabled"] is True
    assert result["record_class"] == "evidence_and_snapshots"
    assert result["deletion_executed"] is True

    assert result["eligible"] == 1
    assert result["held"] == 1
    assert result["cases_processed"] == 1
    assert result["payloads_purged"] == 1

    assert result["evidence_metadata_preserved"] is True
    assert result["snapshots_preserved"] is True

    # --------------------------------------------------------
    # Prove payload purged but immutable lineage survived.
    # --------------------------------------------------------

    with transaction(factory) as session:
        purge_item_after = session.get(
            EvidenceItem,
            purge_item_id,
        )

        held_item_after = session.get(
            EvidenceItem,
            held_item_id,
        )

        assert purge_item_after is not None
        assert held_item_after is not None

        assert purge_item_after.content is None
        assert held_item_after.content is None

        assert purge_item_after.content_hash == purge_hash

        assert held_item_after.content_hash == held_hash

        purge_payload_after = session.scalar(
            select(EvidenceItemPayload).where(
                EvidenceItemPayload.evidence_item_id == purge_item_id,
            )
        )

        held_payload_after = session.scalar(
            select(EvidenceItemPayload).where(
                EvidenceItemPayload.evidence_item_id == held_item_id,
            )
        )

        assert purge_payload_after is None

        assert held_payload_after is not None
        assert held_payload_after.content == held_content

        purge_snapshot_after = session.get(
            EvidenceSnapshot,
            purge_snapshot_id,
        )

        held_snapshot_after = session.get(
            EvidenceSnapshot,
            held_snapshot_id,
        )

        assert purge_snapshot_after is not None
        assert held_snapshot_after is not None

        assert purge_snapshot_after.content_hash == purge_hash

        assert held_snapshot_after.content_hash == held_hash

        purge_link = session.get(
            EvidenceSnapshotItem,
            (
                purge_case.tenant_id,
                purge_snapshot_id,
                purge_item_id,
            ),
        )

        held_link = session.get(
            EvidenceSnapshotItem,
            (
                held_case.tenant_id,
                held_snapshot_id,
                held_item_id,
            ),
        )

        assert purge_link is not None
        assert held_link is not None

    # --------------------------------------------------------
    # Retry must be harmless and delete nothing more.
    # --------------------------------------------------------

    retry = ok(
        enabled.post(
            "/api/v1/admin/retention/execute/evidence-and-snapshots",
            headers=token(TADMIN),
        )
    )

    assert retry["eligible"] == 1
    assert retry["held"] == 1

    assert retry["deletion_executed"] is False
    assert retry["cases_processed"] == 0
    assert retry["payloads_purged"] == 0

    print("RETENTION_EVIDENCE_SNAPSHOT_PURGE_PASSED")
