"""Exercise the connected briefing with a disposable synthetic case."""

import re

import pytest

from tests.agent_helpers import prepared_case
from tests.browser.test_ui_journey import sign_in, stack  # noqa: F401

pytestmark = [pytest.mark.browser, pytest.mark.integration]


@pytest.fixture(autouse=True)
def local_web_auth(monkeypatch):
    monkeypatch.setenv("FINAGENT_WEB_AUTH_MODE", "local")
    monkeypatch.setenv("FINAGENT_ENVIRONMENT", "local")


def test_briefing_requires_case_access(stack, make_client, make_settings, token, bank):
    from playwright.sync_api import sync_playwright

    prep = prepared_case(make_client(bank_base_url=bank.url), token, make_settings(bank_base_url=bank.url))
    response = prep.client.get(f"/api/v1/cases/{prep.case_id}/evidence", headers=token(prep.analyst))
    assert response.status_code == 200
    assert response.json()["items"]
    web = stack["web"]
    path = f"{web}/workspace/evidence-briefing?case={prep.case_id}"
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            page.goto(path)
            assert "/signin" in page.url
            sign_in(page, web, prep.analyst)
            page.goto(path)
            main = page.locator("main")
            assert main.get_by_text("Connected, read-only evidence overview").count() == 1
            assert "TRANSACTIONS" in main.inner_text()
            assert "does not use an AI model" in main.inner_text()
            assert main.get_by_role("link", name="Open case evidence and source details").count() == 1
            page.get_by_label("Search text (3–80 characters)").fill("customer")
            page.get_by_role("button", name="Search evidence").click()
            main.get_by_text(re.compile(r"\d+ match\(es\)")).wait_for(timeout=30_000)
            assert "ev:" in main.inner_text()
            assert "customer" not in page.url
            source_link = main.get_by_role("link", name="Open source evidence").first
            href = source_link.get_attribute("href")
            assert href and "#evidence-" in href
            source_link.click()
            page.wait_for_url(re.compile(r"/cases/.*/evidence#evidence-"), timeout=30_000)
            assert page.locator(f"[id='{href.split('#', 1)[1]}']").count() == 1
            other = "a.analyst2@synthetic" if prep.analyst == "a.analyst1@synthetic" else "a.analyst1@synthetic"
            other_page = browser.new_page()
            sign_in(other_page, web, other)
            other_page.goto(path)
            assert other_page.locator("main").get_by_role("heading", name="Current evidence sources").count() == 0
        finally:
            browser.close()
