"""The browser evidence gate must not pass missing or incomplete reports."""

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from scripts.verify_browser_report import EXPECTED, verify


def report(path: Path, *, missing: bool = False, result: str | None = None) -> None:
    root = ET.Element("testsuites")
    suite = ET.SubElement(root, "testsuite")
    names = sorted(EXPECTED)
    if missing:
        names.pop()
    for index, name in enumerate(names):
        case = ET.SubElement(suite, "testcase", name=f"{name}[synthetic]" if index == 0 else name)
        if index == 0 and result:
            ET.SubElement(case, result)
    ET.ElementTree(root).write(path, encoding="utf-8")


def test_complete_passing_report_is_accepted(tmp_path: Path) -> None:
    path = tmp_path / "browser.xml"
    report(path)
    verify(path)


@pytest.mark.parametrize("outcome", ["failure", "error", "skipped"])
def test_unsuccessful_scenarios_are_rejected(tmp_path: Path, outcome: str) -> None:
    path = tmp_path / "browser.xml"
    report(path, result=outcome)
    with pytest.raises(ValueError, match="failures, errors or skipped"):
        verify(path)


def test_missing_or_invalid_report_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "browser.xml"
    with pytest.raises(ValueError, match="missing or invalid"):
        verify(path)
    path.write_text("<invalid", encoding="utf-8")
    with pytest.raises(ValueError, match="missing or invalid"):
        verify(path)
    report(path, missing=True)
    with pytest.raises(ValueError, match="missing scenarios"):
        verify(path)


def test_suite_level_error_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "browser.xml"
    report(path)
    tree = ET.parse(path)  # noqa: S314 - test-generated fixture
    tree.getroot().find("testsuite").set("errors", "1")
    tree.write(path, encoding="utf-8")
    with pytest.raises(ValueError, match="failures, errors or skipped"):
        verify(path)
