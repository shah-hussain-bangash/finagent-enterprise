"""Workflow type -> graph builder + graph version. A run records the graph version it started
with; resuming under a different version is refused (prompt §20 graph-version compatibility)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from finagent.core.errors import AppError, ErrorCode


@dataclass(frozen=True)
class WorkflowSpec:
    workflow_type: str
    graph_version: str
    build: Callable[[Any], Any]
    budget_exempt: frozenset[str] = frozenset()
    local_only: bool = False


def _specs() -> dict[str, WorkflowSpec]:
    from finagent.orchestration.graphs import aml, skeleton  # lazy: keeps API start-up light

    return {
        "aml_investigation": WorkflowSpec(
            "aml_investigation", aml.GRAPH_VERSION, aml.build, aml.BUDGET_EXEMPT
        ),
        # P4 durability probe (no agents): local/test only, used by recovery tests and slice 2.
        "durability_probe": WorkflowSpec(
            "durability_probe", skeleton.GRAPH_VERSION, skeleton.build, local_only=True
        ),
    }


def spec_for(workflow_type: str) -> WorkflowSpec:
    spec = _specs().get(workflow_type)
    if spec is None:
        raise AppError(
            ErrorCode.SCHEMA_VALIDATION_FAILED, "Unknown workflow type.", {"field": "workflow_type"}
        )
    return spec


def graph_version_for(workflow_type: str) -> str:
    return spec_for(workflow_type).graph_version
