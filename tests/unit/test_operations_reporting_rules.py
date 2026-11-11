"""Missing-data semantics and connector observation behavior without a live service."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from finagent.integrations.http_client import ConnectorHttpClient
from finagent.integrations.observations import ConnectorSample
from finagent.integrations.resilience import (
    CircuitBreaker,
    ConnectorError,
    ErrorKind,
    RetryPolicy,
)
from finagent.services.reporting_service.dashboard import (
    connector_state,
    ratio,
    safe_code,
)

NOW = datetime(2026, 10, 21, 6, tzinfo=UTC)


@pytest.mark.parametrize(
    ("age", "outcome", "expected"),
    [
        (None, None, "NO_OBSERVATIONS"),
        (-1, "SUCCEEDED", "NO_OBSERVATIONS"),
        (301, "SUCCEEDED", "STALE"),
        (300, "SUCCEEDED", "LAST_CALL_SUCCEEDED"),
        (1, "FAILED", "LAST_CALL_FAILED"),
    ],
)
def test_connector_state_never_invents_a_healthy_sample(
    age: int | None, outcome: str | None, expected: str
) -> None:
    when = NOW - timedelta(seconds=age) if age is not None else None
    assert connector_state(when, outcome, NOW) == expected


def test_zero_denominator_is_not_a_perfect_score() -> None:
    assert ratio(0, 0) == {"numerator": 0, "denominator": 0, "value": None}
    assert ratio(1, 4)["value"] == 0.25


@pytest.mark.parametrize("value", [None, "https://private.example/secret", 'customer "name"', "x\nsecret"])
def test_unsafe_labels_are_omitted(value: str | None) -> None:
    assert safe_code(value) == "OTHER"


def test_logical_connector_call_records_retry_count_without_payload() -> None:
    samples: list[ConnectorSample] = []
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ReadTimeout("sensitive transport detail", request=request)
        return httpx.Response(
            200, json={"schema_version": "1.0", "as_of": NOW.isoformat(), "private": "customer-secret"}
        )

    with httpx.Client(base_url="https://synthetic.invalid", transport=httpx.MockTransport(handler)) as http:
        connector = ConnectorHttpClient(
            "core-banking",
            "https://synthetic.invalid",
            "synthetic-key",
            http=http,
            policy=RetryPolicy(2, 0, 0),
            observe=samples.append,
        )
        assert connector.request_json("GET", "/synthetic")["private"] == "customer-secret"
    assert len(samples) == 1 and samples[0].attempts == 2 and samples[0].outcome == "SUCCEEDED"
    assert samples[0].operation == "JSON" and "customer-secret" not in repr(samples)


def test_open_circuit_records_zero_transport_attempts() -> None:
    samples: list[ConnectorSample] = []
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("synthetic", request=request)

    with httpx.Client(base_url="https://synthetic.invalid", transport=httpx.MockTransport(handler)) as http:
        connector = ConnectorHttpClient(
            "core-banking",
            "https://synthetic.invalid",
            "synthetic-key",
            http=http,
            policy=RetryPolicy(1, 0, 0),
            breaker=CircuitBreaker(failure_threshold=1),
            observe=samples.append,
        )
        for _ in range(2):
            with pytest.raises(ConnectorError):
                connector.request_json("GET", "/synthetic")
    assert calls == 1 and samples[-1].attempts == 0 and samples[-1].error_code == ErrorKind.CIRCUIT_OPEN.value


def test_telemetry_failure_does_not_repeat_business_effect(caplog: pytest.LogCaptureFixture) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"schema_version": "1.0", "as_of": NOW.isoformat()})

    def unavailable(sample: ConnectorSample) -> None:
        raise RuntimeError("secret-sink-exception")

    with httpx.Client(base_url="https://synthetic.invalid", transport=httpx.MockTransport(handler)) as http:
        connector = ConnectorHttpClient(
            "secret-source", "https://synthetic.invalid", "secret-key", http=http, observe=unavailable
        )
        connector.request_json("POST", "/synthetic", retry=False)
    assert calls == 1
    assert "connector_observation_unavailable" in caplog.text
    assert all(
        secret not in caplog.text for secret in ("secret-source", "secret-key", "secret-sink-exception")
    )


def test_byte_download_emits_one_observation() -> None:
    samples: list[ConnectorSample] = []
    with httpx.Client(
        base_url="https://synthetic.invalid",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, content=b"synthetic", headers={"content-type": "application/pdf"}
            )
        ),
    ) as http:
        connector = ConnectorHttpClient(
            "core-banking", "https://synthetic.invalid", "synthetic-key", http=http, observe=samples.append
        )
        assert connector.get_bytes("/synthetic/{id}", {"id": "doc-1"}, 100)[0] == b"synthetic"
    assert len(samples) == 1 and samples[0].operation == "BYTES"


def test_schema_envelope_failure_is_an_observed_failure() -> None:
    samples: list[ConnectorSample] = []
    with httpx.Client(
        base_url="https://synthetic.invalid",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"schema_version": "unsupported"})
        ),
    ) as http:
        connector = ConnectorHttpClient(
            "core-banking", "https://synthetic.invalid", "synthetic-key", http=http, observe=samples.append
        )
        with pytest.raises(ConnectorError):
            connector.request_json("GET", "/synthetic")
    assert samples[0].error_code == "SCHEMA_UNSUPPORTED" and samples[0].attempts == 1
