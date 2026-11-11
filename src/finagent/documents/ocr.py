"""OCR provider port for scanned/image-only documents.

OCR is deliberately outside the isolated structural parser. The parser first
determines whether a document has a usable text layer. Only an explicit
OCR_UNAVAILABLE result may be offered to this port.

The default provider is disabled and preserves OCR_UNAVAILABLE exactly.
No provider may turn an empty OCR result into a successful parse.
"""

from __future__ import annotations

import shutil
import subprocess  # nosec B404 - required for configured local OCR binaries
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from finagent.documents import parsers


@dataclass(frozen=True)
class OcrPage:
    """Recognised text for one source page."""

    page: int
    text: str


@dataclass(frozen=True)
class OcrResult:
    """Provider-neutral OCR result."""

    status: str
    provider: str
    pages: tuple[OcrPage, ...] = ()
    reason: str | None = None
    warnings: tuple[str, ...] = ()


class OcrPort(Protocol):
    """OCR provider contract."""

    def extract(
        self,
        media_type: str,
        data: bytes,
        *,
        max_pages: int,
    ) -> OcrResult: ...


class NoOcrProvider:
    """Fail-closed provider used when OCR has not been configured."""

    name = "none"

    def extract(
        self,
        media_type: str,
        data: bytes,
        *,
        max_pages: int,
    ) -> OcrResult:
        del media_type, data, max_pages
        return OcrResult(
            status=parsers.OCR_UNAVAILABLE,
            provider=self.name,
            reason="NO_OCR_PROVIDER",
        )


class TesseractOcrProvider:
    """Local OCR adapter using pdftoppm + Tesseract.

    Both executables must already be present on PATH. The application does not
    install or download them at runtime.
    """

    name = "tesseract"

    def __init__(
        self,
        *,
        tesseract_path: str | None = None,
        pdftoppm_path: str | None = None,
        timeout_s: float = 60.0,
    ) -> None:
        self.tesseract_path = tesseract_path or shutil.which("tesseract")
        self.pdftoppm_path = pdftoppm_path or shutil.which("pdftoppm")
        self.timeout_s = timeout_s

    def extract(
        self,
        media_type: str,
        data: bytes,
        *,
        max_pages: int,
    ) -> OcrResult:
        if media_type != "application/pdf":
            return OcrResult(
                status=parsers.OCR_UNAVAILABLE,
                provider=self.name,
                reason="OCR_MEDIA_TYPE_UNSUPPORTED",
            )

        if not self.tesseract_path:
            return OcrResult(
                status=parsers.OCR_UNAVAILABLE,
                provider=self.name,
                reason="TESSERACT_NOT_AVAILABLE",
            )

        if not self.pdftoppm_path:
            return OcrResult(
                status=parsers.OCR_UNAVAILABLE,
                provider=self.name,
                reason="PDF_RENDERER_NOT_AVAILABLE",
            )

        if max_pages < 1:
            return OcrResult(
                status=parsers.LIMIT_EXCEEDED,
                provider=self.name,
                reason="PAGE_LIMIT",
            )

        try:
            with tempfile.TemporaryDirectory(prefix="finagent-ocr-") as temp_dir:
                root = Path(temp_dir)
                source = root / "source.pdf"
                source.write_bytes(data)

                render_prefix = root / "page"

                render = subprocess.run(  # noqa: S603 # nosec B603 - trusted executable; generated local paths; no shell
                    [
                        self.pdftoppm_path,
                        "-f",
                        "1",
                        "-l",
                        str(max_pages + 1),
                        "-png",
                        "-r",
                        "200",
                        str(source),
                        str(render_prefix),
                    ],
                    shell=False,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_s,
                    check=False,
                )

                if render.returncode != 0:
                    return OcrResult(
                        status=parsers.OCR_UNAVAILABLE,
                        provider=self.name,
                        reason="PDF_RENDER_FAILED",
                    )

                images = sorted(root.glob("page-*.png"))

                if len(images) > max_pages:
                    return OcrResult(
                        status=parsers.LIMIT_EXCEEDED,
                        provider=self.name,
                        reason="PAGE_LIMIT",
                    )

                if not images:
                    return OcrResult(
                        status=parsers.OCR_UNAVAILABLE,
                        provider=self.name,
                        reason="NO_RENDERED_PAGES",
                    )

                pages: list[OcrPage] = []
                warnings: list[str] = []

                for page_no, image in enumerate(images, start=1):
                    ocr = subprocess.run(  # noqa: S603 # nosec B603 - trusted executable; generated local paths; no shell
                        [
                            self.tesseract_path,
                            str(image),
                            "stdout",
                            "--psm",
                            "6",
                        ],
                        shell=False,
                        capture_output=True,
                        text=True,
                        timeout=self.timeout_s,
                        check=False,
                    )

                    if ocr.returncode != 0:
                        warnings.append(f"OCR_PAGE_{page_no}_FAILED")
                        continue

                    text = ocr.stdout.strip()

                    if text:
                        pages.append(OcrPage(page=page_no, text=text))
                    else:
                        warnings.append(f"OCR_PAGE_{page_no}_EMPTY")

                if not pages:
                    return OcrResult(
                        status=parsers.OCR_UNAVAILABLE,
                        provider=self.name,
                        reason="OCR_EMPTY",
                        warnings=tuple(warnings),
                    )

                return OcrResult(
                    status=parsers.PARSED,
                    provider=self.name,
                    pages=tuple(pages),
                    warnings=tuple(warnings),
                )

        except subprocess.TimeoutExpired:
            return OcrResult(
                status=parsers.TIMEOUT,
                provider=self.name,
                reason="OCR_TIMEOUT",
            )
        except OSError:
            return OcrResult(
                status=parsers.OCR_UNAVAILABLE,
                provider=self.name,
                reason="OCR_EXECUTION_FAILED",
            )


def build_ocr_provider(backend: str) -> OcrPort:
    """Build only explicitly supported OCR providers."""

    if backend == "none":
        return NoOcrProvider()

    if backend == "tesseract":
        return TesseractOcrProvider()

    raise ValueError(f"Unsupported OCR backend: {backend}")
