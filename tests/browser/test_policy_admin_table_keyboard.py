"""Exercise the synthetic policy administration table at a mobile viewport."""

import pytest

from tests.agent_helpers import PADMIN
from tests.browser.test_ui_journey import sign_in, stack  # noqa: F401

pytestmark = [pytest.mark.browser, pytest.mark.integration]


@pytest.fixture(autouse=True)
def local_web_auth(monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("FINAGENT_WEB_AUTH_MODE", "local")
    monkeypatch.setenv("FINAGENT_ENVIRONMENT", "local")


def test_policy_table_scrolls_with_keyboard_on_mobile(  # type: ignore[no-untyped-def]
    stack,  # noqa: F811
    make_client,
    token,
    bank,
):
    from playwright.sync_api import expect, sync_playwright

    client = make_client(bank_base_url=bank.url)
    assert client.post("/api/v1/knowledge/sync", headers=token(PADMIN)).status_code == 200
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 390, "height": 844})
            sign_in(page, stack["web"], PADMIN)
            page.goto(f"{stack['web']}/policies/admin")
            region = page.get_by_role("region", name="Policy versions table")
            expect(region).to_be_visible()
            expect(region.get_by_role("table", name="Policy versions")).to_be_visible()
            assert region.evaluate("element => element.scrollWidth > element.clientWidth")
            region.focus()
            expect(region).to_be_focused()
            page.keyboard.press("ArrowRight")
            page.wait_for_function("element => element.scrollLeft > 0", arg=region.element_handle())
        finally:
            browser.close()
