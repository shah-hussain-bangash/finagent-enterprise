"""Recorded-risk browser check against the disposable synthetic test stack."""

import pytest

from finagent.agents.runner import run_simple
from tests.agent_helpers import prepared_case
from tests.browser.test_ui_journey import sign_in, stack  # noqa: F401

pytestmark = [pytest.mark.browser, pytest.mark.integration]


@pytest.fixture(autouse=True)
def local_web_auth(monkeypatch):
    # The spawned test web server must never inherit the running portal's auth mode.
    monkeypatch.setenv("FINAGENT_WEB_AUTH_MODE", "local")
    monkeypatch.setenv("FINAGENT_ENVIRONMENT", "local")


def test_recorded_indicator_in_browser(stack, make_client, make_settings, token, bank):
    from playwright.sync_api import sync_playwright

    settings = make_settings(bank_base_url=bank.url)
    prep = prepared_case(make_client(bank_base_url=bank.url), token, settings)
    scope, _ = prep.scope()
    run_simple("AG-AML", prep.facts(), scope)
    web = stack["web"]
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            page.goto(f"{web}/workspace/recorded-risk?case={prep.case_id}")
            assert "/signin" in page.url
            sign_in(page, web, prep.analyst)
            page.goto(f"{web}/workspace/recorded-risk?case={prep.case_id}")
            main = page.locator("main")
            assert main.get_by_text("Connected, read-only view").count() == 1
            assert main.get_by_text("aml-ind-STRUCTURING_PATTERN").count() >= 1
            assert str(prep.snapshot_id) in main.inner_text()
            assert "calc:cash_below_threshold_count" in main.inner_text()
        finally:
            browser.close()
