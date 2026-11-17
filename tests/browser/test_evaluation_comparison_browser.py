"""Compare recorded synthetic evaluation runs in the disposable browser stack."""

import pytest

from tests.agent_helpers import PADMIN, approve_policies
from tests.browser.test_ui_journey import sign_in, stack  # noqa: F401
from tests.helpers import subject

pytestmark = [pytest.mark.browser, pytest.mark.integration]


@pytest.fixture(autouse=True)
def local_web_auth(monkeypatch):
    monkeypatch.setenv("FINAGENT_WEB_AUTH_MODE", "local")
    monkeypatch.setenv("FINAGENT_ENVIRONMENT", "local")


def test_evaluation_comparison_requires_permission(stack, make_client, token, bank):
    from playwright.sync_api import sync_playwright

    client = make_client(bank_base_url=bank.url)
    approve_policies(client, token)
    first = client.post("/api/v1/evaluations", json={"dataset": "aml-synthetic-v1"}, headers=token(PADMIN))
    second = client.post(
        "/api/v1/evaluations", json={"dataset": "aml-synthetic-v1"}, headers=token(subject("a", "operator1"))
    )
    assert first.status_code == 201 and second.status_code == 201
    first_id, second_id = first.json()["evaluation_id"], second.json()["evaluation_id"]
    web = stack["web"]
    path = f"{web}/workspace/evaluation-comparison?baseline={first_id}&candidate={second_id}"
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            page.goto(path)
            assert "/signin" in page.url
            sign_in(page, web, PADMIN)
            page.goto(path)
            main = page.locator("main")
            assert main.get_by_role("table", name="Recorded metrics by run").count() == 1
            assert "not an acceptance decision" in main.inner_text()
            assert "not evaluated" in main.inner_text()
            analyst = browser.new_page()
            sign_in(analyst, web, subject("a", "analyst1"))
            analyst.goto(path)
            assert analyst.locator("main").get_by_role("table", name="Recorded metrics by run").count() == 0
        finally:
            browser.close()
