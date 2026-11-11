"""Fenced leases (FR-034, DIR-031, NQ-021, AER-021; prompt §20).

* `claim` - one guarded `INSERT .. ON CONFLICT DO UPDATE .. WHERE expired-or-free RETURNING`. Every
  successful claim increments the fencing token, so tokens are strictly monotonic per resource.
* `heartbeat` - extends the lease only if (owner, token) still hold and it has not expired.
* `assert_current` - called INSIDE every protected commit. It locks the lease row, so a takeover
  cannot interleave with the commit, and raises StaleLeaseError if the token is no longer current.
* `release` - frees the lease early (the row and its token stay).

Database time (`clock_timestamp()`) is the only clock, so worker clock skew cannot matter.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from finagent.database.models import Lease

RUN_RESOURCE = "workflow_run"


class StaleLeaseError(Exception):
    """The caller no longer owns the lease; it must not commit anything."""


@dataclass(frozen=True)
class LeaseHandle:
    tenant_id: uuid.UUID
    resource_type: str
    resource_id: uuid.UUID
    owner: str
    token: int
    expires_at: datetime


def _expiry(ttl_s: int) -> object:
    return func.clock_timestamp() + func.make_interval(0, 0, 0, 0, 0, 0, ttl_s)


def claim(
    session: Session, tenant_id: uuid.UUID, resource_type: str, resource_id: uuid.UUID, owner: str, ttl_s: int
) -> LeaseHandle | None:
    insert = pg_insert(Lease).values(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        resource_type=resource_type,
        resource_id=resource_id,
        owner=owner,
        fencing_token=1,
        acquired_at=func.clock_timestamp(),
        heartbeat_at=func.clock_timestamp(),
        expires_at=_expiry(ttl_s),
    )
    stmt = insert.on_conflict_do_update(
        constraint="uq_leases_resource",
        set_={
            "owner": insert.excluded.owner,
            "fencing_token": Lease.fencing_token + 1,
            "acquired_at": func.clock_timestamp(),
            "heartbeat_at": func.clock_timestamp(),
            "expires_at": insert.excluded.expires_at,
        },
        where=(Lease.expires_at <= func.clock_timestamp()) | Lease.owner.is_(None),
    ).returning(Lease.fencing_token, Lease.expires_at)
    row = session.execute(stmt).first()
    if row is None:
        return None
    return LeaseHandle(tenant_id, resource_type, resource_id, owner, int(row[0]), row[1])


def heartbeat(session: Session, lease: LeaseHandle, ttl_s: int) -> LeaseHandle | None:
    row = session.execute(
        update(Lease)
        .where(
            Lease.tenant_id == lease.tenant_id,
            Lease.resource_type == lease.resource_type,
            Lease.resource_id == lease.resource_id,
            Lease.owner == lease.owner,
            Lease.fencing_token == lease.token,
            Lease.expires_at > func.clock_timestamp(),
        )
        .values(heartbeat_at=func.clock_timestamp(), expires_at=_expiry(ttl_s))
        .returning(Lease.expires_at)
    ).first()
    if row is None:
        return None
    return LeaseHandle(
        lease.tenant_id, lease.resource_type, lease.resource_id, lease.owner, lease.token, row[0]
    )


def assert_current(session: Session, lease: LeaseHandle) -> None:
    """Fence a commit: lock the lease row and verify (owner, token, not expired)."""
    row = session.execute(
        select(Lease.owner, Lease.fencing_token, Lease.expires_at > func.clock_timestamp())
        .where(
            Lease.tenant_id == lease.tenant_id,
            Lease.resource_type == lease.resource_type,
            Lease.resource_id == lease.resource_id,
        )
        .with_for_update()
    ).first()
    if row is None or row[0] != lease.owner or int(row[1]) != lease.token or not row[2]:
        raise StaleLeaseError(f"lease token {lease.token} is no longer current")


def release(session: Session, lease: LeaseHandle) -> bool:
    result = session.execute(
        text(
            "UPDATE leases SET owner = NULL, expires_at = clock_timestamp() "
            "WHERE tenant_id = :t AND resource_type = :rt AND resource_id = :rid "
            "AND owner = :o AND fencing_token = :tok"
        ),
        {
            "t": lease.tenant_id,
            "rt": lease.resource_type,
            "rid": lease.resource_id,
            "o": lease.owner,
            "tok": lease.token,
        },
    )
    return bool(getattr(result, "rowcount", 0))
