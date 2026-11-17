"""Synthetic-only test of the disabled assistant candidate through the real model gateway."""

import json
import uuid
from dataclasses import replace
from types import MappingProxyType

import pytest

from finagent.agents import registry
from finagent.agents.assistant_candidate import run_assistant_candidate
from finagent.agents.providers import ProviderResponse
from finagent.agents.registry import CapabilityUnavailableError
from tests.agent_helpers import prepared_case

pytestmark = pytest.mark.integration


class SyntheticProvider:
    name = "synthetic-test-provider"

    def __init__(self, *, invent_reference=False):  # type: ignore[no-untyped-def]
        self.calls = 0
        self.invent_reference = invent_reference

    def complete(self, request):  # type: ignore[no-untyped-def]
        self.calls += 1
        payload = json.loads(request.user)
        first = payload["facts"]["retrieved_excerpts"][0]
        ref = "ev:aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa" if self.invent_reference else first["evidence_ref"]
        quote = first["excerpt"].strip()
        response = {"status": "SUPPORTED_EXCERPTS", "quotes": [{"evidence_ref": ref, "quote": quote}]}
        return ProviderResponse(text=json.dumps(response), input_tokens=10, output_tokens=10, model="synthetic")


@pytest.fixture
def prep(make_client, make_settings, token, bank):  # type: ignore[no-untyped-def]
    return prepared_case(make_client(bank_base_url=bank.url), token, make_settings(bank_base_url=bank.url))


def enable_candidate(monkeypatch):  # type: ignore[no-untyped-def]
    caps = dict(registry.CAPABILITIES)
    caps["AG-ASSISTANT"] = replace(caps["AG-ASSISTANT"], status="ENABLED")
    monkeypatch.setattr(registry, "CAPABILITIES", MappingProxyType(caps))


def test_candidate_is_disabled_by_default(prep) -> None:  # type: ignore[no-untyped-def]
    provider = SyntheticProvider()
    scope, _ = prep.scope(provider)
    with pytest.raises(CapabilityUnavailableError):
        run_assistant_candidate(prep.facts(), scope, "customer")
    assert provider.calls == 0


def test_candidate_accepts_only_snapshot_excerpts_and_abstains_on_missing(prep, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    enable_candidate(monkeypatch)
    provider = SyntheticProvider()
    scope, _ = prep.scope(provider)
    facts = prep.facts()
    facts.items = []  # Caller-provided evidence is ignored; the stored snapshot is reloaded.
    result = run_assistant_candidate(facts, scope, "customer")
    assert result["status"] == "SUPPORTED_EXCERPTS"
    assert result["snapshot_id"] == str(prep.snapshot_id)
    assert result["quotes"] and provider.calls == 1
    missing = run_assistant_candidate(prep.facts(), scope, "no-such-synthetic-evidence-7f619")
    assert missing["status"] == "INSUFFICIENT_EVIDENCE" and provider.calls == 1


def test_candidate_rejects_invented_reference_and_tenant_mismatch(prep, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    enable_candidate(monkeypatch)
    provider = SyntheticProvider(invent_reference=True)
    scope, _ = prep.scope(provider)
    result = run_assistant_candidate(prep.facts(), scope, "customer")
    assert result["status"] == "REJECTED" and result["quotes"] == []
    facts = prep.facts()
    facts.tenant_id = uuid.uuid4()
    with pytest.raises(PermissionError):
        run_assistant_candidate(facts, scope, "customer")
    assert provider.calls == 1
