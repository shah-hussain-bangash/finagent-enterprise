"""Connector HTTP client (DIR-021; SPR-013/014/016; prompt §12-13 "no arbitrary URLs").

The base URL is fixed per source by configuration; callers supply only a path TEMPLATE from code and
parameter values that must match a strict identifier pattern, are URL-encoded and can never change
host, scheme or path structure. Every response must declare a supported `schema_version` and a
timezone-aware `as_of`; anything else is rejected rather than silently misparsed (DIR-038).
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any, Literal
from urllib.parse import quote

import httpx

from finagent.core.correlation import get_correlation_id
from finagent.integrations.observations import ConnectorSample
from finagent.integrations.resilience import (
    CircuitBreaker,
    ConnectorError,
    ErrorKind,
    RetryPolicy,
    call_with_resilience,
)
from finagent.security.rate_limit import Bucket, InMemoryRateLimiter

log = logging.getLogger("finagent.connectors")

_PARAM = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_TEMPLATE_FIELD = re.compile(r"\{([a-z_]+)\}")


def _classify(response: httpx.Response, source: str) -> ConnectorError | None:
    code = response.status_code
    if code < 400:
        return None
    if code == 401:
        return ConnectorError(ErrorKind.UNAUTHORIZED, source)
    if code == 403:
        revoked = "CONSENT_REVOKED" in response.text[:200]
        return ConnectorError(ErrorKind.REVOKED if revoked else ErrorKind.FORBIDDEN, source)
    if code == 404:
        return ConnectorError(ErrorKind.NOT_FOUND, source)
    if code == 409:
        return ConnectorError(ErrorKind.CONFLICT, source)
    if code == 429:
        retry = response.headers.get("Retry-After", "")
        return ConnectorError(ErrorKind.RATE_LIMITED, source, float(retry) if retry.isdigit() else None)
    if code >= 500:
        return ConnectorError(ErrorKind.UNAVAILABLE, source)
    return ConnectorError(ErrorKind.INVALID_REQUEST, source)


class ConnectorHttpClient:
    def __init__(
        self,
        source: str,
        base_url: str,
        api_key: str,
        *,
        timeout_s: float = 5.0,
        policy: RetryPolicy | None = None,
        breaker: CircuitBreaker | None = None,
        rate_per_minute: int = 1200,
        supported_versions: frozenset[str] = frozenset({"1.0"}),
        http: httpx.Client | None = None,
        observe: Callable[[ConnectorSample], None] | None = None,
    ) -> None:
        self.source = source
        self._observe = observe
        self._key, self._versions = api_key, supported_versions
        self._policy, self.breaker = policy or RetryPolicy(), breaker or CircuitBreaker()
        self._limiter, self._bucket = (
            InMemoryRateLimiter(),
            Bucket(rate_per_minute, max(1, rate_per_minute // 60)),
        )
        # TLS verification is httpx's default and is never disabled (SPR-014).
        self._http = http or httpx.Client(base_url=base_url, timeout=timeout_s, follow_redirects=False)

    def _path(self, template: str, params: dict[str, str]) -> str:
        names = _TEMPLATE_FIELD.findall(template)
        if set(names) != set(params):
            raise ConnectorError(ErrorKind.INVALID_REQUEST, self.source)
        for value in params.values():
            if not _PARAM.fullmatch(value):
                raise ConnectorError(ErrorKind.INVALID_REQUEST, self.source)
        return template.format(**{k: quote(v, safe="") for k, v in params.items()})

    def _once(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        decision = self._limiter.hit(self.source, self._bucket)
        if not decision.allowed:
            raise ConnectorError(ErrorKind.RATE_LIMITED, self.source, float(decision.retry_after_s))
        headers = {
            "Authorization": f"Bearer {self._key}",
            "X-Correlation-Id": get_correlation_id(),
            "Accept": "application/json",
            **kwargs.pop("headers", {}),
        }
        try:
            response = self._http.request(method, path, headers=headers, **kwargs)
        except httpx.TimeoutException as exc:
            raise ConnectorError(ErrorKind.TIMEOUT, self.source) from exc
        except httpx.TransportError as exc:
            raise ConnectorError(ErrorKind.UNAVAILABLE, self.source) from exc
        error = _classify(response, self.source)
        if error is not None:
            raise error
        try:
            body = response.json()
        except ValueError as exc:
            raise ConnectorError(ErrorKind.INVALID_RESPONSE, self.source) from exc
        if not isinstance(body, dict) or body.get("schema_version") not in self._versions:
            raise ConnectorError(ErrorKind.SCHEMA_UNSUPPORTED, self.source)
        try:
            as_of = datetime.fromisoformat(str(body.get("as_of")))
        except ValueError as exc:
            raise ConnectorError(ErrorKind.INVALID_RESPONSE, self.source) from exc
        if as_of.tzinfo is None:
            raise ConnectorError(ErrorKind.INVALID_RESPONSE, self.source)
        return body

    def _observed[T](
        self, operation: Literal["JSON", "BYTES"], fn: Callable[[], T], policy: RetryPolicy
    ) -> T:
        started, attempts = time.monotonic(), 0
        error: str | None = "UNEXPECTED"

        def attempt() -> T:
            nonlocal attempts
            attempts += 1
            return fn()

        try:
            result = call_with_resilience(self.source, attempt, policy, self.breaker)
            error = None
            return result
        except ConnectorError as exc:
            error = exc.kind.value
            raise
        except Exception:
            error = "UNEXPECTED"
            raise
        finally:
            if self._observe is not None:
                sample = ConnectorSample(
                    operation,
                    "FAILED" if error else "SUCCEEDED",
                    error,
                    attempts,
                    max(0, int((time.monotonic() - started) * 1000)),
                )
                try:
                    self._observe(sample)
                except Exception:
                    # Observation loss must never retry or change a business effect.
                    log.warning("connector_observation_unavailable")

    def request_json(
        self,
        method: str,
        template: str,
        params: dict[str, str] | None = None,
        *,
        query: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        retry: bool = True,
    ) -> dict[str, Any]:
        path = self._path(template, params or {})
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else {}
        policy = self._policy if retry else RetryPolicy(max_attempts=1)
        return self._observed(
            "JSON", lambda: self._once(method, path, params=query, json=json_body, headers=headers), policy
        )

    def get_bytes(self, template: str, params: dict[str, str], max_bytes: int) -> tuple[bytes, str]:
        path = self._path(template, params)

        def once() -> tuple[bytes, str]:
            try:
                with self._http.stream("GET", path, headers={"Authorization": f"Bearer {self._key}"}) as r:
                    error = _classify(r, self.source)
                    if error is not None:
                        r.read()
                        raise _classify(r, self.source) or error
                    data = bytearray()
                    for chunk in r.iter_bytes():
                        data += chunk
                        if len(data) > max_bytes:  # bounded download (SPR-017)
                            raise ConnectorError(ErrorKind.INVALID_RESPONSE, self.source)
                    return bytes(data), r.headers.get("content-type", "application/octet-stream")
            except httpx.TimeoutException as exc:
                raise ConnectorError(ErrorKind.TIMEOUT, self.source) from exc
            except httpx.TransportError as exc:
                raise ConnectorError(ErrorKind.UNAVAILABLE, self.source) from exc

        return self._observed("BYTES", once, self._policy)

    def close(self) -> None:
        self._http.close()
