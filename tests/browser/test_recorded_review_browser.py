"""Read recorded report history through the disposable synthetic browser stack."""

import pytest

from tests.agent_helpers import approve_policies
from tests.browser.test_ui_journey import sign_in, stack, wait_text  # noqa: F401
from tests.helpers import assigned_analyst, ingest

pytestmark = [pytest.mark.browser, pytest.mark.integration]


@pytest.fixture(autouse=True)
def local_web_auth(monkeypatch):
    monkeypatch.setenv("FINAGENT_WEB_AUTH_MODE", "local")
    monkeypatch.setenv("FINAGENT_ENVIRONMENT", "local")


def test_report_history_requires_case_access(stack, client, token):
    from playwright.sync_api import sync_playwright

    approve_policies(client, token)
    case_id = ingest(client, token)["case_id"]
    analyst = assigned_analyst(client, token, case_id)
    web = stack["web"]
    path = f"{web}/workspace/recorded-review?case={case_id}"
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            page.goto(path)
            assert "/signin" in page.url
            sign_in(page, web, analyst)
            page.goto(f"{web}/cases/{case_id}")
            page.get_by_role("button", name="Start investigation").click()
            page.get_by_text("Investigation queued.").first.wait_for(timeout=30_000)
            wait_text(page, f"{web}/cases/{case_id}", "WAITING_REVIEW")
            page.goto(path)
            main = page.locator("main")
            assert main.get_by_role("heading", name="Report version history").count() == 1
            assert main.get_by_role("heading", name="Changes for version 1").count() == 1
            assert "Evidence snapshot:" in main.inner_text()
            other = "a.analyst2@synthetic" if analyst == "a.analyst1@synthetic" else "a.analyst1@synthetic"
            other_page = browser.new_page()
            sign_in(other_page, web, other)
            other_page.goto(path)
            assert other_page.locator("main").get_by_role("heading", name="Changes for version 1").count() == 0
        finally:
            browser.close()
