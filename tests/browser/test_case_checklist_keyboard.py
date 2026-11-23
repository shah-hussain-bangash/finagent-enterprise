"""Keyboard access to the synthetic case checklist disclosure."""

import pytest

from tests.browser.test_ui_journey import sign_in, stack  # noqa: F401
from tests.helpers import assigned_analyst, ingest

pytestmark = [pytest.mark.browser, pytest.mark.integration]


def test_case_checklist_can_be_collapsed_with_keyboard(  # type: ignore[no-untyped-def]
    stack,  # noqa: F811
    client,
    token,
):
    from playwright.sync_api import expect, sync_playwright

    case_id = ingest(client, token)["case_id"]
    analyst = assigned_analyst(client, token, case_id)
    evidence = client.get(f"/api/v1/cases/{case_id}/evidence", headers=token(analyst))
    assert evidence.status_code == 200
    assert len(evidence.json()["gaps"]) > 0
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 390, "height": 844})
            sign_in(page, stack["web"], analyst)
            page.goto(f"{stack['web']}/workspace/case-overview?case={case_id}")
            details = page.locator("details").filter(has=page.locator("ul.investigation-gaps"))
            summary = details.locator("summary")
            gaps = details.locator("ul.investigation-gaps")
            expect(gaps).to_be_visible()
            summary.focus()
            expect(summary).to_be_focused()
            page.keyboard.press("Enter")
            expect(gaps).to_be_hidden()
            page.keyboard.press("Enter")
            expect(gaps).to_be_visible()
            briefing = page.get_by_role("link", name="Review evidence briefing")
            assert f"case={case_id}" in briefing.get_attribute("href")
        finally:
            browser.close()
