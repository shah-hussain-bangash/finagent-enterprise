"""Exercise recorded transaction evidence with the disposable synthetic stack."""

import pytest

from tests.agent_helpers import prepared_case
from tests.browser.test_ui_journey import sign_in, stack  # noqa: F401

pytestmark = [pytest.mark.browser, pytest.mark.integration]


@pytest.fixture(autouse=True)
def local_web_auth(monkeypatch):
    monkeypatch.setenv("FINAGENT_WEB_AUTH_MODE", "local")
    monkeypatch.setenv("FINAGENT_ENVIRONMENT", "local")


def test_case_transactions_are_visible_only_to_assigned_analyst(stack, make_client, make_settings, token, bank):
    from playwright.sync_api import sync_playwright

    settings = make_settings(bank_base_url=bank.url)
    prep = prepared_case(make_client(bank_base_url=bank.url), token, settings)
    web = stack["web"]
    path = f"{web}/workspace/recorded-transactions?case={prep.case_id}"
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            page.goto(path)
            assert "/signin" in page.url
            sign_in(page, web, prep.analyst)
            page.goto(path)
            main = page.locator("main")
            assert main.get_by_text("Connected, read-only view").count() == 1
            assert main.get_by_role("table", name="Transactions from this evidence source").count() >= 1
            assert main.get_by_role("heading", name="Filtered posted amounts by currency and direction").count() >= 1
            assert "COMPLETE" in main.inner_text()
            page.get_by_label("Search transaction ID, counterparty, date, direction or status").fill("no-such-synthetic-transaction")
            page.get_by_role("button", name="Apply filters").click()
            assert "No recorded transactions match this search" in main.inner_text()
            page.goto(path)
            page.get_by_label("Direction", exact=True).select_option("DEBIT")
            page.get_by_role("button", name="Apply filters").click()
            for row in page.get_by_role("table", name="Transactions from this evidence source").locator("tbody tr").all():
                assert row.locator("td").nth(3).inner_text() == "DEBIT"
            other = "a.analyst2@synthetic" if prep.analyst == "a.analyst1@synthetic" else "a.analyst1@synthetic"
            other_page = browser.new_page()
            sign_in(other_page, web, other)
            other_page.goto(path)
            assert other_page.locator("main").get_by_role("table", name="Transactions from this evidence source").count() == 0
        finally:
            browser.close()
