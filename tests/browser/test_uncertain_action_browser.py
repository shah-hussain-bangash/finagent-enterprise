"""Exercise an interrupted synthetic investigation request through the real web UI."""

import socket
import time
from urllib.parse import urlparse

import pytest

from tests.browser.test_ui_journey import sign_in, stack  # noqa: F401
from tests.helpers import assigned_analyst, ingest

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
