"""Operator-only local synthetic intake demonstration; never an agent capability.

The schema owner issues/revokes a short-lived connector credential, while alert
creation goes through the real HTTP API as that connector. No role, assignment,
human session or approval is forged. No credential is printed or written to disk.
"""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from finagent.core.config import Settings, get_settings
from finagent.database.models import Alert, Case, CaseAssignment, IntegrationConnection, Principal, Tenant
from finagent.database.session import session_factory, transaction
from finagent.security.authentication.connector_credentials import (
    issue_local_credential,
    require_local,
    revoke_local_credential,
)


def validate_demo_target(settings: Settings, api_url: str) -> None:
    require_local(settings)
    url = urlsplit(api_url)
    targets = {("127.0.0.1", 8001), ("localhost", 8001), ("finagent-api-1", 8000)}
    if (
        settings.model_provider != "mock"
        or settings.case_status_write_enabled
        or url.scheme != "http"
        or (url.hostname, url.port) not in targets
        or url.username
        or url.password
        or url.path not in ("", "/")
        or url.query
        or url.fragment
    ):
        raise RuntimeError("LOCAL_SYNTHETIC_DEMO_TARGET_REQUIRED")


@contextmanager
def managed_credential(
    factory: sessionmaker[Session], settings: Settings, principal_id: UUID
) -> Iterator[str]:
    credential_id = None
    try:
        with transaction(factory) as session:
            token, credential = issue_local_credential(
                session,
                settings,
                principal_id,
                ttl_seconds=900,
                reason="Operator-requested local synthetic intake demonstration",
            )
            credential_id = credential.id
        yield token
    finally:
        # Also attempt cleanup when commit confirmation or HTTP client creation fails.
        if credential_id is not None:
            with transaction(factory) as session:
                revoke_local_credential(
                    session,
                    settings,
                    credential_id,
                    reason="Revoke completed local synthetic intake credential",
                )


def run_demo(
    settings: Settings,
    owner_url: str,
    run_id: UUID,
    *,
    api_url: str = "http://127.0.0.1:8001",
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    validate_demo_target(settings, api_url)
    owner_factory = session_factory(owner_url)
    source_ref = "AML-DEMO-" + run_id.hex
    key = "synthetic-demo-" + run_id.hex
    # These historical timestamps match the mock-bank fixture. Receipt and case
    # creation times come from the live application; repeated runs reuse one key.
    envelope = {
        "schema_version": "1.0",
        "event_id": "evt-demo-" + run_id.hex,
        "source_system": "tm-synthetic-a",
        "source_event_time": "2026-10-15T07:00:00Z",
        "payload": {
            "source_alert_id": source_ref,
            "alert_type": "TM_STRUCTURING",
            "occurred_at": "2026-10-15T06:59:00Z",
            "subject_refs": ["customer-demo-001"],
            "severity": "HIGH",
            "rule_ref": "synthetic-local-connector-demo-v1",
            "window_start": "2026-09-15T00:00:00Z",
            "window_end": "2026-10-15T00:00:00Z",
        },
    }
    with transaction(owner_factory) as session:
        registration = session.execute(
            select(Principal, Tenant)
            .join(Tenant, Tenant.id == Principal.tenant_id)
            .join(
                IntegrationConnection,
                (IntegrationConnection.principal_id == Principal.id)
                & (IntegrationConnection.tenant_id == Principal.tenant_id),
            )
            .where(
                Tenant.slug == "synthetic-bank-a",
                Tenant.is_synthetic.is_(True),
                Principal.kind == "INTEGRATION",
                IntegrationConnection.source_system == "tm-synthetic-a",
                IntegrationConnection.status == "ACTIVE",
            )
        ).one_or_none()
        if registration is None:
            raise RuntimeError("REGISTERED_SYNTHETIC_CONNECTOR_REQUIRED")
        principal, tenant = registration
        tenant_id = tenant.id
        existing = session.scalar(
            select(Case.id)
            .join(Alert, (Alert.id == Case.alert_id) & (Alert.tenant_id == Case.tenant_id))
            .where(
                Case.tenant_id == tenant_id,
                Alert.source_system == "tm-synthetic-a",
                Alert.source_alert_id == source_ref,
            )
        )
        principal_id = principal.id
    with managed_credential(owner_factory, settings, principal_id) as token:
        context = (
            nullcontext(client)
            if client is not None
            else httpx.Client(base_url=api_url, timeout=20, follow_redirects=False, trust_env=False)
        )
        with context as http:
            headers = {"Authorization": "Bearer " + token, "Idempotency-Key": key}
            if http.get("/api/v1/cases", headers=headers).status_code != 401:
                raise RuntimeError("CONNECTOR_CASE_READ_RESTRICTION_FAILED")
            first = http.post("/api/v1/alerts", headers=headers, json=envelope)
            if first.status_code not in (200, 201):
                raise RuntimeError("ALERT_INTAKE_HTTP_" + str(first.status_code))
            case_id = UUID(first.json()["case_id"])
            replay = http.post("/api/v1/alerts", headers=headers, json=envelope)
            if replay.status_code not in (200, 201) or replay.json().get("case_id") != str(case_id):
                raise RuntimeError("IDEMPOTENT_REPLAY_VERIFICATION_FAILED")
            with transaction(session_factory(settings.database_url.get_secret_value())) as session:
                case = session.scalar(
                    select(Case)
                    .join(Alert, (Alert.id == Case.alert_id) & (Alert.tenant_id == Case.tenant_id))
                    .where(
                        Case.id == case_id,
                        Case.tenant_id == tenant_id,
                        Alert.source_system == "tm-synthetic-a",
                        Alert.source_alert_id == source_ref,
                    )
                )
                if case is None or (existing is not None and existing != case.id):
                    raise RuntimeError("CREATED_CASE_BINDING_DIFFERS")
                count = session.scalar(
                    select(func.count())
                    .select_from(Case)
                    .join(Alert, (Alert.id == Case.alert_id) & (Alert.tenant_id == Case.tenant_id))
                    .where(
                        Case.tenant_id == tenant_id,
                        Alert.source_system == "tm-synthetic-a",
                        Alert.source_alert_id == source_ref,
                    )
                )
                if count != 1:
                    raise RuntimeError("ONE_CASE_PER_SOURCE_ALERT_REQUIRED")
                assignee = session.execute(
                    select(Principal.id, Principal.display_name)
                    .join(
                        CaseAssignment,
                        (CaseAssignment.principal_id == Principal.id)
                        & (CaseAssignment.tenant_id == Principal.tenant_id),
                    )
                    .where(
                        CaseAssignment.tenant_id == tenant_id,
                        CaseAssignment.case_id == case.id,
                        CaseAssignment.assignment_role == "ANALYST",
                        CaseAssignment.active.is_(True),
                    )
                ).one_or_none()
                information = {
                    "run_id": str(run_id),
                    "case_id": str(case.id),
                    "case_status": case.status,
                    "case_url": "http://127.0.0.1:3001/cases/" + str(case.id),
                    "source_alert_id": source_ref,
                    "new_case_created": existing is None,
                    "idempotent_replay_same_case": True,
                    "matching_case_count": count,
                    "assigned_analyst_id": str(assignee.id) if assignee else None,
                    "assigned_analyst_name": assignee.display_name if assignee else None,
                    "connector_case_read_denied": True,
                    "human_roles_changed": False,
                    "workflow_started": False,
                    "fixture_window": "2026-09-15 to 2026-10-15",
                    "downstream_status_write_enabled": False,
                }
    # Reopen only for a revocation probe; a failed check must never be reported as
    # successful. Replaying this run_id later still addresses the same source alert.
    context = (
        nullcontext(client)
        if client is not None
        else httpx.Client(base_url=api_url, timeout=20, follow_redirects=False, trust_env=False)
    )
    with context as http:
        revoked = http.post(
            "/api/v1/alerts",
            headers={"Authorization": "Bearer " + token, "Idempotency-Key": key},
            json=envelope,
        )
        if revoked.status_code != 401:
            raise RuntimeError("REVOKED_CONNECTOR_CREDENTIAL_WAS_NOT_REJECTED")
    information["credential_revoked"] = True
    information["revoked_credential_rejected"] = True
    return information


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Local synthetic intake, with no role or assignment changes."
    )
    parser.add_argument(
        "--run-id", type=UUID, required=True, help="Stable run ID; reuse after an interrupted attempt."
    )
    args = parser.parse_args()
    owner_url = os.environ.get("FINAGENT_MIGRATION_DATABASE_URL")
    if not owner_url:
        raise SystemExit("SCHEMA_OWNER_CONFIGURATION_REQUIRED")
    try:
        result = run_demo(
            get_settings(),
            owner_url,
            args.run_id,
            api_url=os.environ.get("FINAGENT_CONNECTOR_DEMO_API_URL", "http://127.0.0.1:8001"),
        )
    except Exception as error:
        # Do not echo exception messages, DSNs, raw credentials or provider data.
        print(
            json.dumps({"status": "FAILED", "error_type": type(error).__name__, "run_id": str(args.run_id)})
        )
        raise SystemExit(1) from None
    print(json.dumps({"status": "SYNTHETIC_INTAKE_VERIFIED", **result}))


if __name__ == "__main__":
    main()
