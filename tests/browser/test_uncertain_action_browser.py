"""Exercise an interrupted synthetic investigation request through the real web UI."""

import socket
import time
from urllib.parse import urlparse

import pytest

from tests.browser.test_ui_journey import sign_in, stack  # noqa: F401
from tests.helpers import assigned_analyst, ingest
from tests.integration.test_case_commands import force_status

pytestmark = [pytest.mark.browser, pytest.mark.integration]


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
