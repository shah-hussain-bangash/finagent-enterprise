"""Workflow metadata used by services without loading graph builders."""

from __future__ import annotations

from dataclasses import dataclass

from finagent.core.errors import AppError, ErrorCode


@dataclass(frozen=True)
class WorkflowMetadata:
    workflow_type: str
    graph_version: str
    local_only: bool = False


_WORKFLOWS = {
    "aml_investigation": WorkflowMetadata(
        "aml_investigation",
        "aml-1",
    ),
    "durability_probe": WorkflowMetadata(
        "durability_probe",
        "skeleton-1",
        local_only=True,
    ),
}


def spec_for(workflow_type: str) -> WorkflowMetadata:
    spec = _WORKFLOWS.get(workflow_type)
    if spec is None:
        raise AppError(
            ErrorCode.SCHEMA_VALIDATION_FAILED,
            "Unknown workflow type.",
            {"field": "workflow_type"},
        )
    return spec


def graph_version_for(workflow_type: str) -> str:
    return spec_for(workflow_type).graph_version
