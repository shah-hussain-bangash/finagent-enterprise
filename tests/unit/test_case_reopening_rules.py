"""Reopening is an explicit exception, never a generic terminal-case edit."""

import pytest

from finagent.domain.states import (
    CaseStatus,
    InvalidTransitionError,
    allowed_case_targets,
    case_transition,
    reopening_transition,
)


def test_completed_case_has_a_dedicated_reopening_rule() -> None:
    assert reopening_transition(CaseStatus.COMPLETED) is CaseStatus.REWORK
    assert allowed_case_targets(CaseStatus.COMPLETED) == frozenset()
    with pytest.raises(InvalidTransitionError):
        case_transition(CaseStatus.COMPLETED, CaseStatus.REWORK)


@pytest.mark.parametrize("status", [s for s in CaseStatus if s is not CaseStatus.COMPLETED])
def test_only_completed_cases_can_use_reopening_rule(status: CaseStatus) -> None:
    with pytest.raises(InvalidTransitionError):
        reopening_transition(status)
