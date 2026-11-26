"""The integration evidence gate fails closed on incomplete reports."""

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from scripts.verify_integration_report import verify


def write_report(path: Path, *, outcome: str | None = None) -> None:
    root = ET.Element("testsuites")
    suite = ET.SubElement(root, "testsuite", tests="1", errors="0", failures="0", skipped="0")
    case = ET.SubElement(suite, "testcase", name="test_synthetic")
    if outcome:
        ET.SubElement(case, outcome)
    ET.ElementTree(root).write(path, encoding="utf-8")


def test_passing_report_is_accepted(tmp_path: Path) -> None:
    path = tmp_path / "report.xml"
    write_report(path)
    verify(path)


@pytest.mark.parametrize("outcome", ["error", "failure", "skipped"])
def test_unsuccessful_test_is_rejected(tmp_path: Path, outcome: str) -> None:
    path = tmp_path / "report.xml"
    write_report(path, outcome=outcome)
    with pytest.raises(ValueError, match="failures, errors or skipped"):
        verify(path)


def test_missing_malformed_and_empty_reports_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "report.xml"
    with pytest.raises(ValueError, match="missing or invalid"):
        verify(path)
    path.write_text("<invalid", encoding="utf-8")
    with pytest.raises(ValueError, match="missing or invalid"):
        verify(path)
    path.write_text("<testsuites><testsuite /></testsuites>", encoding="utf-8")
    with pytest.raises(ValueError, match="no executed tests"):
        verify(path)
