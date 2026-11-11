from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from finagent.documents import parsers
from finagent.documents.ocr import (
    NoOcrProvider,
    TesseractOcrProvider,
    build_ocr_provider,
)


def test_disabled_ocr_provider_fails_closed() -> None:
    provider = NoOcrProvider()

    result = provider.extract(
        "application/pdf",
        b"%PDF-synthetic",
        max_pages=10,
    )

    assert result.status == parsers.OCR_UNAVAILABLE
    assert result.provider == "none"
    assert result.reason == "NO_OCR_PROVIDER"
    assert result.pages == ()


def test_build_disabled_ocr_provider() -> None:
    provider = build_ocr_provider("none")
    assert isinstance(provider, NoOcrProvider)


def test_build_tesseract_ocr_provider() -> None:
    with patch("finagent.documents.ocr.shutil.which", return_value=None):
        provider = build_ocr_provider("tesseract")

    assert isinstance(provider, TesseractOcrProvider)


def test_unknown_ocr_provider_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unsupported OCR backend"):
        build_ocr_provider("not-approved")


def test_tesseract_missing_binary_fails_closed() -> None:
    provider = TesseractOcrProvider(
        tesseract_path=None,
        pdftoppm_path="pdftoppm",
    )
    provider.tesseract_path = None

    result = provider.extract(
        "application/pdf",
        b"%PDF-synthetic",
        max_pages=10,
    )

    assert result.status == parsers.OCR_UNAVAILABLE
    assert result.reason == "TESSERACT_NOT_AVAILABLE"


def test_tesseract_missing_pdf_renderer_fails_closed() -> None:
    provider = TesseractOcrProvider(
        tesseract_path="tesseract",
        pdftoppm_path=None,
    )
    provider.pdftoppm_path = None

    result = provider.extract(
        "application/pdf",
        b"%PDF-synthetic",
        max_pages=10,
    )

    assert result.status == parsers.OCR_UNAVAILABLE
    assert result.reason == "PDF_RENDERER_NOT_AVAILABLE"


def test_tesseract_rejects_unsupported_media_type() -> None:
    provider = TesseractOcrProvider(
        tesseract_path="tesseract",
        pdftoppm_path="pdftoppm",
    )

    result = provider.extract(
        "text/plain",
        b"synthetic",
        max_pages=10,
    )

    assert result.status == parsers.OCR_UNAVAILABLE
    assert result.reason == "OCR_MEDIA_TYPE_UNSUPPORTED"


@pytest.mark.req("SPR-013", "SPR-017")
def test_ocr_keeps_document_bytes_out_of_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = b"%PDF-synthetic; $(untrusted document text)"
    provider = TesseractOcrProvider(
        tesseract_path="C:/Program Files/OCR/tesseract.exe",
        pdftoppm_path="C:/Program Files/PDF/pdftoppm.exe",
    )
    calls: list[list[str]] = []

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        assert kwargs["shell"] is False
        assert kwargs["timeout"] == provider.timeout_s
        assert not any("untrusted document text" in value for value in argv)
        calls.append(argv)
        if argv[0] == provider.pdftoppm_path:
            assert Path(argv[-2]).read_bytes() == payload
            Path(argv[-1] + "-1.png").write_bytes(b"synthetic image")
            return subprocess.CompletedProcess(argv, 0, "", "")
        assert argv[0] == provider.tesseract_path
        assert argv[-3:] == ["stdout", "--psm", "6"]
        return subprocess.CompletedProcess(argv, 0, "Synthetic OCR content", "")

    monkeypatch.setattr("finagent.documents.ocr.subprocess.run", fake_run)
    result = provider.extract("application/pdf", payload, max_pages=1)
    assert result.status == parsers.PARSED
    assert len(calls) == 2
    assert result.pages[0].text == "Synthetic OCR content"
