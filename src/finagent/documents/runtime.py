"""Build the document ports from Settings (one place, so API and worker use identical adapters)."""

from __future__ import annotations

from finagent.core.config import Settings
from finagent.core.crypto import Encryptor
from finagent.documents.ocr import OcrPort
from finagent.documents.ocr import build_ocr_provider as build_ocr_adapter
from finagent.documents.parsers import ParseLimits
from finagent.documents.sandbox import SandboxConfig
from finagent.documents.scanner import MalwareScanner, build_scanner
from finagent.documents.storage import AzureBlobStore, BlobStore, LocalBlobStore


def build_store(settings: Settings) -> BlobStore:
    enc = Encryptor(settings.data_key())
    if settings.blob_backend == "azure" and settings.azure_blob_account_url:
        return AzureBlobStore(settings.azure_blob_account_url, settings.azure_blob_container, enc)
    return LocalBlobStore(settings.blob_local_root, enc)


def build_document_scanner(settings: Settings) -> MalwareScanner:
    return build_scanner(settings.malware_scanner, settings.clamd_host, settings.clamd_port)


def build_ocr_provider(settings: Settings) -> OcrPort:
    """Build the configured OCR adapter.

    `none` is intentionally the only accepted backend until a concrete
    provider is implemented and approved.
    """

    return build_ocr_adapter(settings.ocr_backend)


def sandbox_config(settings: Settings) -> SandboxConfig:
    return SandboxConfig(
        timeout_s=settings.parser_timeout_seconds,
        memory_bytes=settings.parser_memory_mb * 1024 * 1024,
        limits=ParseLimits(
            max_pages=settings.parser_max_pages,
            max_rows=settings.parser_max_rows,
            max_compression_ratio=settings.parser_max_compression_ratio,
            max_uncompressed_bytes=settings.parser_max_uncompressed_mb * 1024 * 1024,
        ),
    )
