"""Exercise an interrupted synthetic investigation request through the real web UI."""

import socket
import time
from urllib.parse import urlparse

import pytest

from tests.browser.test_ui_journey import sign_in, stack, wait_text  # noqa: F401
from tests.helpers import assigned_analyst, ingest
from tests.integration.test_case_commands import force_status

pytestmark = [pytest.mark.browser, pytest.mark.integration]


@pytest.mark.parametrize("stack", ["workflow_cancel_gateway_error"], indirect=True)
def test_workflow_cancel_gateway_error_requires_reload(stack, client, token):  # type: ignore[no-untyped-def] # noqa: F811
    from playwright.sync_api import expect, sync_playwright

    case_id = ingest(client, token)["case_id"]
    analyst = assigned_analyst(client, token, case_id)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()
            sign_in(page, stack["web"], analyst)
            page.goto(f"{stack['web']}/cases/{case_id}")
            page.get_by_role("button", name="Start investigation").click()
            page.get_by_text("Investigation queued.").first.wait_for(timeout=30_000)
            wait_text(page, f"{stack['web']}/cases/{case_id}", "WAITING_REVIEW")
            runs = client.get(f"/api/v1/cases/{case_id}/workflows", headers=token(analyst))
            assert runs.status_code == 200 and len(runs.json()["items"]) == 1
            run_id = runs.json()["items"][0]["run_id"]
            page.goto(f"{stack['web']}/workflows/{run_id}")
            page.get_by_label("Cancel (committed effects are kept): reason").fill("Synthetic cancellation")
            page.get_by_role("button", name="Cancel run").click()
            expect(page.get_by_text("Check the recorded run before another action.")).to_be_visible(
                timeout=30_000
            )
            assert stack["proxy"].dropped_statuses == [202]
            result = client.get(f"/api/v1/workflows/{run_id}", headers=token(analyst))
            assert result.status_code == 200 and result.json()["status"] == "CANCELLED"
            page.get_by_role("link", name="Reload run").click()
            expect(page.get_by_text("CANCELLED", exact=False).first).to_be_visible()
        finally:
            browser.close()


@pytest.mark.parametrize("stack", ["report_withdrawal_gateway_error"], indirect=True)
def test_report_withdrawal_gateway_error_requires_reload(stack, client, token):  # type: ignore[no-untyped-def] # noqa: F811
    from playwright.sync_api import expect, sync_playwright

    case_id = ingest(client, token)["case_id"]
    analyst = assigned_analyst(client, token, case_id)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()
            sign_in(page, stack["web"], analyst)
            page.goto(f"{stack['web']}/cases/{case_id}")
            page.get_by_role("button", name="Start investigation").click()
            page.get_by_text("Investigation queued.").first.wait_for(timeout=30_000)
            wait_text(page, f"{stack['web']}/cases/{case_id}", "WAITING_REVIEW")
            page.goto(f"{stack['web']}/cases/{case_id}/evidence")
            page.get_by_label("Resolution", exact=True).select_option("USE_A")
            page.get_by_label("Reason (recorded; neither source is deleted)").fill(
                "Synthetic evidence reviewed"
            )
            page.get_by_role("button", name="Record resolution").click()
            page.get_by_text("Conflict resolved").first.wait_for(timeout=30_000)
            page.goto(f"{stack['web']}/cases/{case_id}")
            page.get_by_label("Re-analyse with fresh evidence (reason)").fill("Synthetic conflict resolved")
            page.get_by_role("button", name="Re-run evidence and analysis").click()
            wait_text(page, f"{stack['web']}/cases/{case_id}/report", "version 2 of 2")
            page.get_by_role("button", name="Submit version 2 for independent review").click()
            page.get_by_text("Submitted for independent review.").first.wait_for(timeout=30_000)
            page.goto(f"{stack['web']}/cases/{case_id}/report")
            page.get_by_label("Reason for withdrawing the current submission").fill(
                "Synthetic review correction"
            )
            page.get_by_role("button", name="Withdraw current submission").click()
            expect(page.get_by_text("Check the recorded report before another action.")).to_be_visible(
                timeout=30_000
            )
            assert stack["proxy"].dropped_statuses == [200]
            report = client.get(f"/api/v1/cases/{case_id}/reports", headers=token(analyst))
            assert report.status_code == 200
            assert report.json()["report"]["status"] == "DRAFT"
            assert report.json()["report"]["current_version_no"] == 2
            case = client.get(f"/api/v1/cases/{case_id}", headers=token(analyst))
            assert case.status_code == 200 and case.json()["status"] == "REWORK"
            page.get_by_role("link", name="Reload report").click()
            expect(page.get_by_text("version 2 of 2", exact=False)).to_be_visible()
        finally:
            browser.close()


@pytest.mark.parametrize("stack", ["field_correction_gateway_error"], indirect=True)
def test_field_correction_gateway_error_requires_reload(  # type: ignore[no-untyped-def]
    stack,  # noqa: F811
    client,
    token,
):
    from playwright.sync_api import expect, sync_playwright

    from tests.doc_fixtures import pdf

    case_id = ingest(client, token)["case_id"]
    analyst = assigned_analyst(client, token, case_id)
    upload = client.post(
        "/api/v1/documents",
        params={"case_id": case_id, "document_type": "PASSPORT", "filename": "synthetic-passport.pdf"},
        content=pdf("Full name: Synthetic Person", "Date of birth: 03/04/1985"),
        headers={
            **token(analyst),
            "Content-Type": "application/pdf",
            "Idempotency-Key": "synthetic-field-upload",
        },
    )
    assert upload.status_code == 201, upload.text
    document_id = upload.json()["document_id"]
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        current = client.get(f"/api/v1/documents/{document_id}", headers=token(analyst))
        assert current.status_code == 200, current.text
        if any(f["field_key"] == "date_of_birth" for f in current.json()["fields"]):
            break
        time.sleep(0.5)
    else:
        pytest.fail("Synthetic document did not produce the expected field")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()
            sign_in(page, stack["web"], analyst)
            page.goto(f"{stack['web']}/documents/{document_id}")
            page.get_by_label("New value for date_of_birth").fill("1985-04-03")
            page.get_by_label("Reason for correcting date_of_birth").fill(
                "Confirmed against synthetic original"
            )
            page.get_by_role("button", name="Save correction for date_of_birth").click()
            expect(page.get_by_text("Check the recorded document before another action.")).to_be_visible(
                timeout=30_000
            )
            assert stack["proxy"].dropped_statuses == [201]
            result = client.get(f"/api/v1/documents/{document_id}", headers=token(analyst))
            assert result.status_code == 200
            assert [f["value"] for f in result.json()["fields"] if f["field_key"] == "date_of_birth"] == [
                "1985-04-03"
            ]
            assert [
                f["value"] for f in result.json()["field_history"] if f["field_key"] == "date_of_birth"
            ] == ["03/04/1985"]
            page.get_by_role("link", name="Reload document").click()
            expect(page.get_by_text("1985-04-03", exact=True).first).to_be_visible()
            expect(page.get_by_text("03/04/1985", exact=False).first).to_be_visible()
        finally:
            browser.close()


@pytest.mark.parametrize("stack", ["snapshot_gateway_error"], indirect=True)
def test_snapshot_gateway_error_requires_reload(  # type: ignore[no-untyped-def]
    stack,  # noqa: F811
    client,
    token,
):
    from playwright.sync_api import expect, sync_playwright

    case_id = ingest(client, token)["case_id"]
    analyst = assigned_analyst(client, token, case_id)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()
            sign_in(page, stack["web"], analyst)
            page.goto(f"{stack['web']}/cases/{case_id}/evidence")
            page.get_by_role("button", name="Freeze current evidence").click()
            expect(page.get_by_text("Check the recorded evidence before another action.")).to_be_visible(
                timeout=30_000
            )
            assert stack["proxy"].dropped_statuses == [201]
            snapshots = client.get(f"/api/v1/cases/{case_id}/snapshots", headers=token(analyst))
            assert snapshots.status_code == 200 and len(snapshots.json()["items"]) == 1
            page.get_by_role("link", name="Reload evidence").click()
            expect(page.get_by_role("cell", name="v1", exact=False)).to_be_visible()
        finally:
            browser.close()


@pytest.mark.parametrize("stack", ["workflow_resume_gateway_error"], indirect=True)
def test_workflow_resume_gateway_error_requires_reload(  # type: ignore[no-untyped-def]
    stack,  # noqa: F811
    client,
    token,
):
    from playwright.sync_api import expect, sync_playwright

    case_id = ingest(client, token)["case_id"]
    analyst = assigned_analyst(client, token, case_id)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()
            sign_in(page, stack["web"], analyst)
            page.goto(f"{stack['web']}/cases/{case_id}")
            page.get_by_role("button", name="Start investigation").click()
            page.get_by_text("Investigation queued.").first.wait_for(timeout=30_000)
            wait_text(page, f"{stack['web']}/cases/{case_id}", "WAITING_REVIEW")
            page.goto(f"{stack['web']}/cases/{case_id}/evidence")
            page.get_by_label("Resolution", exact=True).select_option("USE_A")
            page.get_by_label("Reason (recorded; neither source is deleted)").fill(
                "Synthetic evidence reviewed"
            )
            page.get_by_role("button", name="Record resolution").click()
            page.get_by_text("Conflict resolved").first.wait_for(timeout=30_000)
            page.goto(f"{stack['web']}/cases/{case_id}")
            page.get_by_label("Re-analyse with fresh evidence (reason)").fill("Synthetic conflict resolved")
            page.get_by_role("button", name="Re-run evidence and analysis").click()
            expect(page.get_by_text("Check the recorded case before another action.")).to_be_visible(
                timeout=30_000
            )
            assert stack["proxy"].dropped_statuses == [202]
            page.get_by_role("link", name="Reload case").click()
            wait_text(page, f"{stack['web']}/cases/{case_id}/report", "version 2 of 2")
            runs = client.get(f"/api/v1/cases/{case_id}/workflows", headers=token(analyst))
            assert runs.status_code == 200 and len(runs.json()["items"]) == 1
        finally:
            browser.close()


@pytest.mark.parametrize("stack", ["document_upload_gateway_error"], indirect=True)
def test_document_upload_gateway_error_requires_reload(  # type: ignore[no-untyped-def]
    stack,  # noqa: F811
    client,
    token,
):
    from playwright.sync_api import expect, sync_playwright

    case_id = ingest(client, token)["case_id"]
    analyst = assigned_analyst(client, token, case_id)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()
            sign_in(page, stack["web"], analyst)
            page.goto(f"{stack['web']}/cases/{case_id}/documents")
            page.locator('input[name="file"]').set_input_files(
                {
                    "name": "synthetic-evidence.csv",
                    "mimeType": "text/csv",
                    "buffer": b"name,amount\nsample,10\n",
                }
            )
            page.get_by_label("Document type").select_option("OTHER")
            page.get_by_role("button", name="Upload", exact=True).click()
            expect(page.get_by_text("Check the recorded documents before another action.")).to_be_visible(
                timeout=30_000
            )
            assert stack["proxy"].dropped_statuses == [201]
            documents = client.get(f"/api/v1/cases/{case_id}/documents", headers=token(analyst))
            assert documents.status_code == 200
            assert sum(d["title"] == "synthetic-evidence.csv" for d in documents.json()["items"]) == 1
            page.get_by_role("link", name="Reload documents").click()
            expect(page.get_by_text("synthetic-evidence.csv", exact=False).first).to_be_visible()
        finally:
            browser.close()


@pytest.mark.parametrize("stack", ["report_edit_gateway_error"], indirect=True)
def test_report_edit_gateway_error_requires_reload(  # type: ignore[no-untyped-def]
    stack,  # noqa: F811
    client,
    token,
):
    from playwright.sync_api import expect, sync_playwright

    case_id = ingest(client, token)["case_id"]
    analyst = assigned_analyst(client, token, case_id)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()
            sign_in(page, stack["web"], analyst)
            page.goto(f"{stack['web']}/cases/{case_id}")
            page.get_by_role("button", name="Start investigation").click()
            page.get_by_text("Investigation queued.").first.wait_for(timeout=30_000)
            wait_text(page, f"{stack['web']}/cases/{case_id}/report", "version 1 of 1")
            section = page.locator('textarea[name="section_text"]').first
            section.fill(section.input_value() + "\nSynthetic analyst review note.")
            page.get_by_label("Edit note (required)").fill("Synthetic report review")
            page.get_by_role("button", name="Save as new version").click()
            expect(page.get_by_text("Check the recorded report before another action.")).to_be_visible(
                timeout=30_000
            )
            assert stack["proxy"].dropped_statuses == [201]
            report = client.get(f"/api/v1/cases/{case_id}/reports", headers=token(analyst))
            assert report.status_code == 200 and report.json()["report"]["current_version_no"] == 2
            saved = client.get(f"/api/v1/cases/{case_id}/reports/versions/2", headers=token(analyst))
            assert saved.status_code == 200
            assert any("Synthetic analyst review note." in item["text"] for item in saved.json()["sections"])
            page.get_by_role("link", name="Reload report").click()
            expect(page.get_by_text("version 2 of 2", exact=False)).to_be_visible()
        finally:
            browser.close()


def test_interrupted_start_requires_case_refresh(  # type: ignore[no-untyped-def]
    stack,  # noqa: F811
    client,
    token,
):
    from playwright.sync_api import expect, sync_playwright

    case_id = ingest(client, token)["case_id"]
    analyst = assigned_analyst(client, token, case_id)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()
            sign_in(page, stack["web"], analyst)
            page.goto(f"{stack['web']}/cases/{case_id}")
            start = page.get_by_role("button", name="Start investigation")
            expect(start).to_be_visible()

            # Stop only this fixture's local synthetic API after the page has loaded.
            stack["stop_api"]()
            address = urlparse(stack["api"])
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                try:
                    with socket.create_connection((address.hostname, address.port), timeout=0.2):
                        time.sleep(0.1)
                except OSError:
                    break
            else:
                pytest.fail("Synthetic API did not stop")

            start.click()
            expect(page.get_by_role("alert").filter(has_text="outcome may be unknown")).to_be_visible()
            expect(start).to_have_count(0)
            expect(page.get_by_role("link", name="Refresh case")).to_be_visible()
        finally:
            browser.close()


@pytest.mark.parametrize("stack", ["drop_case_update_response", "case_update_gateway_error"], indirect=True)
def test_lost_case_update_response_requires_reload(  # type: ignore[no-untyped-def]
    stack,  # noqa: F811
    client,
    token,
    db_url,
):
    from playwright.sync_api import expect, sync_playwright

    case_id = ingest(client, token)["case_id"]
    analyst = assigned_analyst(client, token, case_id)
    force_status(db_url, case_id, "INVESTIGATING")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()
            sign_in(page, stack["web"], analyst)
            page.goto(f"{stack['web']}/cases/{case_id}")
            page.get_by_label("Block case: reason").fill("Synthetic evidence needs review")
            page.get_by_role("button", name="Mark blocked").click()
            expect(page.get_by_text("Check the recorded case before another action.")).to_be_visible()
            expect(page.get_by_role("button", name="Resume investigation")).to_have_count(0)
            assert stack["proxy"].dropped_statuses == [200]
            response = client.get(f"/api/v1/cases/{case_id}", headers=token(analyst))
            assert response.status_code == 200
            assert response.json()["status"] == "BLOCKED"
            page.get_by_role("link", name="Reload case").click()
            expect(page.get_by_role("button", name="Resume investigation")).to_be_visible()
        finally:
            browser.close()


def test_lost_browser_response_does_not_repeat_start(  # type: ignore[no-untyped-def]
    stack,  # noqa: F811
    client,
    token,
):
    from playwright.sync_api import expect, sync_playwright

    case_id = ingest(client, token)["case_id"]
    analyst = assigned_analyst(client, token, case_id)
    responses: list[int] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()
            sign_in(page, stack["web"], analyst)
            page.goto(f"{stack['web']}/cases/{case_id}")
            start = page.get_by_role("button", name="Start investigation")
            expect(start).to_be_visible()

            def lose_response(route):  # type: ignore[no-untyped-def]
                if route.request.method != "POST":
                    route.continue_()
                    return
                # Forward the server action to completion, then drop its browser response.
                response = route.fetch()
                responses.append(response.status)
                route.abort("failed")

            page.route(f"**/cases/{case_id}", lose_response)
            start.click()
            expect(page.get_by_role("alert").filter(has_text="Could not confirm the request")).to_be_visible()
            expect(start).to_have_count(0)
            expect(page.get_by_role("link", name="Refresh case")).to_be_visible()
            assert responses == [200]
            runs = client.get(f"/api/v1/cases/{case_id}/workflows", headers=token(analyst))
            assert runs.status_code == 200
            assert len(runs.json()["items"]) == 1
        finally:
            browser.close()


@pytest.mark.parametrize("stack", ["drop_workflow_response"], indirect=True)
def test_api_response_lost_after_start_requires_refresh(  # type: ignore[no-untyped-def]
    stack,  # noqa: F811
    client,
    token,
):
    from playwright.sync_api import expect, sync_playwright

    case_id = ingest(client, token)["case_id"]
    analyst = assigned_analyst(client, token, case_id)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()
            sign_in(page, stack["web"], analyst)
            page.goto(f"{stack['web']}/cases/{case_id}")
            start = page.get_by_role("button", name="Start investigation")
            expect(start).to_be_visible()
            start.click()
            expect(page.get_by_role("alert").filter(has_text="outcome may be unknown")).to_be_visible()
            expect(start).to_have_count(0)
            expect(page.get_by_role("link", name="Refresh case")).to_be_visible()
            assert stack["proxy"].dropped_statuses == [202]
            runs = client.get(f"/api/v1/cases/{case_id}/workflows", headers=token(analyst))
            assert runs.status_code == 200
            assert len(runs.json()["items"]) == 1
        finally:
            browser.close()
