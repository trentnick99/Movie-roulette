"""Database engine and session handling (SQLite, WAL mode, foreign keys enforced)."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


_state: dict = {"engine": None, "factory": None, "path": None}


def _pragmas(dbapi_conn, _record) -> None:
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.execute("PRAGMA busy_timeout=10000")
    cur.close()


def make_engine(path: Path | str) -> Engine:
    url = "sqlite://" if str(path) == ":memory:" else f"sqlite:///{Path(path).as_posix()}"
    kw: dict = {"connect_args": {"check_same_thread": False, "timeout": 15}}
    if str(path) == ":memory:":
        from sqlalchemy.pool import StaticPool

        kw["poolclass"] = StaticPool
    eng = create_engine(url, **kw)
    event.listen(eng, "connect", _pragmas)
    return eng


def init_engine(path: Path | str) -> Engine:
    dispose()
    eng = make_engine(path)
    _state.update(engine=eng, factory=sessionmaker(bind=eng, expire_on_commit=False), path=str(path))
    return eng


def dispose() -> None:
    if _state["engine"] is not None:
        _state["engine"].dispose()
    _state.update(engine=None, factory=None)


def engine() -> Engine:
    if _state["engine"] is None:
        raise RuntimeError("database not initialised")
    return _state["engine"]


def new_session() -> Session:
    if _state["factory"] is None:
        raise RuntimeError("database not initialised")
    return _state["factory"]()


@contextmanager
def session_scope() -> Iterator[Session]:
    """A unit of work: commit on success, roll back on any error."""
    s = new_session()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def get_db() -> Iterator[Session]:
    """FastAPI dependency."""
    s = new_session()
    try:
        yield s
    finally:
        s.close()
