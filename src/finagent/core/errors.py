"""Shared machine-readable error model (prompt §42; SRS §12).

Messages are safe for end users. Never place stack traces, provider payloads, secrets or another
tenant's identifiers in an AppError.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class ErrorCode(StrEnum):
    MALFORMED_REQUEST = "MALFORMED_REQUEST"
    SCHEMA_VALIDATION_FAILED = "SCHEMA_VALIDATION_FAILED"
    IDEMPOTENCY_KEY_REQUIRED = "IDEMPOTENCY_KEY_REQUIRED"
    PRECONDITION_REQUIRED = "PRECONDITION_REQUIRED"
    UNAUTHENTICATED = "UNAUTHENTICATED"
    MFA_REQUIRED = "MFA_REQUIRED"
    FORBIDDEN = "FORBIDDEN"
    FEATURE_DISABLED = "FEATURE_DISABLED"
    NOT_FOUND = "NOT_FOUND"
    CASE_INVALID_STATE = "CASE_INVALID_STATE"
    RUN_INVALID_STATE = "RUN_INVALID_STATE"
    OPERATION_INVALID_STATE = "OPERATION_INVALID_STATE"
    VERSION_CONFLICT = "VERSION_CONFLICT"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    ALERT_PAYLOAD_CONFLICT = "ALERT_PAYLOAD_CONFLICT"
    SEMANTIC_VALIDATION_FAILED = "SEMANTIC_VALIDATION_FAILED"
    PAYLOAD_TOO_LARGE = "PAYLOAD_TOO_LARGE"
    UNSUPPORTED_MEDIA_TYPE = "UNSUPPORTED_MEDIA_TYPE"
    RESOURCE_UNAVAILABLE = "RESOURCE_UNAVAILABLE"
    LINK_EXPIRED = "LINK_EXPIRED"
    RATE_LIMITED = "RATE_LIMITED"
    DEPENDENCY_UNAVAILABLE = "DEPENDENCY_UNAVAILABLE"
    INTERNAL_ERROR = "INTERNAL_ERROR"


HTTP_STATUS: dict[ErrorCode, int] = {
    ErrorCode.MALFORMED_REQUEST: 400,
    ErrorCode.SCHEMA_VALIDATION_FAILED: 400,
    ErrorCode.IDEMPOTENCY_KEY_REQUIRED: 400,
    ErrorCode.PRECONDITION_REQUIRED: 400,
    ErrorCode.UNAUTHENTICATED: 401,
    ErrorCode.MFA_REQUIRED: 401,  # step-up: re-authenticate with MFA (RFC 9470 semantics)
    ErrorCode.FORBIDDEN: 403,
    ErrorCode.FEATURE_DISABLED: 403,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.CASE_INVALID_STATE: 409,
    ErrorCode.RUN_INVALID_STATE: 409,
    ErrorCode.OPERATION_INVALID_STATE: 409,
    ErrorCode.VERSION_CONFLICT: 409,
    ErrorCode.IDEMPOTENCY_CONFLICT: 409,
    ErrorCode.ALERT_PAYLOAD_CONFLICT: 409,
    ErrorCode.PAYLOAD_TOO_LARGE: 413,
    ErrorCode.UNSUPPORTED_MEDIA_TYPE: 415,
    ErrorCode.RESOURCE_UNAVAILABLE: 409,
    ErrorCode.LINK_EXPIRED: 410,
    ErrorCode.SEMANTIC_VALIDATION_FAILED: 422,
    ErrorCode.RATE_LIMITED: 429,
    ErrorCode.DEPENDENCY_UNAVAILABLE: 503,
    ErrorCode.INTERNAL_ERROR: 500,
}

SAFE_MESSAGES: dict[ErrorCode, str] = {
    ErrorCode.NOT_FOUND: "The requested resource was not found.",
    ErrorCode.UNAUTHENTICATED: "Authentication is required or the credentials are invalid.",
    ErrorCode.FORBIDDEN: "You are not permitted to perform this action.",
    ErrorCode.MFA_REQUIRED: "This action requires multi-factor authentication. Sign in again with MFA.",
    ErrorCode.FEATURE_DISABLED: "This capability is disabled by configuration.",
    ErrorCode.RATE_LIMITED: "Too many requests. Retry after the indicated delay.",
    ErrorCode.INTERNAL_ERROR: "An unexpected error occurred. Quote the correlation ID to support.",
}


class AppError(Exception):
    def __init__(
        self,
        code: ErrorCode,
        message: str | None = None,
        details: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.code = code
        self.message = message or SAFE_MESSAGES.get(code, code.value.replace("_", " ").capitalize() + ".")
        self.details = details or {}
        self.headers = headers or {}
        super().__init__(self.message)

    @property
    def status_code(self) -> int:
        return HTTP_STATUS[self.code]


def not_found() -> AppError:
    """Single constructor so 'absent' and 'concealed' responses are identical (prompt §18)."""
    return AppError(ErrorCode.NOT_FOUND)


def error_body(
    code: ErrorCode, message: str, correlation_id: str, details: dict[str, Any] | None = None
) -> dict[str, Any]:
    return {
        "error": {
            "code": code.value,
            "message": message,
            "correlation_id": correlation_id,
            "details": details or {},
        }
    }
