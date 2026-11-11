"""Content-free connector observations shared by API and worker processes."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Literal

from sqlalchemy.orm import Session, sessionmaker

from finagent.database.models import ConnectorObservation
from finagent.database.session import transaction
from finagent.integrations.resilience import ErrorKind


@dataclass(frozen=True)
class ConnectorSample:
    operation: Literal["JSON", "BYTES"]
    outcome: Literal["SUCCEEDED", "FAILED"]
    error_code: str | None
    attempts: int
    latency_ms: int


def record_observation(
    factory: sessionmaker[Session], tenant_id: uuid.UUID, source: str, sample: ConnectorSample
) -> None:
    codes = {kind.value for kind in ErrorKind}
    error = sample.error_code if sample.error_code in codes else "UNEXPECTED"
    with transaction(factory) as session:
        session.add(
            ConnectorObservation(
                tenant_id=tenant_id,
                source=source if source == "core-banking" else "OTHER",
                operation=sample.operation,
                outcome=sample.outcome,
                error_code=error if sample.outcome == "FAILED" else None,
                attempts=max(0, min(sample.attempts, 2147483647)),
                latency_ms=max(0, min(sample.latency_ms, 2147483647)),
            )
        )
