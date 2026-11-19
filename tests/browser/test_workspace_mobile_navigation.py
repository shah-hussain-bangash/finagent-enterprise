"""Check the synthetic workspace navigation at a narrow viewport."""

import pytest

from tests.browser.test_ui_journey import sign_in, stack  # noqa: F401

pytestmark = [pytest.mark.browser, pytest.mark.integration]


@pytest.fixture(autouse=True)
def local_web_auth(monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("FINAGENT_WEB_AUTH_MODE", "local")
    monkeypatch.setenv("FINAGENT_ENVIRONMENT", "local")


def test_mobile_sidebar_is_hidden_until_opened_and_escape_returns_focus(stack):  # type: ignore[no-untyped-def]
    from playwright.sync_api import expect, sync_playwright

    web = stack["web"]
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 390, "height": 844})
            sign_in(page, web, "a.analyst1@synthetic")
            page.goto(f"{web}/workspace")
            menu = page.get_by_role("button", name="Toggle navigation")
            sidebar = page.locator("#workspace-navigation")
            expect(menu).to_have_attribute("aria-expanded", "false")
            expect(sidebar).to_be_hidden()
            menu.focus()
            page.keyboard.press("Enter")
            expect(sidebar).to_be_visible()
            expect(menu).to_have_attribute("aria-expanded", "true")
            expect(sidebar.get_by_role("button", name="Dashboard")).to_be_focused()
            page.keyboard.press("Escape")
            expect(sidebar).to_be_hidden()
            expect(menu).to_have_attribute("aria-expanded", "false")
            expect(menu).to_be_focused()
        finally:
            browser.close()
