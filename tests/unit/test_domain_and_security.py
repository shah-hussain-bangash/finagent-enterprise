from __future__ import annotations

import itertools
import json
import logging
import uuid
from decimal import Decimal

import pytest
from pydantic import ValidationError

from finagent.core.config import Settings
from finagent.core.errors import AppError, ErrorCode
from finagent.core.logging import JsonFormatter
from finagent.domain.fingerprint import fingerprint
from finagent.domain.money import Direction, Money, Txn, TxStatus, posted_credit_totals
from finagent.domain.states import (
    CaseStatus,
    InvalidTransitionError,
    RunStatus,
    allowed_case_targets,
    case_transition,
    run_transition,
)
from finagent.security.authentication.tokens import LocalDevTokenValidator
from finagent.security.authorization.policy import (
    ROLE_PERMISSIONS,
    CaseScope,
    Permission,
    PrincipalContext,
    PrincipalKind,
    Role,
    case_scope,
    check_separation_of_duties,
    require,
)
from finagent.security.redaction import REDACTED, redact

SECRET = "x" * 40


def ctx(*roles: Role) -> PrincipalContext:
    return PrincipalContext(uuid.uuid4(), uuid.uuid4(), PrincipalKind.USER, frozenset(roles))


# ---------------------------------------------------------------- state machines
LEGAL_CASE = {
    ("RECEIVED", "ASSIGNED"),
    ("ASSIGNED", "INVESTIGATING"),
    ("INVESTIGATING", "BLOCKED"),
    ("BLOCKED", "INVESTIGATING"),
    ("INVESTIGATING", "AWAITING_REVIEW"),
    ("REWORK", "AWAITING_REVIEW"),
    ("REWORK", "BLOCKED"),
    ("AWAITING_REVIEW", "REWORK"),
    ("AWAITING_REVIEW", "BLOCKED"),
    ("AWAITING_REVIEW", "APPROVED"),
    ("APPROVED", "FINALIZING"),
    ("APPROVED", "REWORK"),
    ("FINALIZING", "COMPLETED"),
    ("FINALIZING", "REWORK"),
}


@pytest.mark.req("FR-004", "SPR-033")
def test_case_transition_table_is_exhaustively_as_specified() -> None:
    for a, b in itertools.product(CaseStatus, CaseStatus):
        legal = (a.value, b.value) in LEGAL_CASE or (
            b is CaseStatus.CANCELLED and a not in {CaseStatus.COMPLETED, CaseStatus.CANCELLED}
        )
        if legal:
            assert case_transition(a, b) is b
        else:
            with pytest.raises(InvalidTransitionError):
                case_transition(a, b)


@pytest.mark.req("FR-006", "AER-016", "DIR-018")
def test_approval_invalidation_path_exists() -> None:
    assert CaseStatus.REWORK in allowed_case_targets(CaseStatus.APPROVED)
    assert CaseStatus.REWORK in allowed_case_targets(CaseStatus.FINALIZING)


@pytest.mark.req("FR-029", "DIR-020")
def test_case_cannot_complete_without_finalizing() -> None:
    for s in CaseStatus:
        if s is not CaseStatus.FINALIZING:
            with pytest.raises(InvalidTransitionError):
                case_transition(s, CaseStatus.COMPLETED)


@pytest.mark.req("FR-005", "FR-033", "FR-035")
def test_run_states_uncertain_cannot_be_cancelled_or_blindly_retried() -> None:
    with pytest.raises(InvalidTransitionError):
        run_transition(RunStatus.OUTCOME_UNCERTAIN, RunStatus.CANCELLED)
    with pytest.raises(InvalidTransitionError):
        run_transition(RunStatus.OUTCOME_UNCERTAIN, RunStatus.RUNNING)
    assert run_transition(RunStatus.RUNNING, RunStatus.CANCELLED) is RunStatus.CANCELLED
    for terminal in (RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED):
        for target in RunStatus:
            with pytest.raises(InvalidTransitionError):
                run_transition(terminal, target)


# ---------------------------------------------------------------- money (DIR-013 fixture)
@pytest.mark.req("DIR-008", "DIR-013", "FR-023", "AER-009", "SPR-031")
def test_dir013_fixture_per_currency_posted_credits() -> None:
    txs = [
        Txn("t1", Money("100.00", "AED"), Direction.CREDIT, TxStatus.POSTED),
        Txn("t2", Money("250.50", "AED"), Direction.CREDIT, TxStatus.POSTED),
        Txn("t3", Money("40.00", "AED"), Direction.CREDIT, TxStatus.PENDING),
        Txn("t4", Money("20.00", "USD"), Direction.CREDIT, TxStatus.POSTED),
    ]
    r = posted_credit_totals(txs)
    assert r.totals == {"AED": Money(Decimal("350.50"), "AED"), "USD": Money("20.00", "USD")}
    assert [(e.source_transaction_id, e.reason) for e in r.exclusions] == [("t3", "status_pending")]
    assert r.rule_version.endswith("SYNTHETIC")


@pytest.mark.req("DIR-008")
def test_money_refuses_float_and_currency_mixing() -> None:
    with pytest.raises(TypeError):
        Money(1.1, "AED")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="different currencies"):
        Money("1", "AED") + Money("1", "USD")
    with pytest.raises(ValueError):
        Money("1", "aed")


@pytest.mark.req("FR-032", "DIR-027")
def test_fingerprint_is_canonical() -> None:
    assert fingerprint({"b": 1, "a": [1, 2]}) == fingerprint({"a": [1, 2], "b": 1})
    assert fingerprint({"a": "1"}) != fingerprint({"a": 1})


# ---------------------------------------------------------------- authorization
@pytest.mark.req("FR-002", "SPR-009", "DIR-003")
def test_deny_by_default_and_admin_roles_have_no_case_content() -> None:
    for role in (
        Role.TENANT_ADMIN,
        Role.POLICY_ADMIN,
        Role.AUDITOR,
        Role.PLATFORM_OPERATOR,
        Role.INTEGRATION,
    ):
        assert Permission.CASE_READ not in ROLE_PERMISSIONS[role]
        assert case_scope(ctx(role)) is CaseScope.NONE
    with pytest.raises(AppError) as e:
        require(ctx(), Permission.CASE_READ)
    assert e.value.code is ErrorCode.FORBIDDEN


@pytest.mark.req("BR-007", "FR-024", "SPR-029", "MST:AG-003")
def test_no_role_holds_any_financial_action_permission() -> None:
    all_perms = {p.value for perms in ROLE_PERMISSIONS.values() for p in perms}
    forbidden_words = ("transfer", "payment", "freeze", "block", "credit", "sar", "filing", "risk_score")
    assert not [p for p in all_perms if any(w in p for w in forbidden_words)]


@pytest.mark.req("FR-003", "SPR-033", "MST:BR-004")
def test_separation_of_duties_blocks_self_approval() -> None:
    reviewer = ctx(Role.REVIEWER)
    with pytest.raises(AppError):
        check_separation_of_duties(reviewer.principal_id, reviewer)
    check_separation_of_duties(uuid.uuid4(), reviewer)


@pytest.mark.req("FR-002")
def test_case_scopes() -> None:
    assert case_scope(ctx(Role.AML_SUPERVISOR)) is CaseScope.TENANT
    assert case_scope(ctx(Role.ANALYST)) is CaseScope.ASSIGNED
    assert case_scope(ctx(Role.REVIEWER)) is CaseScope.REVIEW_ASSIGNED


# ---------------------------------------------------------------- authentication & config
@pytest.mark.req("FR-001", "SPR-004")
def test_local_tokens_reject_expired_forged_and_wrong_audience() -> None:
    v = LocalDevTokenValidator(SECRET, "finagent-api")
    assert v.validate(v.issue("a.analyst1@synthetic")).subject == "a.analyst1@synthetic"
    for bad in (
        LocalDevTokenValidator(SECRET, "finagent-api").issue("x", ttl_seconds=-120),
        LocalDevTokenValidator("y" * 40, "finagent-api").issue("x"),
        LocalDevTokenValidator(SECRET, "other-api").issue("x"),
        "not-a-token",
    ):
        with pytest.raises(AppError) as e:
            v.validate(bad)
        assert e.value.code is ErrorCode.UNAUTHENTICATED


@pytest.mark.req("SPR-005")
def test_mfa_claim_is_detected() -> None:
    v = LocalDevTokenValidator(SECRET, "finagent-api")
    assert v.validate(v.issue("s", mfa=True)).mfa is True
    assert v.validate(v.issue("s", mfa=False)).mfa is False


@pytest.mark.release_blocker
@pytest.mark.req("FR-001", "SPR-004", "MST:ARCH-002")
@pytest.mark.parametrize("env", ["dev", "staging", "production"])
def test_local_auth_cannot_start_outside_local_or_test(env: str) -> None:
    with pytest.raises(ValidationError, match="forbidden outside"):
        Settings(environment=env, auth_mode="local", database_url="postgresql://x", dev_jwt_secret=SECRET)


def test_config_fails_closed_on_weak_secret_and_missing_entra() -> None:
    with pytest.raises(ValidationError):
        Settings(
            environment="local", auth_mode="local", database_url="postgresql://x", dev_jwt_secret="short"
        )
    with pytest.raises(ValidationError):
        Settings(environment="production", auth_mode="entra", database_url="postgresql://x")


# ---------------------------------------------------------------- redaction
@pytest.mark.req("SPR-036", "AER-027", "NQ-035", "MST:DB-002", "NFR-001")
def test_redaction_of_logs() -> None:
    sample = {"token": "abc", "note": "mail jane@bank.example acct 123456789012 Bearer eyJabc.def.ghi1234567"}
    out = redact(sample)
    assert out["token"] == REDACTED
    assert "jane@" not in out["note"] and "123456789012" not in out["note"] and "eyJ" not in out["note"]
    record = logging.LogRecord("t", logging.INFO, "f", 1, "user jane@bank.example", None, None)
    record.password = "hunter2"  # type: ignore[attr-defined]
    line = json.loads(JsonFormatter().format(record))
    assert "jane@" not in line["msg"] and line["ctx"]["password"] == REDACTED
