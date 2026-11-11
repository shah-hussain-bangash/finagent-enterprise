"""PostgreSQL coverage for the advisory readiness route; synthetic test database only."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from finagent.database.models import ConfigurationVersion, EvaluationRun
from finagent.database.seeds import sid
from finagent.database.session import session_factory, transaction
from finagent.domain.fingerprint import fingerprint
from finagent.evaluation.dataset_control import read_dataset, record_for
from finagent.services.governance_service import config as config_service
from finagent.services.governance_service import promotion_readiness
from tests.helpers import principal, subject

pytestmark = [pytest.mark.integration, pytest.mark.req("FR-002", "FR-040", "SPR-009", "SPR-032")]


@pytest.fixture
def readiness_pair(db_url, tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    folder = tmp_path / "datasets"
    folder.mkdir()
    entries = {}
    for index, split in enumerate(("development", "validation", "locked_release"), 1):
        name, scenario = f"readiness-integration-{index}", f"scenario-{index}"
        data = {
            "dataset": name,
            "version": "1.0.0",
            "window": {},
            "capability_scenarios": [{"id": scenario, "family": f"family-{index}", "input": str(index)}],
            "budget_scenarios": [],
            "retrieval_scenarios": [],
        }
        lineage = {scenario: {"family": f"family-{index}", "customer": str(index), "story": str(index)}}
        entries[name] = record_for(name, data, split, lineage, "Synthetic integration fixture only")
        (folder / f"{name}.json").write_text(json.dumps(data), encoding="utf-8")
    (tmp_path / "dataset-catalog.json").write_text(
        json.dumps({"schema_version": 1, "datasets": entries, "history": list(entries.values())}),
        encoding="utf-8",
    )
    monkeypatch.setattr(config_service, "_EVALUATION_DATASETS", folder)
    data, control = read_dataset(folder, "readiness-integration-3", purpose="release")
    config_id, evaluation_id = uuid4(), uuid4()
    with transaction(session_factory(db_url)) as session:
        session.add(
            ConfigurationVersion(
                id=config_id,
                tenant_id=sid("tenant:a"),
                config_hash="a" * 64,
                status="CANDIDATE",
                content={"private": "PRIVATE_READINESS_SENTINEL"},
                change_reason="synthetic fixture",
            )
        )
        session.add(
            EvaluationRun(
                id=evaluation_id,
                tenant_id=sid("tenant:a"),
                config_hash="a" * 64,
                dataset=data["dataset"],
                dataset_version=data["version"],
                dataset_hash=fingerprint({**data, "_dataset_control": control}),
                thresholds_version="integration-fixture-PROPOSED",
                gate="PASS_PROPOSED",
                metrics={},
                results=[
                    {"evaluation_context": {"evaluation_purpose": "release", "dataset_control": control}}
                ],
                created_by=principal("a", "tenantadmin1"),
            )
        )
    return config_id, evaluation_id


def request(client: Any, token: Any, pair: tuple[UUID, UUID], **kwargs: Any) -> Any:
    return client.get(
        f"/api/v1/admin/config-versions/{pair[0]}/promotion-readiness",
        params={"evaluation_id": str(pair[1])},
        headers=token(subject("a", "tenantadmin1"), **kwargs),
    )


def snapshot(db_url: str, pair: tuple[UUID, UUID]) -> tuple[Any, ...]:
    with transaction(session_factory(db_url)) as session:
        config = session.get(ConfigurationVersion, pair[0])
        evaluation = session.get(EvaluationRun, pair[1])
        assert config is not None and evaluation is not None
        return (
            config.status,
            config.evaluation_id,
            config.change_reason,
            config.updated_at,
            config.content,
            evaluation.gate,
            evaluation.results,
            evaluation.metrics,
        )


def test_advisory_response_preserves_database_state(client, token, readiness_pair, db_url):  # type: ignore[no-untyped-def]
    before = snapshot(db_url, readiness_pair)
    response = request(client, token, readiness_pair)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["readiness"] == "CHECKS_SATISFIED_PROPOSED_ONLY"
    assert body["execution_authorized"] is False and body["promotion_executed"] is False
    assert "PRIVATE_READINESS_SENTINEL" not in response.text
    assert "no-store" in response.headers["cache-control"]
    assert response.headers["pragma"] == "no-cache"
    assert snapshot(db_url, readiness_pair) == before


@pytest.mark.release_blocker
def test_postgresql_enforces_route_transaction_mode(
    client: Any,
    token: Any,
    readiness_pair: tuple[UUID, UUID],
    monkeypatch: Any,
) -> None:
    original = promotion_readiness.get_readiness
    observed = []
    write_outcomes = []

    def inspect(session, ctx, config_id, evaluation_id):  # type: ignore[no-untyped-def]
        observed.append(
            (
                session.scalar(text("SHOW transaction_read_only")),
                session.scalar(text("SHOW transaction_isolation")),
            )
        )
        # A savepoint confines the deliberately rejected write to this test probe.
        savepoint = session.begin_nested()
        try:
            session.execute(
                text("UPDATE configuration_versions SET change_reason = 'must not persist' WHERE id = :id"),
                {"id": config_id},
            )
        except DBAPIError as exc:
            write_outcomes.append(getattr(exc.orig, "sqlstate", None))
        else:
            write_outcomes.append("WRITE_ACCEPTED")
        finally:
            savepoint.rollback()
        return original(session, ctx, config_id, evaluation_id)

    monkeypatch.setattr(promotion_readiness, "get_readiness", inspect)
    response = request(client, token, readiness_pair)
    assert response.status_code == 200, response.text
    assert observed == [("on", "repeatable read")]
    assert write_outcomes == ["25006"]


@pytest.mark.release_blocker
@pytest.mark.parametrize("foreign_part", ["configuration", "evaluation"])
def test_cross_tenant_rows_match_missing_response(
    client: Any,
    token: Any,
    readiness_pair: tuple[UUID, UUID],
    db_url: str,
    foreign_part: str,
) -> None:
    foreign_id = uuid4()
    with transaction(session_factory(db_url)) as session:
        if foreign_part == "configuration":
            session.add(
                ConfigurationVersion(
                    id=foreign_id,
                    tenant_id=sid("tenant:b"),
                    config_hash="b" * 64,
                    content={},
                    status="CANDIDATE",
                )
            )
        else:
            session.add(
                EvaluationRun(
                    id=foreign_id,
                    tenant_id=sid("tenant:b"),
                    config_hash="b" * 64,
                    dataset="foreign-fixture",
                    dataset_version="1",
                    dataset_hash="b" * 64,
                    thresholds_version="PROPOSED",
                    gate="INCOMPLETE",
                    metrics={},
                    results=[],
                    created_by=principal("b", "tenantadmin1"),
                )
            )
    index = 0 if foreign_part == "configuration" else 1
    foreign_pair, missing_pair = list(readiness_pair), list(readiness_pair)
    foreign_pair[index], missing_pair[index] = foreign_id, uuid4()
    foreign = request(client, token, (foreign_pair[0], foreign_pair[1]))
    missing = request(client, token, (missing_pair[0], missing_pair[1]))
    assert foreign.status_code == missing.status_code == 404
    assert foreign.json()["error"]["code"] == missing.json()["error"]["code"]
    assert foreign.json()["error"]["message"] == missing.json()["error"]["message"]


@pytest.mark.release_blocker
def test_admin_requires_mfa(client, token, readiness_pair):  # type: ignore[no-untyped-def]
    response = request(client, token, readiness_pair, mfa=False)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "MFA_REQUIRED"


def test_operator_has_advisory_access_without_promotion(client, token, readiness_pair):  # type: ignore[no-untyped-def]
    response = client.get(
        f"/api/v1/admin/config-versions/{readiness_pair[0]}/promotion-readiness",
        params={"evaluation_id": str(readiness_pair[1])},
        headers=token(subject("a", "operator1")),
    )
    assert response.status_code == 200, response.text
    assert response.json()["technical_checks_satisfied"] is True
    assert "PROMOTION_FORBIDDEN" in response.json()["blocking_reasons"]
    assert response.json()["execution_authorized"] is False


def test_changed_catalog_is_blocked(client, token, readiness_pair, tmp_path):  # type: ignore[no-untyped-def]
    path = tmp_path / "dataset-catalog.json"
    catalog = json.loads(path.read_text(encoding="utf-8"))
    catalog["datasets"]["readiness-integration-3"]["contaminated"] = True
    catalog["history"][-1]["contaminated"] = True
    path.write_text(json.dumps(catalog), encoding="utf-8")
    response = request(client, token, readiness_pair)
    assert response.status_code == 200, response.text
    assert "RELEASE_EVIDENCE_REJECTED" in response.json()["blocking_reasons"]


def test_anonymous_access_denied(client, readiness_pair):  # type: ignore[no-untyped-def]
    response = client.get(
        f"/api/v1/admin/config-versions/{readiness_pair[0]}/promotion-readiness",
        params={"evaluation_id": str(readiness_pair[1])},
    )
    assert response.status_code == 401
