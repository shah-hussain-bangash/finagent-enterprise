"""Pure units for the workflow foundation: crypto, envelopes, faults, config, Service Bus mapping."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from finagent.core.config import Settings
from finagent.core.crypto import DecryptionError, Encryptor, sign, verify
from finagent.orchestration.faults import FaultInjector, SimulatedCrash
from finagent.worker.events import EventEnvelope
from finagent.worker.queue import ServiceBusQueue

KEY = b"0" * 32


def envelope(**over: Any) -> EventEnvelope:
    base: dict[str, Any] = {
        "event_id": uuid.uuid4(),
        "event_type": "workflow.started.v1",
        "schema_version": "1",
        "tenant_ref": uuid.uuid4(),
        "resource_id": "run-1",
        "occurred_at": datetime.now(UTC),
        "correlation_id": "corr-unit-0001",
        "payload": {"run_id": "run-1"},
    }
    base.update(over)
    return EventEnvelope(**base)


@pytest.mark.release_blocker
@pytest.mark.req("DIR-032", "SPR-010")
def test_encryption_is_authenticated_and_bound_to_associated_data() -> None:
    enc = Encryptor(KEY)
    blob = enc.encrypt(b"case-ref", b"tenant-a:run-1")
    assert b"case-ref" not in blob and enc.decrypt(blob, b"tenant-a:run-1") == b"case-ref"
    assert enc.encrypt(b"x", b"a") != enc.encrypt(b"x", b"a")  # random nonce
    for aad, data in ((b"tenant-b:run-1", blob), (b"tenant-a:run-1", blob[:-1] + bytes([blob[-1] ^ 1]))):
        with pytest.raises(DecryptionError):
            enc.decrypt(data, aad)
    with pytest.raises(ValueError):
        Encryptor(b"short")


@pytest.mark.release_blocker
@pytest.mark.req("DIR-022", "SPR-007", "NQ-020")
def test_envelope_signature_covers_every_field() -> None:
    env = envelope().signed(KEY)
    assert env.signature_valid(KEY) and not env.signature_valid(b"1" * 32)
    for field, value in (
        ("tenant_ref", uuid.uuid4()),
        ("event_type", "case.completed.v1"),
        ("payload", {"run_id": "run-2"}),
        ("resource_id", "run-2"),
    ):
        tampered = env.model_copy(update={field: value})
        assert not tampered.signature_valid(KEY), field
    assert not envelope().signature_valid(KEY)  # unsigned
    assert verify(KEY, b"data", sign(KEY, b"data")) and not verify(KEY, b"data", "0" * 64)
    with pytest.raises(ValidationError):
        EventEnvelope.model_validate({**json.loads(env.model_dump_json()), "role": "ADMIN"})


@pytest.mark.req("NQ-019")
def test_fault_spec_parsing_and_crash_semantics() -> None:
    faults = FaultInjector.parse("after_effect:notify_assignee:crash,before_effect:*:sleep=0")
    faults.hit("before_effect", "authorize")  # wildcard sleep=0, fires once
    with pytest.raises(SimulatedCrash):
        faults.hit("after_effect", "notify_assignee")
    faults.hit("after_effect", "notify_assignee")  # once only
    assert not isinstance(SimulatedCrash("x"), Exception)  # escapes `except Exception` like a kill


@pytest.mark.release_blocker
@pytest.mark.req("DIR-032", "SPR-007", "MST:ARCH-002")
def test_keys_are_mandatory_outside_local_and_validated() -> None:
    common = {
        "database_url": "postgresql+psycopg://x@y/z",
        "auth_mode": "entra",
        "entra_issuer": "https://login.example/t/v2.0",
        "entra_jwks_url": "https://login.example/keys",
    }
    with pytest.raises(ValidationError, match="data_encryption_key"):
        Settings(environment="staging", **common)  # type: ignore[arg-type]
    with pytest.raises(ValidationError, match="servicebus_namespace"):
        Settings(
            environment="test",
            auth_mode="local",
            database_url="postgresql://x/y",  # type: ignore[arg-type]
            dev_jwt_secret="x" * 40,
            queue_backend="servicebus",
        )
    keys = {
        "data_encryption_key": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        "event_signing_key": "BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB",
    }
    with pytest.raises(ValidationError, match="https"):  # plain-HTTP bank endpoint (SPR-014)
        Settings(environment="staging", **common, **keys)  # type: ignore[arg-type]
    bank = {"bank_base_url": "https://bank.example"}
    with pytest.raises(ValidationError, match="connector_credentials"):
        Settings(environment="staging", **common, **keys, **bank)  # type: ignore[arg-type]
    ports = {
        "malware_scanner": "clamd",
        "clamd_host": "clamav",
        "clamd_port": 3310,
        "blob_backend": "azure",
        "azure_blob_account_url": "https://acct.blob.core.windows.net",
        "retrieval_backend": "azure",
        "azure_search_endpoint": "https://s.search.windows.net",
        "azure_search_api_key": "from-key-vault",
        "model_provider": "azure_openai",
        "azure_openai_endpoint": "https://aoai.example",
        "azure_openai_deployment": "approved-deployment",
        "azure_openai_api_key": "from-key-vault",
        "require_promoted_configuration": True,
    }
    s = Settings(
        environment="staging",
        connector_credentials='{"tenant": "from-key-vault"}',  # type: ignore[arg-type]
        **common,  # type: ignore[arg-type]
        **keys,  # type: ignore[arg-type]
        **bank,  # type: ignore[arg-type]
        **ports,  # type: ignore[arg-type]
    )
    assert s.connector_credentials_map() == {"tenant": "from-key-vault"}
    assert len(s.data_key()) == 32 and len(s.signing_key()) >= 32
    bad = s.model_copy(update={"data_encryption_key": s.event_signing_key.__class__("c2hvcnQ")})
    with pytest.raises(ValueError):
        bad.data_key()


class _FakeMsg:
    def __init__(self, body: str, delivery_count: int = 0) -> None:
        self._body, self.delivery_count = body, delivery_count

    def __str__(self) -> str:
        return self._body


class _FakeSender:
    def __init__(self, sink: list[Any]) -> None:
        self.sink = sink

    def __enter__(self) -> _FakeSender:
        return self

    def __exit__(self, *a: object) -> None:
        return None

    def send_messages(self, m: Any) -> None:
        self.sink.append(m)


class _FakeReceiver:
    def __init__(self, msgs: list[_FakeMsg]) -> None:
        self.msgs, self.completed, self.abandoned, self.dead = msgs, [], [], []  # type: ignore[var-annotated]

    def receive_messages(self, max_message_count: int, max_wait_time: int) -> list[_FakeMsg]:
        out, self.msgs = self.msgs[:max_message_count], self.msgs[max_message_count:]
        return out

    def complete_message(self, m: Any) -> None:
        self.completed.append(m)

    def abandon_message(self, m: Any) -> None:
        self.abandoned.append(m)

    def dead_letter_message(self, m: Any, reason: str) -> None:
        self.dead.append((m, reason))


class _FakeClient:
    def __init__(self, msgs: list[_FakeMsg]) -> None:
        self.sent: list[Any] = []
        self.receiver = _FakeReceiver(msgs)

    def get_queue_sender(self, queue_name: str) -> _FakeSender:
        return _FakeSender(self.sent)

    def get_queue_receiver(self, queue_name: str) -> _FakeReceiver:
        return self.receiver


@pytest.mark.req("MST:AZ-002", "DIR-022")
def test_service_bus_adapter_maps_port_semantics_against_fake_client() -> None:
    """Contract against a fake client only - LIVE INTEGRATION NOT VERIFIED."""
    env = envelope().signed(KEY)
    client = _FakeClient([_FakeMsg(env.model_dump_json(), delivery_count=2), _FakeMsg("not json")])
    q = ServiceBusQueue("ns.servicebus.windows.net", "finagent-events", client=client)
    q.publish(env)
    sent = client.sent[0]
    assert sent.message_id == str(env.event_id) and sent.application_properties["tenant_ref"] == str(
        env.tenant_ref
    )
    good, poison = q.receive(10, 30)
    assert good.envelope == env and good.delivery_count == 3
    assert poison.envelope is None and poison.raw == {}
    q.ack(good)
    q.release(good, 5)
    q.dead_letter(poison, "MALFORMED_ENVELOPE")
    r = client.receiver
    assert len(r.completed) == len(r.abandoned) == 1 and r.dead[0][1] == "MALFORMED_ENVELOPE"
