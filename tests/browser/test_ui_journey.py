"""Browser test of the critical human-review journey through the REAL web UI (Chromium/Playwright).

Runs the production web build (`next start`) against a live API (uvicorn, finagent_app role), a worker
loop, the SYNTHETIC mock bank and PostgreSQL. Deselected from the default suite; run with
`python scripts/tasks.py test-browser` (needs `npm run build` and `playwright install chromium`).

UI-001, UI-002, MST:UI-001, NQ-040, FR-027, FR-028.
"""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from tests.helpers import assigned_analyst, ingest

pytestmark = [pytest.mark.browser, pytest.mark.integration]
WEB_DIR = Path(__file__).resolve().parents[2] / "apps" / "web"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def wait_http(url: str, timeout: float = 60) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if httpx.get(url, timeout=2).status_code < 500:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    raise RuntimeError(f"{url} did not come up")


@pytest.fixture
def stack(make_settings, make_worker, bank, request) -> Iterator[dict[str, Any]]:  # type: ignore[no-untyped-def]
    import uvicorn

    from finagent.api.main import create_app

    settings = make_settings(bank_base_url=bank.url)
    api_port, web_port = free_port(), free_port()
    server = uvicorn.Server(
        uvicorn.Config(create_app(settings), host="127.0.0.1", port=api_port, log_level="warning")
    )
    api_thread = threading.Thread(target=server.run, daemon=True)
    api_thread.start()
    wait_http(f"http://127.0.0.1:{api_port}/health/live")
    proxy = None
    if getattr(request, "param", None) in {
        "drop_workflow_response",
        "drop_case_update_response",
        "case_update_gateway_error",
        "report_edit_gateway_error",
        "document_upload_gateway_error",
        "workflow_resume_gateway_error",
        "snapshot_gateway_error",
        "field_correction_gateway_error",
        "workflow_cancel_gateway_error",
        "report_withdrawal_gateway_error",
        "expired_case_evidence_read",
        "report_edit_auth_expired",
    }:
        from tests.browser.lost_response_proxy import LostResponseProxy

        proxy = LostResponseProxy(
            f"http://127.0.0.1:{api_port}",
            drop_case_update=request.param in {"drop_case_update_response", "case_update_gateway_error"},
            drop_report_edit=request.param == "report_edit_gateway_error",
            drop_document_upload=request.param == "document_upload_gateway_error",
            drop_workflow_resume=request.param == "workflow_resume_gateway_error",
            drop_snapshot=request.param == "snapshot_gateway_error",
            drop_field_correction=request.param == "field_correction_gateway_error",
            drop_workflow_cancel=request.param == "workflow_cancel_gateway_error",
            drop_report_withdrawal=request.param == "report_withdrawal_gateway_error",
            expire_case_evidence_read=request.param == "expired_case_evidence_read",
            reject_report_edit_auth=request.param == "report_edit_auth_expired",
            gateway_error=request.param
            in {
                "case_update_gateway_error",
                "report_edit_gateway_error",
                "document_upload_gateway_error",
                "workflow_resume_gateway_error",
                "snapshot_gateway_error",
                "field_correction_gateway_error",
                "workflow_cancel_gateway_error",
                "report_withdrawal_gateway_error",
            },
        )
        proxy.start()
    npx = shutil.which("npx") or shutil.which("npx.cmd")
    assert npx, "npx not found"
    env = {
        **os.environ,
        "FINAGENT_API_URL": proxy.url if proxy else f"http://127.0.0.1:{api_port}",
        "FINAGENT_ENVIRONMENT": "test",
        "FINAGENT_WEB_AUTH_MODE": "local",
        "FINAGENT_WEB_SESSION_SECRET": "browser-test-only-session-secret-0123456789",
        "NEXT_TELEMETRY_DISABLED": "1",
    }
    web_log = tempfile.TemporaryFile(mode="w+t", encoding="utf-8")  # noqa: SIM115 - fixture lifetime
    web = subprocess.Popen(  # noqa: S603 - fixed argv
        [npx, "next", "start", "-p", str(web_port), "-H", "127.0.0.1"],
        cwd=WEB_DIR,
        env=env,
        stdout=web_log,
        stderr=web_log,
    )
    worker = make_worker(bank_base_url=bank.url)
    stop = threading.Event()

    def loop() -> None:
        while not stop.is_set():
            with contextlib.suppress(Exception):  # keep ticking; failures surface as UI state
                worker.tick()
            stop.wait(0.5)

    worker_thread = threading.Thread(target=loop, daemon=True)
    worker_thread.start()
    try:
        wait_http(f"http://127.0.0.1:{web_port}/signin", timeout=90)
        yield {
            "web": f"http://127.0.0.1:{web_port}",
            "api": f"http://127.0.0.1:{api_port}",
            "settings": settings,
            "web_log": web_log,
            "stop_api": lambda: setattr(server, "should_exit", True),
            "proxy": proxy,
        }
    finally:
        stop.set()
        worker_thread.join(timeout=15)
        web.terminate()
        server.should_exit = True
        web.wait(timeout=10)
        api_thread.join(timeout=15)
        web_log.close()
        if proxy:
            proxy.stop()
        if worker_thread.is_alive() or api_thread.is_alive():
            raise RuntimeError("Synthetic browser stack did not stop cleanly")


def sign_in(page: Any, web: str, who: str, *, mfa: str = "yes") -> None:
    page.goto(f"{web}/signin")
    page.get_by_label("Synthetic identity").select_option(who)
    page.get_by_label("Multi-factor authentication satisfied").select_option(mfa)
    page.get_by_role("button", name="Sign in").click()
    page.wait_for_url(f"{web}/")


def test_case_page_is_restored_after_synthetic_sign_in(stack, client, token):  # type: ignore[no-untyped-def]
    from playwright.sync_api import expect, sync_playwright

    case_id = ingest(client, token)["case_id"]
    analyst = assigned_analyst(client, token, case_id)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()
            page.goto(f"{stack['web']}/cases/{case_id}/evidence")
            expect(page).to_have_url(re.compile(r"/signin\?"))
            page.get_by_label("Synthetic identity").select_option(analyst)
            page.get_by_role("button", name="Sign in").click()
            expect(page).to_have_url(f"{stack['web']}/cases/{case_id}/evidence")
            expect(page.locator("main")).to_contain_text("Evidence")
        finally:
            browser.close()


def test_workspace_case_is_restored_after_synthetic_sign_in(stack, client, token):  # type: ignore[no-untyped-def]
    from playwright.sync_api import expect, sync_playwright

    case_id = ingest(client, token)["case_id"]
    analyst = assigned_analyst(client, token, case_id)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()
            page.goto(f"{stack['web']}/workspace/case-overview?case={case_id}")
            expect(page).to_have_url(re.compile(r"/signin\?"))
            page.get_by_label("Synthetic identity").select_option(analyst)
            page.get_by_role("button", name="Sign in").click()
            expect(page).to_have_url(f"{stack['web']}/workspace/case-overview?case={case_id}")
            expect(page.get_by_role("heading", name="Case investigation overview")).to_be_visible()
            expect(page.locator("main")).to_contain_text(case_id[:8])
        finally:
            browser.close()


def test_workspace_snapshot_selection_is_restored_after_sign_in(stack, client, token):  # type: ignore[no-untyped-def]
    from playwright.sync_api import expect, sync_playwright

    case_id = ingest(client, token)["case_id"]
    analyst = assigned_analyst(client, token, case_id)
    output_id = "00000000-0000-4000-8000-000000000001"
    path = f"{stack['web']}/workspace/snapshot-selection?case={case_id}&output={output_id}"
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()
            page.goto(path)
            expect(page).to_have_url(re.compile(r"/signin\?"))
            page.get_by_label("Synthetic identity").select_option(analyst)
            page.get_by_role("button", name="Sign in").click()
            expect(page).to_have_url(path)
            expect(page.get_by_role("heading", name="Recorded snapshot document selection")).to_be_visible()
        finally:
            browser.close()


def test_case_return_does_not_grant_cross_tenant_access(stack, client, token):  # type: ignore[no-untyped-def]
    from playwright.sync_api import expect, sync_playwright

    case_id = ingest(client, token)["case_id"]
    other_analyst = "b.analyst1@synthetic"
    denied = client.get(f"/api/v1/cases/{case_id}/evidence", headers=token(other_analyst))
    assert denied.status_code in (403, 404)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()
            page.goto(f"{stack['web']}/cases/{case_id}/evidence")
            expect(page).to_have_url(re.compile(r"/signin\?"))
            page.get_by_label("Synthetic identity").select_option(other_analyst)
            page.get_by_role("button", name="Sign in").click()
            expect(page).to_have_url(f"{stack['web']}/cases/{case_id}/evidence")
            expect(page.get_by_role("alert").first).to_contain_text(
                re.compile(r"not found|not permitted", re.I)
            )
            expect(page.get_by_text("Contradictory records")).to_have_count(0)
        finally:
            browser.close()


@pytest.mark.parametrize("stack", ["expired_case_evidence_read"], indirect=True)
def test_api_expired_case_read_restores_page_after_sign_in(stack, client, token):  # type: ignore[no-untyped-def]
    from playwright.sync_api import expect, sync_playwright

    case_id = ingest(client, token)["case_id"]
    analyst = assigned_analyst(client, token, case_id)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()
            sign_in(page, stack["web"], analyst)
            page.goto(f"{stack['web']}/cases/{case_id}/evidence")
            expect(page).to_have_url(re.compile(r"/signin\?"))
            assert stack["proxy"].dropped_statuses == [200]
            page.get_by_label("Synthetic identity").select_option(analyst)
            page.get_by_role("button", name="Sign in").click()
            expect(page).to_have_url(f"{stack['web']}/cases/{case_id}/evidence")
            expect(page.locator("main")).to_contain_text("Evidence")
        finally:
            browser.close()


@pytest.mark.parametrize("stack", ["expired_case_evidence_read"], indirect=True)
def test_api_expired_workspace_read_restores_case_after_sign_in(stack, client, token):  # type: ignore[no-untyped-def]
    from playwright.sync_api import expect, sync_playwright

    case_id = ingest(client, token)["case_id"]
    analyst = assigned_analyst(client, token, case_id)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()
            sign_in(page, stack["web"], analyst)
            path = f"{stack['web']}/workspace/case-overview?case={case_id}"
            page.goto(path)
            expect(page).to_have_url(re.compile(r"/signin\?"))
            assert stack["proxy"].dropped_statuses == [200]
            page.get_by_label("Synthetic identity").select_option(analyst)
            page.get_by_role("button", name="Sign in").click()
            expect(page).to_have_url(path)
            expect(page.get_by_role("heading", name="Case investigation overview")).to_be_visible()
            expect(page.locator("main")).to_contain_text(case_id[:8])
        finally:
            browser.close()


def wait_text(page: Any, url: str, text: str, timeout: float = 120) -> None:
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        page.goto(url)
        try:
            page.locator("main").get_by_text(text, exact=False).first.wait_for(timeout=5_000)
            return
        except PlaywrightTimeoutError:
            pass
        time.sleep(1.5)
    raise AssertionError(f"'{text}' not shown at {url}")


@pytest.mark.req(
    "UI-001",
    "UI-002",
    "MST:UI-001",
    "MST:UI-002",
    "MST:UI-003",
    "NQ-040",
    "FR-027",
    "FR-028",
    "FR-010",
    "FR-006",
)
@pytest.mark.parametrize("stack", ["case_update_gateway_error"], indirect=True)
def test_analyst_and_reviewer_complete_a_case_in_the_browser(stack, client, token) -> None:  # type: ignore[no-untyped-def]
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright

    web = stack["web"]
    case_id = ingest(client, token)["case_id"]
    analyst = assigned_analyst(client, token, case_id)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context()
        page = context.new_page()
        page.goto(f"{web}/cases")
        page.wait_for_url("**/signin?expired=1", timeout=30_000)
        assert "/signin" in page.url  # no session: redirected to sign-in
        sign_in(page, web, analyst)
        assert page.locator("html").get_attribute("lang") == "en"
        page.goto(f"{web}/cases")
        page.get_by_role("link", name=case_id[:8]).click()
        assert "Assigned" in page.locator("main").inner_text()
        action_events: list[str] = []
        page.on(
            "response",
            lambda response: (
                action_events.append(
                    f"POST response: {response.status}; "
                    f"action redirect present: {'x-action-redirect' in response.headers}; "
                    f"content type: {response.headers.get('content-type', 'missing')}"
                )
                if response.request.method == "POST" and f"/cases/{case_id}" in response.url
                else None
            ),
        )
        page.on("pageerror", lambda error: action_events.append(f"Browser error: {str(error)[:300]}"))
        page.on(
            "console",
            lambda message: (
                action_events.append(f"Console error: {message.text[:300]}")
                if message.type == "error"
                else None
            ),
        )
        page.on(
            "response",
            lambda response: (
                action_events.append(f"Case GET: {response.status}; url: {response.url}")
                if response.request.method == "GET" and f"/cases/{case_id}" in response.url
                else None
            ),
        )
        page.on(
            "requestfailed",
            lambda request: (
                action_events.append(f"POST request failed: {request.failure}")
                if request.method == "POST" and f"/cases/{case_id}" in request.url
                else None
            ),
        )
        page.on(
            "requestfinished",
            lambda request: (
                action_events.append("POST finished")
                if request.method == "POST" and f"/cases/{case_id}" in request.url
                else None
            ),
        )
        action_responses: list[Any] = []
        page.on(
            "response",
            lambda response: (
                action_responses.append(response)
                if response.request.method == "POST" and f"/cases/{case_id}" in response.url
                else None
            ),
        )
        page.get_by_role("button", name="Start investigation").click()
        # the confirmation comes from the server after the API accepted the command (never optimistic)
        try:
            page.get_by_text("Investigation queued.").first.wait_for(timeout=30_000)
        except PlaywrightTimeoutError as exc:
            main_text = page.locator("main").inner_text(timeout=5_000)
            workflow_response = client.get(f"/api/v1/cases/{case_id}/workflows", headers=token(analyst))
            case_response = client.get(f"/api/v1/cases/{case_id}", headers=token(analyst))
            stack["web_log"].seek(0)
            web_output = stack["web_log"].read().lower()
            redirect_error = "failed to get redirect response" in web_output
            response_summary = "no response"
            if action_responses:
                try:
                    response_body = action_responses[-1].body()
                    response_summary = (
                        f"{len(response_body)} bytes; render error marker: "
                        f"{b'$RX' in response_body or b'NEXT_ERROR' in response_body}"
                    )
                except Exception as body_error:
                    response_summary = f"body unavailable: {type(body_error).__name__}"
            raise AssertionError(
                f"Investigation confirmation missing; page: {page.url}; "
                f"action events: {action_events}; workflows HTTP: {workflow_response.status_code}; "
                f"workflow data: {workflow_response.text}; case data: {case_response.text}; "
                f"redirect fetch error logged: {redirect_error}; "
                f"response: {response_summary}; "
                f"main: {main_text}"
            ) from exc
        wait_text(page, f"{web}/cases/{case_id}", "WAITING_REVIEW")
        # The case update is saved, but the proxy substitutes a gateway error.
        page.goto(f"{web}/cases/{case_id}")
        page.wait_for_load_state("networkidle")
        page.get_by_label("Block case: reason").fill("Synthetic evidence needs review")
        page.get_by_role("button", name="Mark blocked").click()
        try:
            page.get_by_text("Check the recorded case before another action.").wait_for(timeout=30_000)
        except PlaywrightTimeoutError as exc:
            actual_case = client.get(f"/api/v1/cases/{case_id}", headers=token(analyst))
            actual_status = (
                actual_case.json().get("status")
                if actual_case.status_code == 200
                else actual_case.status_code
            )
            raise AssertionError(
                f"Recovery warning missing; page: {page.url}; "
                f"upstream statuses: {stack['proxy'].dropped_statuses}; "
                f"case status: {actual_status}; "
                f"main: {page.locator('main').inner_text()}"
            ) from exc
        assert stack["proxy"].dropped_statuses == [200]
        saved = client.get(f"/api/v1/cases/{case_id}", headers=token(analyst))
        assert saved.status_code == 200 and saved.json()["status"] == "BLOCKED"
        page.get_by_role("link", name="Reload case").click()
        page.get_by_label("Resolve block: reason").fill("Synthetic evidence review complete")
        page.get_by_role("button", name="Resume investigation").click()
        page.wait_for_url(re.compile(rf"^{re.escape(web)}/cases/{re.escape(case_id)}\?ok="), timeout=30_000)
        wait_text(page, f"{web}/cases/{case_id}", "WAITING_REVIEW")
        resumed = client.get(f"/api/v1/cases/{case_id}", headers=token(analyst))
        assert resumed.status_code == 200 and resumed.json()["status"] == "INVESTIGATING"
        page.goto(f"{web}/cases/{case_id}/evidence")
        page.locator("main").get_by_text("date_of_birth", exact=False).first.wait_for(timeout=30_000)
        page.get_by_label("Resolution", exact=True).select_option("USE_A")
        page.get_by_label("Reason (recorded; neither source is deleted)").fill(
            "core banking DOB verified at onboarding"
        )
        page.get_by_role("button", name="Record resolution").click()
        page.get_by_text("Conflict resolved").first.wait_for(timeout=30_000)
        page.goto(f"{web}/cases/{case_id}")
        page.get_by_label("Re-analyse with fresh evidence (reason)").fill("conflict resolved")
        page.get_by_role("button", name="Re-run evidence and analysis").click()
        wait_text(page, f"{web}/cases/{case_id}/report", "version 2 of 2")
        page.goto(f"{web}/cases/{case_id}/agents")
        page.get_by_text("Deterministic result", exact=False).first.wait_for(timeout=30_000)
        findings = page.locator("main").inner_text()
        assert "Deterministic result" in findings and "AI interpretation" in findings  # labelled in text
        page.goto(f"{web}/cases/{case_id}/report")
        page.get_by_role("button", name="Submit version 2 for independent review").click()
        try:
            page.get_by_text("Submitted for independent review.").first.wait_for(timeout=30_000)
        except PlaywrightTimeoutError as exc:
            case = client.get(f"/api/v1/cases/{case_id}", headers=token(analyst))
            report = client.get(f"/api/v1/cases/{case_id}/reports", headers=token(analyst))
            report_status = report.status_code
            if report.status_code == 200:
                report_status = report.json().get("report", {}).get("status")
            raise AssertionError(
                f"Review submission confirmation missing; page: {page.url}; "
                f"main: {page.locator('main').inner_text()}; "
                f"case status: {case.json().get('status') if case.status_code == 200 else case.status_code}; "
                f"report status: {report_status}"
            ) from exc
        page.get_by_role("button", name="Sign out").click()
        sign_in(page, web, "a.reviewer1@synthetic")
        page.goto(f"{web}/approvals")
        page.locator("table a").first.click()
        page.wait_for_url("**/approvals/*")
        page.get_by_text("frozen for this review", exact=False).first.wait_for(timeout=30_000)
        reviewer = "a.reviewer1@synthetic"
        approval_id = page.url.rsplit("/", 1)[-1]
        approval_response = client.get(f"/api/v1/approvals/{approval_id}", headers=token(reviewer))
        assert approval_response.status_code == 200, approval_response.text
        approval = approval_response.json()
        report_response = client.get(f"/api/v1/cases/{case_id}/reports/versions/2", headers=token(analyst))
        assert report_response.status_code == 200, report_response.text
        reviewed_report = report_response.json()
        assert approval["case_id"] == case_id and approval["status"] == "PENDING"
        assert approval["report_version_id"] == reviewed_report["version_id"]
        assert approval["snapshot_id"] == reviewed_report["snapshot_id"]
        assert approval["reviewer_id"] and approval["requested_by"] != approval["reviewer_id"]
        assert approval["report"]["version_no"] == reviewed_report["version_no"] == 2
        assert any(claim["evidence_refs"] for claim in reviewed_report["claims"])
        assert approval["report"]["claims"] == reviewed_report["claims"]
        page.get_by_label("Approve").check()
        page.get_by_label("Rationale (required, recorded with your identity and the reviewed versions)").fill(
            "Evidence, calculations and policy citations reviewed."
        )
        page.get_by_role("button", name="Record decision").click()
        page.get_by_text("Decision recorded.").first.wait_for(timeout=30_000)
        wait_text(page, f"{web}/cases/{case_id}", "to COMPLETED")  # shown only after finalization
        recorded = client.get(f"/api/v1/approvals/{approval_id}", headers=token(reviewer))
        assert recorded.status_code == 200, recorded.text
        assert recorded.json()["decision"]["decision"] == "APPROVE"
        assert recorded.json()["decision"]["reviewer_id"] == approval["reviewer_id"]
        assert recorded.json()["report_version_id"] == reviewed_report["version_id"]
        assert recorded.json()["snapshot_id"] == reviewed_report["snapshot_id"]
        assert page.get_by_label("Case status: Completed").count() == 1  # text label, not colour only
        # Reopening UI: no supervisor controls for analyst/reviewer identities.
        supervisor = "a.supervisor1@synthetic"
        case_url = f"{web}/cases/{case_id}"
        reopen_form = page.get_by_role("form", name="Reopen completed case", exact=True)
        assert reopen_form.count() == 0  # still signed in as the reviewer
        old_version = client.get(f"/api/v1/cases/{case_id}/reports/versions/2", headers=token(supervisor))
        assert old_version.status_code == 200, old_version.text
        assert old_version.json()["version_id"] == reviewed_report["version_id"]
        assert old_version.json()["snapshot_id"] == reviewed_report["snapshot_id"]
        assert old_version.json()["claims"] == reviewed_report["claims"]
        preserved_fields = ("version_id", "version_no", "content_hash", "snapshot_id", "sections", "claims")
        previous = {field: old_version.json()[field] for field in preserved_fields}
        before = client.get(f"/api/v1/cases/{case_id}", headers=token(supervisor)).json()
        completions = [item for item in before["timeline"] if item["to"] == "COMPLETED"]
        page.get_by_role("button", name="Sign out").click()
        sign_in(page, web, analyst)
        page.goto(case_url)
        page.get_by_label("Case status: Completed").wait_for(timeout=30_000)
        assert page.get_by_role("form", name="Reopen completed case", exact=True).count() == 0
        page.get_by_role("button", name="Sign out").click()
        sign_in(page, web, supervisor, mfa="no")
        page.goto(case_url)
        page.get_by_role("link", name="Sign in with MFA", exact=True).wait_for(timeout=30_000)
        assert page.get_by_role("form", name="Reopen completed case", exact=True).count() == 0
        page.get_by_role("button", name="Sign out").click()
        sign_in(page, web, supervisor)
        page.goto(case_url)
        form = page.get_by_role("form", name="Reopen completed case", exact=True)
        form.wait_for(timeout=30_000)
        # Required input prevents empty submission; whitespace is rejected server-side too.
        form.get_by_role("button", name="Reopen case", exact=True).click()
        assert page.get_by_label("Reason for reopening").evaluate("e => e.validity.valueMissing")
        page.get_by_label("Reason for reopening").fill("    ")
        form.get_by_role("button", name="Reopen case", exact=True).click()
        page.get_by_role("alert").filter(has_text="Enter a reason between 3 and 300 characters.").wait_for(
            timeout=30_000
        )
        unchanged = client.get(f"/api/v1/cases/{case_id}", headers=token(supervisor)).json()
        assert unchanged["status"] == "COMPLETED"
        assert unchanged["record_version"] == before["record_version"]
        # Both tabs hold the same case version. One succeeds; the stale form must fail.
        stale = page.context.new_page()
        stale.goto(case_url)
        stale.get_by_label("Reason for reopening").fill("Another supervisor reviewed an older version")
        reason = "New synthetic information requires a renewed investigation"
        page.get_by_label("Reason for reopening").fill(reason)
        page.get_by_role("button", name="Reopen case", exact=True).click()
        page.get_by_text("Case reopened. A new draft requires review.", exact=False).first.wait_for(
            timeout=30_000
        )
        assert "to REWORK" in page.locator("main").inner_text()
        assert page.get_by_role("form", name="Reopen completed case", exact=True).count() == 0
        stale.get_by_role("button", name="Reopen case", exact=True).click()
        stale.get_by_role("alert").filter(has_text="VERSION_CONFLICT").wait_for(timeout=30_000)
        assert stale.get_by_text("Case reopened. A new draft requires review.", exact=False).count() == 0
        stale.close()
        after = client.get(f"/api/v1/cases/{case_id}", headers=token(supervisor)).json()
        assert after["status"] == "REWORK" and after["record_version"] == before["record_version"] + 1
        assert [item for item in after["timeline"] if item["to"] == "COMPLETED"] == completions
        assert after["timeline"][-1]["from"] == "COMPLETED"
        assert after["timeline"][-1]["to"] == "REWORK"
        assert after["timeline"][-1]["reason"] == reason
        old_after = client.get(f"/api/v1/cases/{case_id}/reports/versions/2", headers=token(supervisor))
        assert old_after.status_code == 200, old_after.text
        assert {field: old_after.json()[field] for field in preserved_fields} == previous
        report = client.get(f"/api/v1/cases/{case_id}/reports", headers=token(supervisor))
        assert report.status_code == 200, report.text
        assert report.json()["report"]["status"] == "DRAFT"
        assert report.json()["report"]["current_version_no"] == 3
        # Dashboard uses the same authorized session and never expands case scope.
        page.get_by_role("button", name="Sign out").click()
        sign_in(page, web, analyst)
        page.goto(f"{web}/operations?days=7")
        page.get_by_role("heading", name="Operational dashboard", exact=True).wait_for()
        assert "assigned-cases" in page.locator("main").inner_text()
        assert page.get_by_role("heading", name="Observed connector status", exact=True).count() == 0
        page.get_by_role("button", name="Sign out").click()
        sign_in(page, web, "a.operator1@synthetic")
        page.goto(f"{web}/operations?days=7")
        page.get_by_role("heading", name="Observed connector status", exact=True).wait_for()
        assert "core-banking" in page.locator("main").inner_text()
        assert page.get_by_role("link", name="Open permitted case", exact=True).count() == 0
        assert page.get_by_role("heading", name="Unavailable measurements", exact=True).count() == 1
        browser.close()
