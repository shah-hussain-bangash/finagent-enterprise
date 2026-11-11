"""Pure policy units: MFA step-up, delegation scoping, emergency role, quotas, config guards."""

from __future__ import annotations

import os
import uuid

import pytest
from pydantic import ValidationError

from finagent.core.config import Settings
from finagent.core.errors import AppError, ErrorCode
from finagent.security.authorization.policy import (
    ROLE_PERMISSIONS,
    CaseScope,
    DelegatedRole,
    Permission,
    PrincipalContext,
    PrincipalKind,
    Role,
    case_scope,
    require,
)
from finagent.security.rate_limit import Bucket, InMemoryRateLimiter, RedisRateLimiter

SECRET = "x" * 40


def ctx(*roles: Role, mfa: bool = False, **kw: object) -> PrincipalContext:
    return PrincipalContext(  # type: ignore[arg-type]
        uuid.uuid4(), uuid.uuid4(), PrincipalKind.USER, frozenset(roles), mfa_satisfied=mfa, **kw
    )


@pytest.mark.release_blocker
@pytest.mark.req("SPR-005", "FR-001")
def test_step_up_permissions_require_mfa_others_do_not() -> None:
    with pytest.raises(AppError) as err:
        require(ctx(Role.REVIEWER), Permission.APPROVAL_DECIDE)
    assert err.value.code is ErrorCode.MFA_REQUIRED and err.value.status_code == 401
    require(ctx(Role.REVIEWER, mfa=True), Permission.APPROVAL_DECIDE)
    require(ctx(Role.REVIEWER), Permission.CASE_READ)  # ordinary reads need no step-up
    with pytest.raises(AppError) as err:  # permission is checked before MFA: no oracle
        require(ctx(Role.ANALYST, mfa=True), Permission.APPROVAL_DECIDE)
    assert err.value.code is ErrorCode.FORBIDDEN


@pytest.mark.release_blocker
@pytest.mark.req("SPR-008", "FR-028")
def test_emergency_and_break_glass_roles_never_carry_write_or_approval_power() -> None:
    forbidden = {
        Permission.APPROVAL_DECIDE,
        Permission.REPORT_SUBMIT,
        Permission.CASE_UPDATE,
        Permission.CASE_ASSIGN,
        Permission.EXPORT_CREATE,
        Permission.TENANT_ADMIN,
        Permission.AUDIT_READ,
    }
    assert not forbidden & ROLE_PERMISSIONS[Role.EMERGENCY_READ]
    assert ROLE_PERMISSIONS[Role.BREAK_GLASS] == frozenset({Permission.BREAK_GLASS_ACTIVATE})
    assert case_scope(ctx(Role.BREAK_GLASS)) is CaseScope.NONE
    assert case_scope(ctx(Role.EMERGENCY_READ)) is CaseScope.TENANT


@pytest.mark.req("FR-003")
def test_delegation_scope_includes_delegator_and_marks_delegated_use() -> None:
    delegator = uuid.uuid4()
    d = DelegatedRole(Role.REVIEWER, delegator, uuid.uuid4())
    only_delegated = ctx(
        Role.ANALYST, Role.REVIEWER, delegations=(d,), assigned_roles=frozenset({Role.ANALYST})
    )
    assert only_delegated.acting_for(Role.REVIEWER) == {delegator}
    assert only_delegated.acting_for(Role.ANALYST) == {only_delegated.principal_id}
    assert only_delegated.delegation_for(Role.REVIEWER) == d
    assert only_delegated.delegation_for(Role.ANALYST) is None
    both = ctx(Role.REVIEWER, delegations=(d,), assigned_roles=frozenset({Role.REVIEWER}))
    assert both.acting_for(Role.REVIEWER) == {delegator, both.principal_id}
    assert both.delegation_for(Role.REVIEWER) is None  # direct holding wins: no delegation recorded


@pytest.mark.req("NQ-028", "SPR-017")
def test_in_memory_token_bucket_refills_and_reports_retry_after() -> None:
    now = [0.0]
    limiter = InMemoryRateLimiter(clock=lambda: now[0])
    bucket = Bucket(per_minute=60, burst=2)  # 1 token/s
    assert [limiter.hit("k", bucket).allowed for _ in range(3)] == [True, True, False]
    denied = limiter.hit("k", bucket)
    assert not denied.allowed and denied.retry_after_s == 1
    now[0] += 1.0
    assert limiter.hit("k", bucket).allowed
    assert limiter.hit("other", bucket).allowed  # keys are independent
    assert limiter.hit("big", bucket, cost=5).retry_after_s >= 3


class _BrokenRedis:
    def register_script(self, _: str) -> object:
        def run(**_: object) -> object:
            raise ConnectionError("redis down")

        return run


@pytest.mark.req("NQ-028", "NFR-008")
def test_redis_outage_degrades_to_local_quota_not_open_or_closed() -> None:
    limiter = RedisRateLimiter(_BrokenRedis())
    bucket = Bucket(per_minute=1, burst=1)
    assert limiter.hit("t:1:principal:1", bucket).allowed
    assert not limiter.hit("t:1:principal:1", bucket).allowed


@pytest.mark.integration
@pytest.mark.req("NQ-028")
def test_redis_rate_limiter_against_real_redis() -> None:
    url = os.environ.get("FINAGENT_TEST_REDIS_URL")
    if not url:
        pytest.skip("FINAGENT_TEST_REDIS_URL not set - NOT RUN")
    import redis

    client = redis.Redis.from_url(url)
    key = f"t:{uuid.uuid4()}:principal:test"
    limiter = RedisRateLimiter(client)
    bucket = Bucket(per_minute=60, burst=3)
    results = [limiter.hit(key, bucket) for _ in range(4)]
    assert [r.allowed for r in results] == [True, True, True, False]
    assert results[-1].retry_after_s >= 1
    assert client.pttl(f"rl:{key}") > 0  # buckets expire: Redis stays a disposable cache


@pytest.mark.req("SPR-005", "SPR-008", "FR-003", "MST:ARCH-002")
def test_configuration_fails_closed_on_unsafe_security_settings() -> None:
    base = {"database_url": "postgresql+psycopg://x@y/z", "auth_mode": "entra"}
    entra = {"entra_issuer": "https://login.example/tid/v2.0", "entra_jwks_url": "https://login.example/keys"}
    with pytest.raises(ValidationError, match="privileged baseline"):
        Settings(environment="production", **base, **entra, mfa_step_up_permissions=["case:assign"])  # type: ignore[arg-type]
    with pytest.raises(ValidationError, match="not delegable"):
        Settings(environment="production", **base, **entra, delegable_roles=["TENANT_ADMIN"])  # type: ignore[arg-type]
    with pytest.raises(ValidationError, match="redis_url"):
        Settings(environment="production", **base, **entra, rate_limit_backend="redis")  # type: ignore[arg-type]
    keys = {
        "data_encryption_key": "A" * 43,
        "event_signing_key": "B" * 43,
        "bank_base_url": "https://bank.example",
        "connector_credentials": '{"t": "k"}',
    }
    with pytest.raises(ValidationError, match="not antivirus"):  # local test scanner refused (FR-013)
        Settings(environment="production", **base, **entra, **keys)  # type: ignore[arg-type]
    clamd = {"malware_scanner": "clamd", "clamd_host": "clamav", "clamd_port": 3310}
    with pytest.raises(ValidationError, match="local blob storage"):
        Settings(environment="production", **base, **entra, **keys, **clamd)  # type: ignore[arg-type]
    ports = {**clamd, "blob_backend": "azure", "azure_blob_account_url": "https://acct.blob.core.windows.net"}
    with pytest.raises(ValidationError, match="local BM25"):
        Settings(environment="production", **base, **entra, **keys, **ports)  # type: ignore[arg-type]
    search = {"retrieval_backend": "azure", "azure_search_endpoint": "https://s.search.windows.net"}
    model = {
        "model_provider": "azure_openai",
        "azure_openai_endpoint": "https://aoai.example",
        "azure_openai_deployment": "approved-deployment",
        "azure_openai_api_key": "from-key-vault",
    }
    with pytest.raises(ValidationError, match="api key"):
        Settings(  # type: ignore[arg-type]
            environment="production",
            **base,
            **entra,
            **keys,
            **ports,
            **search,
            **model,
            require_promoted_configuration=True,
        )
    ports |= {**search, "azure_search_api_key": "from-key-vault"}
    with pytest.raises(ValidationError, match="mock model provider"):  # SYNTHETIC model refused (DEC-07)
        Settings(environment="production", **base, **entra, **keys, **ports)  # type: ignore[arg-type]
    ports |= model
    with pytest.raises(ValidationError, match="require_promoted_configuration"):  # AER-031..034
        Settings(environment="production", **base, **entra, **keys, **ports)  # type: ignore[arg-type]
    ports |= {"require_promoted_configuration": True}
    s = Settings(environment="production", **base, **entra, **keys, **ports)  # type: ignore[arg-type]
    assert s.break_glass_enabled is False  # SPR-008 default: disabled
    assert s.case_status_write_enabled is False
