"""Database engine and session plumbing."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .models import Base


def create_db_engine(db_path: str | Path, echo: bool = False) -> Engine:
    """Create the SQLite engine for ``db_path``, creating parent dirs as needed."""
    path = Path(db_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{path}", echo=echo, future=True)

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover - driver hook
        cursor = dbapi_conn.cursor()
        # SQLite ignores ON DELETE CASCADE unless foreign keys are enabled per
        # connection, and WAL keeps the log readable while a session is open.
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

    return engine


def init_db(db_path: str | Path, echo: bool = False) -> sessionmaker[Session]:
    """Create the schema if needed and return a session factory."""
    engine = create_db_engine(db_path, echo=echo)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, future=True, expire_on_commit=False)


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    """Transactional scope around a series of operations."""
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
