"""Reject missing, empty or unsuccessful integration test evidence."""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def verify(path: Path) -> None:
    try:
        root = ET.parse(path).getroot()  # noqa: S314 - locally generated CI report
        suites = list(root.iter("testsuite"))
        cases = list(root.iter("testcase"))
        if not suites or not cases:
            raise ValueError("Integration report contains no executed tests")
        if any(
            int(suite.get(field, "0")) != 0 for suite in suites for field in ("errors", "failures", "skipped")
        ):
            raise ValueError("Integration report contains failures, errors or skipped tests")
        if any(case.find(field) is not None for case in cases for field in ("error", "failure", "skipped")):
            raise ValueError("Integration report contains failures, errors or skipped tests")
    except (OSError, ET.ParseError) as exc:
        raise ValueError("Integration report is missing or invalid") from exc


if __name__ == "__main__":
    try:
        verify(Path(sys.argv[1]))
    except (IndexError, ValueError) as error:
        print(error, file=sys.stderr)
        raise SystemExit(1) from error
    print("Integration report contains executed tests without failures, errors or skips.")
