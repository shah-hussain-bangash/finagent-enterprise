"""Exercise the case overview against the disposable synthetic stack."""

import pytest

from finagent.agents.runner import run_simple
from tests.agent_helpers import prepared_case
from tests.browser.test_ui_journey import sign_in, stack  # noqa: F401

pytestmark = [pytest.mark.browser, pytest.mark.integration]


@pytest.fixture(autouse=True)
def local_web_auth(monkeypatch):
    monkeypatch.setenv("FINAGENT_WEB_AUTH_MODE", "local")
    monkeypatch.setenv("FINAGENT_ENVIRONMENT", "local")


def test_case_overview_requires_case_access(stack, make_client, make_settings, token, bank):  # noqa: F811
    from playwright.sync_api import sync_playwright

    prep = prepared_case(make_client(bank_base_url=bank.url), token, make_settings(bank_base_url=bank.url))
    scope, _ = prep.scope()
    run_simple("AG-AML", prep.facts(), scope)
    web = stack["web"]
    path = f"{web}/workspace/case-overview?case={prep.case_id}"
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            page.goto(path)
            assert "/signin" in page.url
            sign_in(page, web, prep.analyst)
            page.goto(path)
            main = page.locator("main")
            assert main.get_by_role("heading", name="Current evidence and gaps").count() == 1
            assert main.get_by_role("heading", name="Previously recorded risk indicators").count() == 1
            assert str(prep.snapshot_id) in main.inner_text()
            assert main.get_by_role("link", name="Review recorded transactions").count() == 1
            other = (
                "a.analyst2@synthetic" if prep.analyst == "a.analyst1@synthetic" else "a.analyst1@synthetic"
            )
            other_page = browser.new_page()
            sign_in(other_page, web, other)
            other_page.goto(path)
            assert (
                other_page.locator("main").get_by_role("heading", name="Current evidence and gaps").count()
                == 0
            )
        finally:
            browser.close()
