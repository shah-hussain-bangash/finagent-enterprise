"""Engine and session factory. Sync SQLAlchemy: FastAPI runs sync endpoints in a threadpool and
the R1 pilot load (NQ §2: 10 rps) does not justify async complexity. Revisit with load evidence."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker


@lru_cache(maxsize=4)
def get_engine(url: str) -> Engine:
    return create_engine(url, pool_pre_ping=True, pool_size=10, max_overflow=10)


def session_factory(url: str) -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(url), expire_on_commit=False)


@contextmanager
def transaction(factory: sessionmaker[Session]) -> Iterator[Session]:
    """One explicit transaction boundary: commit on success, rollback on any error."""
    session = factory()
    try:
        with session.begin():
            yield session
    finally:
        session.close()
