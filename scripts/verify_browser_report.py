"""Require actual passing results for the synthetic browser acceptance scenarios."""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

EXPECTED = {
    (
        "tests.browser.test_uncertain_action_browser",
        "test_expired_report_edit_keeps_unsaved_text[report_edit_auth_expired]",
    ),
    ("tests.browser.test_ui_journey", "test_case_page_is_restored_after_synthetic_sign_in"),
    ("tests.browser.test_ui_journey", "test_workspace_case_is_restored_after_synthetic_sign_in"),
    ("tests.browser.test_ui_journey", "test_workspace_snapshot_selection_is_restored_after_sign_in"),
    ("tests.browser.test_ui_journey", "test_case_return_does_not_grant_cross_tenant_access"),
    (
        "tests.browser.test_ui_journey",
        "test_api_expired_case_read_restores_page_after_sign_in[expired_case_evidence_read]",
    ),
    (
        "tests.browser.test_ui_journey",
        "test_api_expired_workspace_read_restores_case_after_sign_in[expired_case_evidence_read]",
    ),
    (
        "tests.browser.test_ui_journey",
        "test_analyst_and_reviewer_complete_a_case_in_the_browser[case_update_gateway_error]",
    ),
    ("tests.browser.test_case_checklist_keyboard", "test_case_checklist_can_be_collapsed_with_keyboard"),
    ("tests.browser.test_policy_admin_table_keyboard", "test_policy_table_scrolls_with_keyboard_on_mobile"),
    (
        "tests.browser.test_workspace_mobile_navigation",
        "test_mobile_sidebar_is_hidden_until_opened_and_escape_returns_focus",
    ),
    ("tests.browser.test_workspace_table_keyboard", "test_transaction_table_scrolls_with_keyboard_on_mobile"),
    ("tests.browser.test_uncertain_action_browser", "test_interrupted_start_requires_case_refresh"),
    ("tests.browser.test_uncertain_action_browser", "test_lost_browser_response_does_not_repeat_start"),
    (
        "tests.browser.test_uncertain_action_browser",
        "test_api_response_lost_after_start_requires_refresh[drop_workflow_response]",
    ),
    (
        "tests.browser.test_uncertain_action_browser",
        "test_lost_case_update_response_requires_reload[drop_case_update_response]",
    ),
    (
        "tests.browser.test_uncertain_action_browser",
        "test_lost_case_update_response_requires_reload[case_update_gateway_error]",
    ),
    (
        "tests.browser.test_uncertain_action_browser",
        "test_report_edit_gateway_error_requires_reload[report_edit_gateway_error]",
    ),
    (
        "tests.browser.test_uncertain_action_browser",
        "test_document_upload_gateway_error_requires_reload[document_upload_gateway_error]",
    ),
    (
        "tests.browser.test_uncertain_action_browser",
        "test_workflow_resume_gateway_error_requires_reload[workflow_resume_gateway_error]",
    ),
    (
        "tests.browser.test_uncertain_action_browser",
        "test_snapshot_gateway_error_requires_reload[snapshot_gateway_error]",
    ),
    (
        "tests.browser.test_uncertain_action_browser",
        "test_field_correction_gateway_error_requires_reload[field_correction_gateway_error]",
    ),
    (
        "tests.browser.test_uncertain_action_browser",
        "test_workflow_cancel_gateway_error_requires_reload[workflow_cancel_gateway_error]",
    ),
    (
        "tests.browser.test_uncertain_action_browser",
        "test_report_withdrawal_gateway_error_requires_reload[report_withdrawal_gateway_error]",
    ),
}


def verify(path: Path) -> None:
    try:
        root = ET.parse(path).getroot()  # noqa: S314 - local CI-generated report
    except (OSError, ET.ParseError) as exc:
        raise ValueError("Browser test report is missing or invalid") from exc
    cases = root.findall(".//testcase")
    names = {(case.get("classname", ""), case.get("name", "")) for case in cases}
    missing = EXPECTED - names
    if missing:
        descriptions = ", ".join(".".join(item) for item in sorted(missing))
        raise ValueError(f"Browser test report is missing scenarios: {descriptions}")
    for suite in root.findall(".//testsuite"):
        if any(suite.get(field, "0") != "0" for field in ("failures", "errors", "skipped")):
            raise ValueError("Browser test report contains failures, errors or skipped tests")
    if any(case.find(tag) is not None for case in cases for tag in ("failure", "error", "skipped")):
        raise ValueError("Browser test report contains failures, errors or skipped tests")


if __name__ == "__main__":
    try:
        verify(Path(sys.argv[1]))
    except (IndexError, ValueError) as error:
        print(error, file=sys.stderr)
        raise SystemExit(1) from error
    print("All required synthetic browser scenarios were reported as passed.")
