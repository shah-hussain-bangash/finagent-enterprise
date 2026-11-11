"""Documents and evidence end to end over HTTP + PostgreSQL as finagent_app (P6).

FR-013..016, DIR-014, DIR-025, AER-017, SPR-011, SPR-012, SPR-017, FR-006, MST:SEC-001.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import delete, select, text, update

from finagent.database.models import (
    AuditEvent,
    Document,
    DocumentExtraction,
    DocumentExtractionPayload,
    DocumentField,
    DocumentFieldPayload,
    DocumentVersion,
    EvidenceItem,
    EvidenceItemPayload,
    EvidenceSnapshot,
    QuarantineRecord,
)
from finagent.database.seeds import sid
from finagent.database.session import session_factory, transaction
from finagent.documents.runtime import sandbox_config
from finagent.integrations.factory import ConnectorFactory
from finagent.integrations.tool_gateway.gateway import ExecutionContext, ToolGateway
from finagent.security.authentication.resolver import resolve_principal_by_id
from finagent.services.document_service.processing import ProcessingConfig, process_pending
from finagent.services.evidence_service.gathering import gather_case_evidence, process_case_documents
from finagent.services.evidence_service.items import EvidenceInput, current_items, record_item
from finagent.services.evidence_service.snapshots import create_snapshot
from tests import doc_fixtures as fx
from tests.helpers import assigned_analyst, audit_actions, ingest, principal, scalar_count, subject

pytestmark = pytest.mark.integration
SUP = subject("a", "supervisor1")
PDF = "application/pdf"


def upload(
    client: Any,
    token: Any,
    who: str,
    case_id: str,
    data: Any,
    *,
    doc_type: str = "PASSPORT",
    ctype: str = PDF,
    filename: str = "passport.pdf",
    key: str | None = None,
) -> Any:
    return client.post(
        "/api/v1/documents",
        params={"case_id": case_id, "document_type": doc_type, "filename": filename},
        content=data,
        headers={**token(who), "Content-Type": ctype, "Idempotency-Key": key or f"up-{uuid.uuid4()}"},
    )


def process(client: Any, settings: Any, **over: Any) -> list[tuple[str, str]]:
    cfg = ProcessingConfig(sandbox_config(settings), over.get("max_attempts", settings.document_max_attempts))
    factory = session_factory(settings.database_url.get_secret_value())
    return process_pending(factory, client.app.state.document_ports.store, cfg, limit=50)


@pytest.fixture
def case(client: Any, token: Any) -> tuple[str, str]:
    case_id = ingest(client, token)["case_id"]
    return case_id, assigned_analyst(client, token, case_id)


# ------------------------------------------------------------------ ingestion and processing
@pytest.mark.req("FR-013", "FR-014", "AER-017", "DIR-014", "FR-036")
def test_upload_scan_parse_with_provenance(client, token, settings, db_url, case) -> None:  # type: ignore[no-untyped-def]
    case_id, analyst = case
    r = upload(client, token, analyst, case_id, fx.passport_pdf(), filename="../../etc/passport.pdf")
    assert r.status_code == 201, r.text
    doc = r.json()
    assert doc["status"] == "STORED" and doc["title"] == "passport.pdf"  # path stripped from the name
    assert doc["versions"][0]["scan_result"] == "CLEAN" and "NOT-AV" in doc["versions"][0]["scan_engine"]
    assert process(client, settings) == [(doc["versions"][0]["version_id"], "PARSED")]
    got = client.get(f"/api/v1/documents/{doc['document_id']}", headers=token(analyst)).json()
    assert got["status"] == "PARSED" and got["versions"][0]["extraction"]["page_count"] == 1
    with transaction(session_factory(settings.database_url.get_secret_value())) as session:
        extraction = session.scalar(
            select(DocumentExtraction).where(
                DocumentExtraction.version_id == uuid.UUID(doc["versions"][0]["version_id"])
            )
        )
        assert extraction is not None

        extraction_payload = session.scalar(
            select(DocumentExtractionPayload).where(
                DocumentExtractionPayload.tenant_id == extraction.tenant_id,
                DocumentExtractionPayload.extraction_id == extraction.id,
            )
        )
        assert extraction_payload is not None
        assert extraction.segments is None
        assert extraction_payload.segments

        for field_view_row in got["fields"]:
            field_payload = session.scalar(
                select(DocumentFieldPayload).where(
                    DocumentFieldPayload.tenant_id == extraction.tenant_id,
                    DocumentFieldPayload.field_id == uuid.UUID(field_view_row["field_id"]),
                )
            )
            assert field_payload is not None
            assert field_payload.value_text == field_view_row["value"]

        evidence_item = session.scalar(
            select(EvidenceItem).where(
                EvidenceItem.tenant_id == extraction.tenant_id,
                EvidenceItem.case_id == uuid.UUID(case_id),
                EvidenceItem.source_type == "DOCUMENT_EXTRACTION",
                EvidenceItem.source_ref == doc["document_id"],
                EvidenceItem.status == "CURRENT",
            )
        )
        assert evidence_item is not None

        evidence_payload = session.scalar(
            select(EvidenceItemPayload).where(
                EvidenceItemPayload.tenant_id == evidence_item.tenant_id,
                EvidenceItemPayload.evidence_item_id == evidence_item.id,
            )
        )
        assert evidence_payload is not None
        assert evidence_item.content is None
        assert evidence_payload.content

        limits = extraction.limits
    assert limits["network_limit_enforced"] is True
    assert limits["memory_limit_enforced"] is True
    assert limits["process_limit_enforced"] is True
    assert limits["isolation_method"] == "linux-seccomp-v1"
    fields = {f["field_key"]: f for f in got["fields"]}
    assert fields["date_of_birth"]["value"] == "1985-04-12" and fields["date_of_birth"]["page"] == 1
    assert (
        fields["full_name"]["origin"] == "EXTRACTED"
        and fields["full_name"]["validation_status"] == "UNVERIFIED"
    )
    ev = client.get(f"/api/v1/cases/{case_id}/evidence", headers=token(analyst)).json()
    item = next(i for i in ev["items"] if i["source_type"] == "DOCUMENT_EXTRACTION")
    assert item["source_ref"] == doc["document_id"] and item["content"]["status"] == "PARSED"
    actions = audit_actions(db_url, case_id=case_id)
    assert {"DOCUMENT_STORED", "DOCUMENT_PROCESSED", "EVIDENCE_RECORDED"} <= set(actions)


@pytest.mark.req("FR-032", "DIR-027", "FR-013")
def test_upload_is_idempotent_and_conflicts_on_changed_payload(client, token, db_url, case) -> None:  # type: ignore[no-untyped-def]
    case_id, analyst = case
    no_key = client.post(
        "/api/v1/documents",
        params={"case_id": case_id, "document_type": "PASSPORT", "filename": "p.pdf"},
        content=fx.passport_pdf(),
        headers={**token(analyst), "Content-Type": PDF},
    )
    assert no_key.status_code == 400
    first = upload(client, token, analyst, case_id, fx.passport_pdf(), key="upload-key-0001")
    again = upload(client, token, analyst, case_id, fx.passport_pdf(), key="upload-key-0001")
    assert first.status_code == again.status_code == 201
    assert first.json()["document_id"] == again.json()["document_id"]
    assert scalar_count(db_url, Document) == 1
    changed = upload(client, token, analyst, case_id, fx.passport_pdf(name="Other"), key="upload-key-0001")
    assert changed.status_code == 409 and changed.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"


@pytest.mark.req("FR-013", "DIR-014", "SPR-017")
@pytest.mark.release_blocker
def test_byte_limit_holds_with_and_without_content_length(make_client, token, db_url) -> None:  # type: ignore[no-untyped-def]
    small = make_client(document_max_bytes=4096)
    case_id = ingest(small, token)["case_id"]
    analyst = assigned_analyst(small, token, case_id)
    big = b"a,b\n" + b"1,2\n" * 2000
    declared = upload(small, token, analyst, case_id, big, doc_type="BANK_STATEMENT", ctype="text/csv")
    assert declared.status_code == 413
    chunked = upload(
        small,
        token,
        analyst,
        case_id,
        iter([big[:3000], big[3000:]]),
        doc_type="BANK_STATEMENT",
        ctype="text/csv",
    )
    assert chunked.status_code == 413, "a missing Content-Length must not bypass the limit"
    assert scalar_count(db_url, Document) == 0


@pytest.mark.req("FR-013", "DIR-014")
def test_unsupported_type_is_rejected_without_storing(client, token, db_url, case) -> None:  # type: ignore[no-untyped-def]
    case_id, analyst = case
    r = upload(client, token, analyst, case_id, b"\x89PNG\r\n\x1a\n", ctype="image/png", filename="x.png")
    assert r.status_code == 415 and r.json()["error"]["code"] == "UNSUPPORTED_MEDIA_TYPE"
    assert scalar_count(db_url, Document) == 0


@pytest.mark.req("FR-013", "DIR-014", "DIR-025", "AER-017", "SPR-012")
@pytest.mark.release_blocker
def test_suspicious_files_are_quarantined_never_parsed_or_downloadable(
    client, token, settings, db_url, case
) -> None:  # type: ignore[no-untyped-def]
    case_id, analyst = case
    cases = [
        (fx.statement_csv(), PDF, "SIGNATURE_MISMATCH"),
        (fx.eicar_csv(), "text/csv", "MALWARE_DETECTED"),
        (
            fx.macro_docx(),
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "ACTIVE_CONTENT_MACROS",
        ),
    ]
    for data, ctype, reason in cases:
        r = upload(client, token, analyst, case_id, data, ctype=ctype, doc_type="OTHER", filename="f")
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["status"] == "QUARANTINED" and body["versions"][0]["status_reason"] == reason
        assert body["versions"][0]["quarantine"]["owner_role"] == "AML_SUPERVISOR"
        link = client.get(f"/api/v1/documents/{body['document_id']}/download-link", headers=token(analyst))
        assert link.status_code == 409 and link.json()["error"]["code"] == "RESOURCE_UNAVAILABLE"
    assert process(client, settings) == []  # nothing quarantined is ever handed to a parser
    assert scalar_count(db_url, QuarantineRecord, status="OPEN") == 3
    assert audit_actions(db_url, case_id=case_id).count("DOCUMENT_QUARANTINED") == 3


@pytest.mark.req("FR-013", "AER-017", "DIR-014")
@pytest.mark.release_blocker
def test_encrypted_and_active_pdfs_are_quarantined_by_the_parser(client, token, settings, case) -> None:  # type: ignore[no-untyped-def]
    case_id, analyst = case
    enc = upload(client, token, analyst, case_id, fx.encrypted_pdf()).json()
    js = upload(client, token, analyst, case_id, fx.javascript_pdf(), doc_type="OTHER").json()
    process(client, settings)
    for doc, reason in ((enc, "ENCRYPTED_FILE"), (js, "ACTIVE_CONTENT")):
        got = client.get(f"/api/v1/documents/{doc['document_id']}", headers=token(analyst)).json()
        assert got["status"] == "QUARANTINED" and got["versions"][0]["status_reason"] == reason
        assert got["versions"][0]["downloadable"] is False


@pytest.mark.req("FR-013", "FR-014", "DIR-025", "DIR-009", "AER-017", "FR-015")
def test_unreadable_files_preserve_source_and_accept_corrected_versions(
    client, token, settings, case
) -> None:  # type: ignore[no-untyped-def]
    case_id, analyst = case
    bad = upload(client, token, analyst, case_id, b"%PDF-1.4 truncated garbage").json()
    scanned = upload(client, token, analyst, case_id, fx.blank_pdf(), doc_type="PROOF_OF_ADDRESS").json()
    process(client, settings)
    got = client.get(f"/api/v1/documents/{bad['document_id']}", headers=token(analyst)).json()
    assert got["status"] == "PARSE_FAILED" and got["versions"][0]["next_attempt_at"] is None  # permanent
    assert got["versions"][0]["next_action"] and got["versions"][0]["downloadable"] is True  # source kept
    ocr = client.get(f"/api/v1/documents/{scanned['document_id']}", headers=token(analyst)).json()
    assert ocr["status"] == "OCR_UNAVAILABLE"
    assert {f["value_state"] for f in ocr["fields"]} == {"EXTRACTION_FAILED"}
    gaps = client.get(f"/api/v1/cases/{case_id}/evidence", headers=token(analyst)).json()["gaps"]
    assert {"DOCUMENT_NOT_READABLE", "REQUIRED_FIELD_MISSING"} <= {g["code"] for g in gaps}
    fixed = client.post(
        f"/api/v1/documents/{bad['document_id']}/versions",
        content=fx.passport_pdf(),
        headers={**token(analyst), "Content-Type": PDF, "Idempotency-Key": f"v-{uuid.uuid4()}"},
    )
    assert fixed.status_code == 201, fixed.text
    process(client, settings)
    got = client.get(f"/api/v1/documents/{bad['document_id']}", headers=token(analyst)).json()
    assert [(v["version_no"], v["status"]) for v in got["versions"]] == [(2, "PARSED"), (1, "PARSE_FAILED")]


@pytest.mark.req("FR-013", "NQ-023", "FR-033")
def test_transient_parser_failures_retry_with_a_bound(make_client, make_settings, token, db_url) -> None:  # type: ignore[no-untyped-def]
    settings = make_settings(parser_timeout_seconds=0.001, document_max_attempts=2)
    c = make_client(parser_timeout_seconds=0.001, document_max_attempts=2)
    case_id = ingest(c, token)["case_id"]
    analyst = assigned_analyst(c, token, case_id)
    doc = upload(c, token, analyst, case_id, fx.passport_pdf()).json()
    vid = uuid.UUID(doc["versions"][0]["version_id"])
    process(c, settings)
    with transaction(session_factory(db_url)) as s:
        v = s.get(DocumentVersion, vid)
        assert v is not None and v.status == "PARSE_FAILED" and v.next_attempt_at is not None
        assert v.attempts == 1 and v.last_error_code == "PARSER_TIMEOUT"
        s.execute(
            update(DocumentVersion).where(DocumentVersion.id == vid).values(next_attempt_at=datetime.now(UTC))
        )
    process(c, settings)
    with transaction(session_factory(db_url)) as s:
        v = s.get(DocumentVersion, vid)
        assert v is not None and v.attempts == 2 and v.next_attempt_at is None  # bounded: no third try


# ------------------------------------------------------------------ corrections (AER-017, FR-006)
@pytest.mark.req("FR-014", "AER-017", "FR-016", "FR-006", "FR-036")
def test_field_correction_keeps_original_and_versions_evidence(client, token, settings, db_url, case) -> None:  # type: ignore[no-untyped-def]
    case_id, analyst = case
    doc = upload(client, token, analyst, case_id, fx.pdf("Full name: X", "Date of birth: 03/04/1985")).json()
    process(client, settings)
    got = client.get(f"/api/v1/documents/{doc['document_id']}", headers=token(analyst)).json()
    dob = next(f for f in got["fields"] if f["field_key"] == "date_of_birth")
    assert dob["validation_status"] == "INVALID_FORMAT"
    url = f"/api/v1/documents/{doc['document_id']}/fields/{dob['field_id']}/correction"
    reviewer = subject("a", "reviewer1")
    assert client.post(
        url, json={"value": "1985-04-03", "reason": "checked original"}, headers=token(reviewer)
    ).status_code in (403, 404)
    r = client.post(
        url,
        json={"value": "1985-04-03", "reason": "confirmed against the original passport"},
        headers=token(analyst),
    )
    assert r.status_code == 201, r.text
    assert r.json()["origin"] == "CORRECTED" and r.json()["supersedes_id"] == dob["field_id"]

    corrected_id = uuid.UUID(r.json()["field_id"])

    with transaction(session_factory(db_url)) as s:
        corrected_payload = s.scalar(
            select(DocumentFieldPayload).where(DocumentFieldPayload.field_id == corrected_id)
        )
        assert corrected_payload is not None
        assert corrected_payload.value_text == "1985-04-03"

    stale = client.post(
        url, json={"value": "1985-04-04", "reason": "second edit on stale field"}, headers=token(analyst)
    )
    assert stale.status_code == 409
    got = client.get(f"/api/v1/documents/{doc['document_id']}", headers=token(analyst)).json()
    assert [f["value"] for f in got["field_history"] if f["field_key"] == "date_of_birth"] == ["03/04/1985"]
    with transaction(session_factory(db_url)) as s:
        versions = s.scalars(
            select(EvidenceItem.version_no)
            .where(EvidenceItem.source_ref == doc["document_id"])
            .order_by(EvidenceItem.version_no)
        ).all()
    assert list(versions) == [1, 2]
    assert "MATERIAL_CHANGE_RECORDED" in audit_actions(db_url, case_id=case_id)


# ------------------------------------------------------------------ download links (SPR-012)
def _forge(settings: Any, claims: dict[str, Any]) -> str:
    key = hmac.new(settings.signing_key(), b"finagent:download-links:v1", hashlib.sha256).digest()
    body = (
        base64.urlsafe_b64encode(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode())
        .decode()
        .rstrip("=")
    )
    sig = base64.urlsafe_b64encode(hmac.new(key, body.encode(), hashlib.sha256).digest()).decode().rstrip("=")
    return f"{body}.{sig}"


@pytest.mark.req("SPR-012", "SPR-011", "FR-002", "MST:SEC-001")
@pytest.mark.release_blocker
def test_download_links_are_short_lived_bound_and_rechecked(client, token, settings, db_url, case) -> None:  # type: ignore[no-untyped-def]
    case_id, analyst = case
    data = fx.passport_pdf()
    doc = upload(client, token, analyst, case_id, data).json()
    link = client.get(f"/api/v1/documents/{doc['document_id']}/download-link", headers=token(analyst)).json()
    assert datetime.fromisoformat(link["expires_at"]) - datetime.now(UTC) <= timedelta(seconds=300)
    ok = client.get(link["url"], headers=token(analyst))
    assert ok.status_code == 200 and ok.content == data
    assert ok.headers["content-disposition"] == 'attachment; filename="passport.pdf"'
    other = "a.analyst2@synthetic" if analyst == "a.analyst1@synthetic" else "a.analyst1@synthetic"
    absent = client.get("/api/v1/document-downloads/x.y", headers=token(analyst))
    for who in (other, "b.analyst1@synthetic", SUP):  # forwarded link: bound to the issuing principal
        r = client.get(link["url"], headers=token(who))
        assert r.status_code == 404 and r.json()["error"]["message"] == absent.json()["error"]["message"]
    tampered = link["url"][:-3] + ("AAA" if not link["url"].endswith("AAA") else "BBB")
    assert client.get(tampered, headers=token(analyst)).status_code == 404
    claims = json.loads(base64.urlsafe_b64decode(link["url"].rsplit("/", 1)[1].split(".")[0] + "=="))
    expired = _forge(settings, {**claims, "exp": int(datetime.now(UTC).timestamp()) - 1})
    assert client.get(f"/api/v1/document-downloads/{expired}", headers=token(analyst)).status_code == 410
    # Current authorization is re-checked: after reassignment the old analyst's link is dead.
    new_key = "analyst2" if analyst == "a.analyst1@synthetic" else "analyst1"
    r = client.post(
        f"/api/v1/cases/{case_id}/assignment",
        json={"principal_id": str(principal("a", new_key)), "role": "ANALYST", "reason": "rebalancing"},
        headers=token(SUP),
    )
    assert r.status_code == 200, r.text
    assert client.get(link["url"], headers=token(analyst)).status_code == 404
    assert audit_actions(db_url, case_id=case_id).count("DOCUMENT_DOWNLOADED") == 1


@pytest.mark.req("MST:SEC-001", "FR-002", "SPR-011")
@pytest.mark.release_blocker
def test_documents_and_evidence_are_tenant_isolated(client, token, case) -> None:  # type: ignore[no-untyped-def]
    case_id, analyst = case
    doc = upload(client, token, analyst, case_id, fx.passport_pdf()).json()
    for path in (
        f"/api/v1/documents/{doc['document_id']}",
        f"/api/v1/documents/{doc['document_id']}/download-link",
        f"/api/v1/cases/{case_id}/documents",
        f"/api/v1/cases/{case_id}/evidence",
        f"/api/v1/cases/{case_id}/snapshots",
    ):
        absent = path.replace(doc["document_id"], str(uuid.uuid4())).replace(case_id, str(uuid.uuid4()))
        for who in ("b.analyst1@synthetic", "b.supervisor1@synthetic"):
            r, a = client.get(path, headers=token(who)), client.get(absent, headers=token(who))
            # Indistinguishable from an absent id (404, or 403 when the role lacks the permission).
            assert r.status_code == a.status_code and r.status_code in (403, 404), (path, who)
            assert r.json()["error"]["message"] == a.json()["error"]["message"]
    b_case = ingest(client, token, tenant="b")["case_id"]
    b_analyst = assigned_analyst(client, token, b_case, tenant="b")
    cross = upload(client, token, b_analyst, case_id, fx.passport_pdf())  # B uploads into A's case
    assert cross.status_code == 404


# ------------------------------------------------------------------ gathering + snapshots (FR-015/016)
@pytest.fixture
def gathered(client, token, make_settings, app_db_url, bank, case):  # type: ignore[no-untyped-def]
    case_id, analyst = case
    settings = make_settings(bank_base_url=bank.url)
    factory = session_factory(app_db_url)
    with transaction(factory) as s:
        p = resolve_principal_by_id(s, sid("tenant:a"), principal("a", analyst.split(".")[1].split("@")[0]))
    ctx = ExecutionContext(
        tenant_id=sid("tenant:a"),
        case_id=uuid.UUID(case_id),
        run_id=None,
        step_id="gather",
        capability_id="SVC-EVIDENCE",
        principal=p,
        allowed_tools=frozenset(
            {
                "get_customer_profile",
                "list_kyc_documents",
                "get_transactions",
                "get_sanctions_summary",
                "fetch_kyc_document",
            }
        ),
        data_classes=frozenset({"CONFIDENTIAL", "RESTRICTED"}),
    )
    gw = ToolGateway(factory, ConnectorFactory(settings, factory), settings)
    ports = client.app.state.document_ports
    cfg = ProcessingConfig(sandbox_config(settings), settings.document_max_attempts)

    def run() -> dict[str, Any]:
        summary = gather_case_evidence(factory, gw, ctx, ["customer-test-001"], ports)
        process_case_documents(factory, ports.store, cfg, sid("tenant:a"), uuid.UUID(case_id))
        return summary.as_dict()

    return {"case_id": case_id, "analyst": analyst, "run": run, "factory": factory, "settings": settings}


def test_retention_payload_only_storage_boundary(
    client,
    token,
    settings,
    case,
    db_url,
) -> None:
    case_id, analyst = case

    body = fx.passport_pdf(
        name="PAYLOAD ONLY PERSON",
    )

    response = upload(
        client,
        token,
        analyst,
        case_id,
        body,
        filename="payload-only.pdf",
        key=f"payload-only-{uuid.uuid4()}",
    )

    assert response.status_code == 201, response.text

    document_id = uuid.UUID(response.json()["document_id"])

    factory = session_factory(db_url)

    cfg = ProcessingConfig(
        sandbox_config(settings),
        settings.document_max_attempts,
    )

    process_case_documents(
        factory,
        client.app.state.document_ports.store,
        cfg,
        sid("tenant:a"),
        uuid.UUID(case_id),
    )

    with transaction(factory) as session:
        document = session.scalar(
            select(Document).where(
                Document.id == document_id,
            )
        )

        assert document is not None

        version = session.scalar(
            select(DocumentVersion).where(
                DocumentVersion.document_id == document.id,
                DocumentVersion.version_no == document.current_version_no,
            )
        )

        assert version is not None

        extraction = session.scalar(
            select(DocumentExtraction).where(
                DocumentExtraction.version_id == version.id,
            )
        )

        assert extraction is not None
        assert extraction.segments is None

        extraction_payload = session.scalar(
            select(DocumentExtractionPayload).where(
                DocumentExtractionPayload.extraction_id == extraction.id,
            )
        )

        assert extraction_payload is not None
        assert extraction_payload.segments

        fields = list(
            session.scalars(
                select(DocumentField).where(
                    DocumentField.extraction_id == extraction.id,
                )
            )
        )

        assert fields
        assert all(field.value_text is None for field in fields)

        field_payloads = list(
            session.scalars(
                select(DocumentFieldPayload).where(
                    DocumentFieldPayload.field_id.in_([field.id for field in fields])
                )
            )
        )

        assert len(field_payloads) == len(fields)
        assert any(payload.value_text for payload in field_payloads)

        evidence = session.scalar(
            select(EvidenceItem).where(
                EvidenceItem.case_id == uuid.UUID(case_id),
                EvidenceItem.source_type == "DOCUMENT_EXTRACTION",
                EvidenceItem.source_ref == str(document.id),
                EvidenceItem.status == "CURRENT",
            )
        )

        assert evidence is not None
        assert evidence.content is None

        evidence_payload = session.scalar(
            select(EvidenceItemPayload).where(
                EvidenceItemPayload.evidence_item_id == evidence.id,
            )
        )

        assert evidence_payload is not None
        assert evidence_payload.content
        assert evidence_payload.content["document_id"] == str(document.id)


def test_snapshot_survives_purged_evidence_payload(
    gathered,
) -> None:  # type: ignore[no-untyped-def]
    g = gathered

    summary = g["run"]()
    assert summary["failures"] == []

    tenant_id = sid("tenant:a")
    case_id = uuid.UUID(g["case_id"])

    with transaction(g["factory"]) as session:
        evidence = session.scalar(
            select(EvidenceItem).where(
                EvidenceItem.tenant_id == tenant_id,
                EvidenceItem.case_id == case_id,
                EvidenceItem.source_type == "DOCUMENT_EXTRACTION",
                EvidenceItem.status == "CURRENT",
            )
        )

        assert evidence is not None
        assert evidence.content is None

        payload = session.scalar(
            select(EvidenceItemPayload).where(
                EvidenceItemPayload.tenant_id == tenant_id,
                EvidenceItemPayload.evidence_item_id == evidence.id,
            )
        )

        assert payload is not None
        assert payload.content

        session.execute(
            delete(EvidenceItemPayload).where(
                EvidenceItemPayload.tenant_id == tenant_id,
                EvidenceItemPayload.evidence_item_id == evidence.id,
            )
        )
        session.flush()

        assert (
            session.scalar(
                select(EvidenceItemPayload.id).where(
                    EvidenceItemPayload.tenant_id == tenant_id,
                    EvidenceItemPayload.evidence_item_id == evidence.id,
                )
            )
            is None
        )

        session.expire_all()

        snap, created = create_snapshot(
            session,
            tenant_id=tenant_id,
            case_id=case_id,
            created_by="retention-read-after-purge-test",
            max_age=timedelta(
                hours=g["settings"].source_max_age_hours,
            ),
        )

        assert snap is not None
        assert created is True

        raw_parent_content = session.scalar(
            select(EvidenceItem.content).where(
                EvidenceItem.tenant_id == tenant_id,
                EvidenceItem.id == evidence.id,
            )
        )

        assert raw_parent_content is None


@pytest.mark.req("FR-012", "FR-015", "FR-016", "AER-007", "AER-010", "DIR-017", "BR-004")
@pytest.mark.release_blocker
def test_gathered_evidence_snapshot_conflict_and_resolution(gathered, client, token, db_url) -> None:  # type: ignore[no-untyped-def]
    g = gathered
    summary = g["run"]()
    assert summary["failures"] == [] and summary["documents"] == 3 and summary["recorded"] == 4
    max_age = timedelta(hours=g["settings"].source_max_age_hours)
    with transaction(g["factory"]) as s:
        snap, created = create_snapshot(
            s, tenant_id=sid("tenant:a"), case_id=uuid.UUID(g["case_id"]), created_by="test", max_age=max_age
        )
        assert created and snap.completeness_status == "CONFLICTED" and snap.open_conflicts == 1
        codes = {gap["code"] for gap in snap.gaps}
        assert "KYC_DOCUMENT_NOT_CURRENT" in codes  # proof of address EXPIRED at the source
        snap_id = str(snap.id)
    ev = client.get(f"/api/v1/cases/{g['case_id']}/evidence", headers=token(g["analyst"])).json()
    conflict = ev["conflicts"][0]
    assert (
        conflict["field_key"] == "date_of_birth" and conflict["value_a"] != conflict["value_b"]
    )  # both kept
    url = f"/api/v1/cases/{g['case_id']}/evidence-conflicts/{conflict['conflict_id']}/resolution"
    body = {"resolution": "USE_A", "rationale": "core banking DOB verified at onboarding; passport scan typo"}
    assert client.post(url, json=body, headers=token("b.analyst1@synthetic")).status_code == 404
    r = client.post(url, json=body, headers=token(g["analyst"]))
    assert r.status_code == 200 and r.json()["status"] == "RESOLVED"
    assert client.post(url, json=body, headers=token(g["analyst"])).status_code == 409  # already resolved
    r = client.post(f"/api/v1/cases/{g['case_id']}/snapshots", headers=token(g["analyst"]))
    assert (
        r.status_code == 201 and r.json()["completeness_status"] == "COMPLETE" and r.json()["version_no"] == 2
    )
    again = client.post(f"/api/v1/cases/{g['case_id']}/snapshots", headers=token(g["analyst"]))
    assert (
        again.status_code == 200 and again.json()["snapshot_id"] == r.json()["snapshot_id"]
    )  # unchanged set
    old = client.get(f"/api/v1/cases/{g['case_id']}/snapshots/{snap_id}", headers=token(g["analyst"])).json()
    assert old["completeness_status"] == "CONFLICTED" and len(old["items"]) == 7  # 4 sources + 3 documents


@pytest.mark.req("FR-016", "FR-006", "DIR-018")
@pytest.mark.release_blocker
def test_snapshots_are_immutable_for_every_role(gathered, db_url, app_db_url) -> None:  # type: ignore[no-untyped-def]
    g = gathered
    g["run"]()
    with transaction(g["factory"]) as s:
        snap, _ = create_snapshot(
            s,
            tenant_id=sid("tenant:a"),
            case_id=uuid.UUID(g["case_id"]),
            created_by="t",
            max_age=timedelta(days=1),
        )
        sid_ = snap.id
    for url in (app_db_url, db_url):  # application role AND schema owner
        for stmt in (
            text("UPDATE evidence_snapshots SET completeness_status = 'COMPLETE' WHERE id = :i"),
            text("DELETE FROM evidence_snapshot_items WHERE snapshot_id = :i"),
        ):
            with (
                pytest.raises(Exception, match=r"immutable|permission denied"),
                transaction(session_factory(url)) as s,
            ):
                s.execute(stmt, {"i": sid_})


@pytest.mark.req("FR-016", "FR-006")
def test_refetch_versions_only_on_change_and_flags_material_changes(gathered, db_url) -> None:  # type: ignore[no-untyped-def]
    g = gathered
    g["run"]()
    second = g["run"]()
    assert second["material_changes"] == 0  # same content: refreshed, never "material"
    case_id = uuid.UUID(g["case_id"])
    with transaction(g["factory"]) as s:
        current = next(
            item
            for item in current_items(
                s,
                sid("tenant:a"),
                case_id,
            )
            if item.source_type == "SANCTIONS_SUMMARY"
        )
        assert current.content is not None
        content = {
            "summary": {
                **current.content["summary"],
                "screening_status": "POTENTIAL_MATCH_PENDING_REVIEW",
            }
        }
        rec = record_item(
            s,
            tenant_id=sid("tenant:a"),
            case_id=case_id,
            data=EvidenceInput(
                source_type="SANCTIONS_SUMMARY",
                source_ref=current.source_ref,
                content=content,
                source_system=current.source_system,
                retrieved_at=datetime.now(UTC),
                as_of=current.as_of,
                freshness="FRESH",
                completeness="NOT_APPLICABLE",
                classification="RESTRICTED",
            ),
            created_by="test",
        )
        assert rec.created and rec.material and rec.item.change_reason == "CONTENT_CHANGED"

        payload = s.scalar(
            select(EvidenceItemPayload).where(
                EvidenceItemPayload.tenant_id == rec.item.tenant_id,
                EvidenceItemPayload.evidence_item_id == rec.item.id,
            )
        )
        assert payload is not None
        assert rec.item.content is None
        assert payload.content == content

        payload_count_before = len(
            s.scalars(
                select(EvidenceItemPayload).where(
                    EvidenceItemPayload.tenant_id == rec.item.tenant_id,
                    EvidenceItemPayload.evidence_item_id == rec.item.id,
                )
            ).all()
        )

        same = record_item(
            s,
            tenant_id=sid("tenant:a"),
            case_id=case_id,
            data=EvidenceInput(
                source_type="SANCTIONS_SUMMARY",
                source_ref=rec.item.source_ref,
                content=content,
                source_system=rec.item.source_system,
                retrieved_at=datetime.now(UTC),
                as_of=rec.item.as_of,
                freshness="FRESH",
                completeness="NOT_APPLICABLE",
                classification="RESTRICTED",
            ),
            created_by="test",
        )

        assert same.created is False
        assert same.material is False
        assert same.item.id == rec.item.id

        payload_count_after = len(
            s.scalars(
                select(EvidenceItemPayload).where(
                    EvidenceItemPayload.tenant_id == rec.item.tenant_id,
                    EvidenceItemPayload.evidence_item_id == rec.item.id,
                )
            ).all()
        )

        assert payload_count_before == 1
        assert payload_count_after == 1

    with transaction(session_factory(db_url)) as s:
        assert (
            s.scalar(select(AuditEvent.id).where(AuditEvent.action == "MATERIAL_CHANGE_RECORDED")) is not None
        )
    assert scalar_count(db_url, EvidenceSnapshot) == 0  # gathering never freezes by itself


@pytest.mark.req("FR-013", "FR-014", "AER-017")
@pytest.mark.integration
def test_ocr_result_persists_extraction_and_field_provenance(client, token, settings, case) -> None:  # type: ignore[no-untyped-def]
    """OCR output must persist through extraction with page provenance."""

    from finagent.documents import parsers
    from finagent.documents.ocr import OcrPage, OcrResult

    class SyntheticOcr:
        def extract(
            self,
            media_type: str,
            data: bytes,
            *,
            max_pages: int,
        ) -> OcrResult:
            del data

            assert media_type == "application/pdf"
            assert max_pages >= 1

            return OcrResult(
                status=parsers.PARSED,
                provider="synthetic-test",
                pages=(
                    OcrPage(
                        page=1,
                        text="Full name: SYNTHETIC OCR PERSON 001",
                    ),
                ),
            )

    case_id, analyst = case

    scanned = upload(
        client,
        token,
        analyst,
        case_id,
        fx.blank_pdf(),
        doc_type="PASSPORT",
    ).json()

    factory = session_factory(settings.database_url.get_secret_value())

    cfg = ProcessingConfig(
        sandbox_config(settings),
        settings.document_max_attempts,
        ocr=SyntheticOcr(),
    )

    processed = process_pending(
        factory,
        client.app.state.document_ports.store,
        cfg,
        limit=50,
    )

    assert processed

    got = client.get(
        f"/api/v1/documents/{scanned['document_id']}",
        headers=token(analyst),
    ).json()

    assert got["status"] == "PARSED"

    version = got["versions"][0]

    assert version["status"] == "PARSED"
    assert version["downloadable"] is True

    full_name = next(field for field in got["fields"] if field["field_key"] == "full_name")

    assert full_name["value"] == "SYNTHETIC OCR PERSON 001"
    assert full_name["page"] == 1
    assert full_name["value_state"] != "EXTRACTION_FAILED"

    with transaction(factory) as session:
        extraction = session.scalar(
            select(DocumentExtraction).where(
                DocumentExtraction.version_id == uuid.UUID(version["version_id"])
            )
        )

        assert extraction is not None
        assert extraction.status == parsers.PARSED
        assert extraction.parser == "ocr:synthetic-test"
        assert extraction.page_count == 1

    print("OCR_PERSISTENCE_ACCEPTANCE_PASSED")


def test_retention_payload_content_wins_over_legacy_parent(
    db_url: str,
    case: tuple[str, str],
) -> None:
    tenant_id = sid("tenant:a")
    case_id = uuid.UUID(case[0])

    legacy_content = {
        "marker": "LEGACY_PARENT_CONTENT",
        "value": 111,
    }

    payload_content = {
        "marker": "PAYLOAD_TABLE_CONTENT",
        "value": 999,
    }

    with transaction(session_factory(db_url)) as session:
        recorded = record_item(
            session,
            tenant_id=tenant_id,
            case_id=case_id,
            data=EvidenceInput(
                source_type="SANCTIONS_SUMMARY",
                source_ref="payload-precedence",
                content=legacy_content,
                source_system="integration-test",
                retrieved_at=datetime.now(UTC),
                as_of=None,
                freshness="FRESH",
                completeness="NOT_APPLICABLE",
                classification="RESTRICTED",
            ),
            created_by="test",
        )

        item_id = recorded.item.id

        payload = session.scalar(
            select(EvidenceItemPayload).where(
                EvidenceItemPayload.tenant_id == tenant_id,
                EvidenceItemPayload.evidence_item_id == item_id,
            )
        )

        assert payload is not None

        payload.content = payload_content
        session.flush()

        raw_parent_content = session.scalar(
            select(EvidenceItem.content).where(
                EvidenceItem.tenant_id == tenant_id,
                EvidenceItem.id == item_id,
            )
        )

        assert raw_parent_content is None

        loaded = current_items(
            session,
            tenant_id,
            case_id,
        )

        item = next(row for row in loaded if row.id == item_id)

        assert item.content == payload_content

        # Payload hydration must not dirty the immutable parent.
        assert item not in session.dirty

        session.flush()

        raw_parent_after_flush = session.scalar(
            select(EvidenceItem.content).where(
                EvidenceItem.tenant_id == tenant_id,
                EvidenceItem.id == item_id,
            )
        )

        assert raw_parent_after_flush is None


def test_retention_document_payloads_win_over_legacy_parents(
    client: Any,
    token: Any,
    settings: Any,
    db_url: str,
    case: tuple[str, str],
) -> None:
    case_id, analyst = case

    uploaded = upload(
        client,
        token,
        analyst,
        case_id,
        fx.pdf(
            "Full name: LEGACY DOCUMENT PERSON",
            "Date of birth: 03/04/1985",
        ),
        filename="retention-payload-precedence.pdf",
    )

    assert uploaded.status_code == 201, uploaded.text

    doc = uploaded.json()

    processed = process(
        client,
        settings,
    )

    assert (
        doc["versions"][0]["version_id"],
        "PARSED",
    ) in processed

    document_id = doc["document_id"]
    version_id = uuid.UUID(doc["versions"][0]["version_id"])

    before = client.get(
        f"/api/v1/documents/{document_id}",
        headers=token(analyst),
    )

    assert before.status_code == 200, before.text

    before_view = before.json()

    assert before_view["fields"]

    target_field_view = before_view["fields"][0]
    target_field_id = uuid.UUID(target_field_view["field_id"])

    payload_segments = [
        {
            "page": 77,
            "text": "PAYLOAD SEGMENT PRECEDENCE MARKER",
        }
    ]

    payload_field_value = "PAYLOAD FIELD PRECEDENCE MARKER"

    factory = session_factory(db_url)

    with transaction(factory) as session:
        extraction = session.scalar(
            select(DocumentExtraction).where(DocumentExtraction.version_id == version_id)
        )

        assert extraction is not None

        extraction_id = extraction.id
        tenant_id = extraction.tenant_id

        legacy_segments = extraction.segments

        extraction_payload = session.scalar(
            select(DocumentExtractionPayload).where(
                DocumentExtractionPayload.tenant_id == tenant_id,
                DocumentExtractionPayload.extraction_id == extraction_id,
            )
        )

        assert extraction_payload is not None

        field = session.scalar(
            select(DocumentField).where(
                DocumentField.tenant_id == tenant_id,
                DocumentField.id == target_field_id,
            )
        )

        assert field is not None

        legacy_field_value = field.value_text

        field_payload = session.scalar(
            select(DocumentFieldPayload).where(
                DocumentFieldPayload.tenant_id == tenant_id,
                DocumentFieldPayload.field_id == target_field_id,
            )
        )

        assert field_payload is not None

        extraction_payload.segments = payload_segments
        field_payload.value_text = payload_field_value

        session.flush()

        # Confirm the immutable / legacy parent records have
        # not been changed by updating their payload rows.
        parent_segments = session.scalar(
            select(DocumentExtraction.segments).where(
                DocumentExtraction.tenant_id == tenant_id,
                DocumentExtraction.id == extraction_id,
            )
        )

        parent_field_value = session.scalar(
            select(DocumentField.value_text).where(
                DocumentField.tenant_id == tenant_id,
                DocumentField.id == target_field_id,
            )
        )

        assert parent_segments == legacy_segments
        assert parent_field_value == legacy_field_value

    after = client.get(
        f"/api/v1/documents/{document_id}",
        headers=token(analyst),
    )

    assert after.status_code == 200, after.text

    after_view = after.json()

    extraction_view = after_view["versions"][0]["extraction"]

    assert extraction_view["segments"] == payload_segments

    field_view_after = next(row for row in after_view["fields"] if row["field_id"] == str(target_field_id))

    assert field_view_after["value"] == payload_field_value

    # Re-read the legacy parent values after the API GET.
    # This proves payload hydration/read preference did not
    # dirty or overwrite the legacy records.
    with transaction(factory) as session:
        parent_segments_after = session.scalar(
            select(DocumentExtraction.segments).where(
                DocumentExtraction.tenant_id == tenant_id,
                DocumentExtraction.id == extraction_id,
            )
        )

        parent_field_after = session.scalar(
            select(DocumentField.value_text).where(
                DocumentField.tenant_id == tenant_id,
                DocumentField.id == target_field_id,
            )
        )

        assert parent_segments_after == legacy_segments
        assert parent_field_after == legacy_field_value
