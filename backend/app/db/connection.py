from __future__ import annotations

import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings

logger = logging.getLogger(__name__)

# SQLite waits this many milliseconds (and SQLAlchemy seconds) on a locked DB.
_SQLITE_BUSY_TIMEOUT_MS = 5000

_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None
_bound_path: str | None = None


def ensure_db_parent_dir() -> None:
    Path(settings.sqlite_path).parent.mkdir(parents=True, exist_ok=True)


def _sqlite_url(path: str) -> str:
    return f"sqlite:///{Path(path).resolve()}"


def _set_sqlite_pragma(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute(f"PRAGMA busy_timeout={_SQLITE_BUSY_TIMEOUT_MS}")
    cursor.close()


def get_engine() -> Engine:
    """Return engine bound to current ``settings.sqlite_path`` (recreates on path change)."""
    global _engine, _SessionLocal, _bound_path

    path = str(Path(settings.sqlite_path).resolve())
    if _engine is not None and _bound_path == path:
        return _engine

    if _engine is not None:
        _engine.dispose()

    ensure_db_parent_dir()
    engine = create_engine(
        _sqlite_url(path),
        connect_args={
            "check_same_thread": False,
            "timeout": _SQLITE_BUSY_TIMEOUT_MS / 1000.0,
        },
    )
    event.listen(engine, "connect", _set_sqlite_pragma)
    _engine = engine
    _SessionLocal = sessionmaker(
        bind=engine, autoflush=False, autocommit=False, expire_on_commit=False
    )
    _bound_path = path
    return engine


def get_session_factory() -> sessionmaker[Session]:
    get_engine()
    assert _SessionLocal is not None
    return _SessionLocal


@contextmanager
def get_db() -> Iterator[Session]:
    session = get_session_factory()()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def enable_wal() -> None:
    """Ensure WAL is enabled (also applied on every new connection)."""
    with get_engine().connect() as conn:
        conn.execute(text("PRAGMA journal_mode=WAL"))


def check_sqlite_integrity() -> str:
    """Run ``PRAGMA integrity_check`` and log ERROR when the image is not ok."""
    try:
        with get_engine().connect() as conn:
            rows = conn.execute(text("PRAGMA integrity_check")).fetchall()
        result = "; ".join(str(row[0]) for row in rows) if rows else "unknown"
    except Exception as exc:  # noqa: BLE001
        logger.error("sqlite integrity_check raised: %s", exc)
        return f"error: {exc}"
    if result != "ok":
        logger.error("sqlite integrity_check failed: %s", result)
    else:
        logger.info("sqlite integrity_check ok")
    return result
