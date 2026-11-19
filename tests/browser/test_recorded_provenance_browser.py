"""Exercise document provenance through the disposable synthetic browser stack."""

import pytest

from finagent.agents.runner import run_simple
from tests.agent_helpers import prepared_case
from tests.browser.test_ui_journey import sign_in, stack  # noqa: F401

pytestmark = [pytest.mark.browser, pytest.mark.integration]


@pytest.fixture(autouse=True)
def local_web_auth(monkeypatch):
    monkeypatch.setenv("FINAGENT_WEB_AUTH_MODE", "local")
    monkeypatch.setenv("FINAGENT_ENVIRONMENT", "local")


def test_document_provenance_requires_case_access(stack, make_client, make_settings, token, bank):  # noqa: F811
    from playwright.sync_api import sync_playwright

    prep = prepared_case(make_client(bank_base_url=bank.url), token, make_settings(bank_base_url=bank.url))
    scope, _ = prep.scope()
    result = run_simple("AG-KYC", prep.facts(), scope)
    output_response = prep.client.get(
        f"/api/v1/cases/{prep.case_id}/agent-outputs", headers=token(prep.analyst)
    )
    assert output_response.status_code == 200
    output = next(
        item for item in output_response.json()["items"] if item["output_id"] == str(result.output_id)
    )
    selection = output["lineage"]["document_selection"]
    assert selection["rule_version"] == "kyc-document-selection-0.1-SYNTHETIC"
    snapshot = prep.client.get(
        f"/api/v1/cases/{prep.case_id}/snapshots/{prep.snapshot_id}", headers=token(prep.analyst)
    )
    assert snapshot.status_code == 200
    snapshot_refs = {f"ev:{item['evidence_item_id']}" for item in snapshot.json()["items"]}
    for selected in selection["items"]:
        if selected["selection"] == "SELECTED":
            assert selected["evidence_ref"] in snapshot_refs
    response = prep.client.get(f"/api/v1/cases/{prep.case_id}/documents", headers=token(prep.analyst))
    assert response.status_code == 200
    documents = response.json()["items"]
    assert documents
    doc = documents[0]
    web = stack["web"]
    path = f"{web}/workspace/recorded-provenance?case={prep.case_id}&document={doc['document_id']}"
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            page.goto(path)
            assert "/signin" in page.url
            sign_in(page, web, prep.analyst)
            page.goto(path)
            main = page.locator("main")
            assert main.get_by_role("heading", name="Recorded versions").count() == 1
            assert main.get_by_role("heading", name="Current extracted fields").count() == 1
            assert main.get_by_role("heading", name="Recorded KYC checklist findings").count() == 1
            assert "Checklist item" in main.inner_text()
            assert str(prep.snapshot_id) in main.inner_text()
            assert "kyc-document-selection-0.1-SYNTHETIC" in main.inner_text()
            assert "Selection does not verify authenticity" in main.inner_text()
            assert main.get_by_role("link", name="Open current case gaps and evidence").count() == 1
            main.get_by_role("link", name="Inspect this recorded selection in its snapshot").first.click()
            page.get_by_role("heading", name="Recorded snapshot document selection").wait_for(timeout=30_000)
            assert "Recorded snapshot document selection" in page.locator("main").inner_text()
            assert str(prep.snapshot_id) in page.locator("main").inner_text()
            for selected in selection["items"]:
                if selected["selection"] == "SELECTED":
                    assert selected["evidence_ref"] in page.locator("main").inner_text()
            other = (
                "a.analyst2@synthetic" if prep.analyst == "a.analyst1@synthetic" else "a.analyst1@synthetic"
            )
            other_page = browser.new_page()
            sign_in(other_page, web, other)
            other_page.goto(path)
            assert other_page.locator("main").get_by_role("heading", name="Recorded versions").count() == 0
            snapshot_path = (
                f"{web}/workspace/snapshot-selection?case={prep.case_id}&output={result.output_id}"
            )
            other_page.goto(snapshot_path)
            assert str(prep.snapshot_id) not in other_page.locator("main").inner_text()
            tenant_page = browser.new_page()
            sign_in(tenant_page, web, "b.supervisor1@synthetic")
            tenant_page.goto(snapshot_path)
            assert str(prep.snapshot_id) not in tenant_page.locator("main").inner_text()
        finally:
            browser.close()
