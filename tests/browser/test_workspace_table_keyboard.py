"""Check keyboard access to a wide synthetic transaction table on mobile."""

import re

import pytest

from tests.agent_helpers import prepared_case
from tests.browser.test_ui_journey import sign_in, stack  # noqa: F401

pytestmark = [pytest.mark.browser, pytest.mark.integration]


@pytest.fixture(autouse=True)
def local_web_auth(monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("FINAGENT_WEB_AUTH_MODE", "local")
    monkeypatch.setenv("FINAGENT_ENVIRONMENT", "local")


def test_transaction_table_scrolls_with_keyboard_on_mobile(stack, make_client, make_settings, token, bank):  # type: ignore[no-untyped-def]
    from playwright.sync_api import expect, sync_playwright

    settings = make_settings(bank_base_url=bank.url)
    prep = prepared_case(make_client(bank_base_url=bank.url), token, settings)
    web = stack["web"]
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 390, "height": 844})
            sign_in(page, web, prep.analyst)
            page.goto(f"{web}/workspace/recorded-transactions?case={prep.case_id}")
            region = page.get_by_role("region", name=re.compile("^Transactions for evidence source ")).first
            expect(region).to_be_visible()
            assert region.evaluate("element => element.scrollWidth > element.clientWidth")
            region.focus()
            expect(region).to_be_focused()
            page.keyboard.press("ArrowRight")
            page.wait_for_function("element => element.scrollLeft > 0", arg=region.element_handle())
        finally:
            browser.close()
