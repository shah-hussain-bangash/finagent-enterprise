"""Require actual passing results for the synthetic browser acceptance scenarios."""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

EXPECTED = {
    "test_analyst_and_reviewer_complete_a_case_in_the_browser",
    "test_case_checklist_can_be_collapsed_with_keyboard",
    "test_policy_table_scrolls_with_keyboard_on_mobile",
    "test_mobile_sidebar_is_hidden_until_opened_and_escape_returns_focus",
    "test_transaction_table_scrolls_with_keyboard_on_mobile",
    "test_interrupted_start_requires_case_refresh",
    "test_lost_browser_response_does_not_repeat_start",
    "test_api_response_lost_after_start_requires_refresh",
    "test_lost_case_update_response_requires_reload",
}


def verify(path: Path) -> None:
    try:
        root = ET.parse(path).getroot()  # noqa: S314 - local CI-generated report
    except (OSError, ET.ParseError) as exc:
        raise ValueError("Browser test report is missing or invalid") from exc
    cases = root.findall(".//testcase")
    names = {case.get("name", "").split("[", 1)[0] for case in cases}
    missing = EXPECTED - names
    if missing:
        raise ValueError(f"Browser test report is missing scenarios: {', '.join(sorted(missing))}")
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
