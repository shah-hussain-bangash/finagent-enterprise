"""LangGraph checkpointer stored in OUR schema (prompt §7, §20; FR-031; AER-021; ADR-0004).

One saver instance serves ONE run of ONE tenant: the thread id must equal the run id, every row
carries the tenant, and every payload is AES-256-GCM encrypted with associated data
`tenant:run:namespace:kind`, so rows copied across tenants or runs cannot be decrypted. Writes
(`put`, `put_writes`) are FENCED: they run in a transaction that first asserts the caller's lease
token is still current, so a superseded worker can never advance a run's state.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from collections.abc import Iterator, Sequence
from typing import Any, cast

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    WRITES_IDX_MAP,
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    get_checkpoint_id,
    get_checkpoint_metadata,
)
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, sessionmaker

from finagent.core.crypto import Encryptor
from finagent.database.models import Checkpoint as CheckpointRow
from finagent.database.models import CheckpointBlob, CheckpointWrite
from finagent.database.session import transaction
from finagent.worker.leases import LeaseHandle, assert_current

_RNG = secrets.SystemRandom()


class CheckpointScopeError(Exception):
    """A config tried to read or write another run's checkpoints."""


class TenantCheckpointer(BaseCheckpointSaver[str]):
    def __init__(
        self,
        factory: sessionmaker[Session],
        *,
        tenant_id: uuid.UUID,
        run_id: uuid.UUID,
        graph_version: str,
        encryptor: Encryptor,
        lease: LeaseHandle | None,
    ) -> None:
        super().__init__()
        self._factory, self._tenant, self._run = factory, tenant_id, run_id
        self._graph_version, self._enc, self._lease = graph_version, encryptor, lease

    # ------------------------------------------------------------------ helpers
    def _scope(self, config: RunnableConfig) -> tuple[str, str]:
        conf = config.get("configurable", {})
        if str(conf.get("thread_id")) != str(self._run):
            raise CheckpointScopeError("thread_id must equal this saver's run id")
        return str(conf.get("thread_id")), str(conf.get("checkpoint_ns", ""))

    def _aad(self, ns: str, kind: str) -> bytes:
        return f"{self._tenant}:{self._run}:{ns}:{kind}".encode()

    def _dump(self, value: Any, ns: str, kind: str) -> tuple[str, bytes]:
        type_, data = self.serde.dumps_typed(value)
        return type_, self._enc.encrypt(data, self._aad(ns, kind))

    def _load(self, type_: str, data: bytes, ns: str, kind: str) -> Any:
        return self.serde.loads_typed((type_, self._enc.decrypt(data, self._aad(ns, kind))))

    def _fence(self, session: Session) -> None:
        if self._lease is None:
            raise CheckpointScopeError("checkpoint writes require a lease")
        assert_current(session, self._lease)

    def _tuple(self, session: Session, row: CheckpointRow) -> CheckpointTuple:
        ns = row.checkpoint_ns
        checkpoint: Checkpoint = self._load(row.checkpoint_type, row.checkpoint_data, ns, "checkpoint")
        values: dict[str, Any] = {}
        for channel, version in checkpoint["channel_versions"].items():
            blob = session.get(CheckpointBlob, (self._tenant, self._run, ns, channel, str(version)))
            if blob is not None and blob.value_type != "empty":
                values[channel] = self._load(blob.value_type, blob.value_data, ns, f"blob:{channel}")
        writes = session.scalars(
            select(CheckpointWrite)
            .where(
                CheckpointWrite.tenant_id == self._tenant,
                CheckpointWrite.run_id == self._run,
                CheckpointWrite.checkpoint_ns == ns,
                CheckpointWrite.checkpoint_id == row.checkpoint_id,
            )
            .order_by(CheckpointWrite.task_path, CheckpointWrite.task_id, CheckpointWrite.idx)
        ).all()
        thread = str(self._run)
        return CheckpointTuple(
            config={
                "configurable": {"thread_id": thread, "checkpoint_ns": ns, "checkpoint_id": row.checkpoint_id}
            },
            checkpoint={**checkpoint, "channel_values": values},
            metadata=self._load(row.metadata_type, row.metadata_data, ns, "metadata"),
            parent_config=(
                {
                    "configurable": {
                        "thread_id": thread,
                        "checkpoint_ns": ns,
                        "checkpoint_id": row.parent_checkpoint_id,
                    }
                }
                if row.parent_checkpoint_id
                else None
            ),
            pending_writes=[
                (w.task_id, w.channel, self._load(w.value_type, w.value_data, ns, f"write:{w.channel}"))
                for w in writes
            ],
        )

    # ------------------------------------------------------------------ reads
    def get_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        _, ns = self._scope(config)
        checkpoint_id = get_checkpoint_id(config)
        with transaction(self._factory) as s:
            stmt = select(CheckpointRow).where(
                CheckpointRow.tenant_id == self._tenant,
                CheckpointRow.run_id == self._run,
                CheckpointRow.checkpoint_ns == ns,
            )
            stmt = (
                stmt.where(CheckpointRow.checkpoint_id == checkpoint_id)
                if checkpoint_id
                else stmt.order_by(CheckpointRow.checkpoint_id.desc()).limit(1)
            )
            row = s.scalar(stmt)
            return self._tuple(s, row) if row is not None else None

    def list(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> Iterator[CheckpointTuple]:
        ns = self._scope(config)[1] if config else None
        with transaction(self._factory) as s:
            stmt = select(CheckpointRow).where(
                CheckpointRow.tenant_id == self._tenant, CheckpointRow.run_id == self._run
            )
            if ns is not None:
                stmt = stmt.where(CheckpointRow.checkpoint_ns == ns)
            if before is not None and (before_id := get_checkpoint_id(before)):
                stmt = stmt.where(CheckpointRow.checkpoint_id < before_id)
            rows = s.scalars(stmt.order_by(CheckpointRow.checkpoint_id.desc())).all()
            out: list[CheckpointTuple] = []
            for row in rows:
                tup = self._tuple(s, row)
                if filter and not all(tup.metadata.get(k) == v for k, v in filter.items()):
                    continue
                out.append(tup)
                if limit is not None and len(out) >= limit:
                    break
        yield from out

    # ------------------------------------------------------------------ fenced writes
    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        thread, ns = self._scope(config)
        c = dict(checkpoint)
        values = cast(dict[str, Any], c.pop("channel_values", {}))
        ck_type, ck_data = self._dump(c, ns, "checkpoint")
        md_type, md_data = self._dump(get_checkpoint_metadata(config, metadata), ns, "metadata")
        plain_hash = hashlib.sha256(self.serde.dumps_typed(c)[1]).hexdigest()
        with transaction(self._factory) as s:
            self._fence(s)
            for channel, version in new_versions.items():
                if channel in values:
                    vt, vd = self._dump(values[channel], ns, f"blob:{channel}")
                else:
                    vt, vd = "empty", b""
                s.execute(
                    pg_insert(CheckpointBlob)
                    .values(
                        tenant_id=self._tenant,
                        run_id=self._run,
                        checkpoint_ns=ns,
                        channel=channel,
                        version=str(version),
                        value_type=vt,
                        value_data=vd,
                    )
                    .on_conflict_do_nothing()
                )
            s.execute(
                pg_insert(CheckpointRow)
                .values(
                    tenant_id=self._tenant,
                    run_id=self._run,
                    checkpoint_ns=ns,
                    checkpoint_id=checkpoint["id"],
                    parent_checkpoint_id=config["configurable"].get("checkpoint_id"),
                    graph_version=self._graph_version,
                    fencing_token=self._lease.token if self._lease else 0,
                    checkpoint_type=ck_type,
                    checkpoint_data=ck_data,
                    metadata_type=md_type,
                    metadata_data=md_data,
                    payload_hash=plain_hash,
                )
                .on_conflict_do_nothing()
            )
        return {"configurable": {"thread_id": thread, "checkpoint_ns": ns, "checkpoint_id": checkpoint["id"]}}

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        _, ns = self._scope(config)
        checkpoint_id = config["configurable"]["checkpoint_id"]
        with transaction(self._factory) as s:
            self._fence(s)
            for idx, (channel, value) in enumerate(writes):
                widx = WRITES_IDX_MAP.get(channel, idx)
                vt, vd = self._dump(value, ns, f"write:{channel}")
                stmt = pg_insert(CheckpointWrite).values(
                    tenant_id=self._tenant,
                    run_id=self._run,
                    checkpoint_ns=ns,
                    checkpoint_id=checkpoint_id,
                    task_id=task_id,
                    idx=widx,
                    channel=channel,
                    task_path=task_path,
                    value_type=vt,
                    value_data=vd,
                )
                # Special writes (errors/interrupts, negative idx) may be replaced; regular writes
                # are immutable once stored (same rule as LangGraph's reference savers).
                if widx < 0:
                    stmt = stmt.on_conflict_do_update(
                        index_elements=[
                            "tenant_id",
                            "run_id",
                            "checkpoint_ns",
                            "checkpoint_id",
                            "task_id",
                            "idx",
                        ],
                        set_={"channel": channel, "value_type": vt, "value_data": vd},
                    )
                else:
                    stmt = stmt.on_conflict_do_nothing()
                s.execute(stmt)

    def delete_thread(self, thread_id: str) -> None:
        raise NotImplementedError("checkpoints follow the retention policy (DEC-05); no ad-hoc deletion")

    def get_next_version(self, current: str | None, channel: None) -> str:
        if current is None:
            current_v = 0
        elif isinstance(current, int):
            current_v = current
        else:
            current_v = int(str(current).split(".")[0])
        return f"{current_v + 1:032}.{_RNG.random():016}"  # ordering tiebreak only
