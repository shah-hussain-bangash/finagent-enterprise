"""Canonical alert intake contract v1.0 (DIR-008/010/022, SRS FR-007).

`extra="forbid"` means a payload carrying tenant_id, role or any unknown field is rejected:
tenant comes only from the authenticated IntegrationConnection (DIR-002).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, model_validator

Ref = Annotated[str, StringConstraints(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9._:/-]+$")]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class AlertPayload(_Strict):
    source_alert_id: Ref
    alert_type: Annotated[str, StringConstraints(min_length=1, max_length=64, pattern=r"^[A-Z0-9_]+$")]
    occurred_at: AwareDatetime
    subject_refs: list[Ref] = Field(min_length=1, max_length=50)
    rule_ref: Ref | None = None
    severity: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"] | None = None
    window_start: AwareDatetime
    window_end: AwareDatetime

    @model_validator(mode="after")
    def _window(self) -> AlertPayload:
        if self.window_start >= self.window_end:
            raise ValueError("window_start must be before window_end (half-open window, DATA-D02)")
        if len(set(self.subject_refs)) != len(self.subject_refs):
            raise ValueError("subject_refs must be unique")
        return self


class AlertEnvelope(_Strict):
    schema_version: Literal["1.0"]
    event_id: Ref  # delivery id - distinct from the business key (DIR-022)
    source_system: Ref
    source_event_time: AwareDatetime
    payload: AlertPayload


class AlertAccepted(BaseModel):
    case_id: str
    alert_id: str
    status: str
    replayed: bool
    received_at: datetime
