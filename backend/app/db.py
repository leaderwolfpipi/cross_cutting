"""Engine / session management. SQLite runs in WAL mode with a busy timeout so API and workers can share it;
PostgreSQL is the production target (same code, DATABASE_URL=postgresql+psycopg://...)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .config import get_settings

_engine: Engine | None = None
_factory: sessionmaker[Session] | None = None


def utcnow() -> datetime:
    """Naive UTC timestamps everywhere (SQLite has no tz type; keep both backends identical)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def engine() -> Engine:
    global _engine, _factory
    if _engine is None:
        url = get_settings().db_url
        if url.startswith("sqlite"):
            _engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30}, pool_pre_ping=True)

            @event.listens_for(_engine, "connect")
            def _pragmas(conn, _):  # noqa: ANN001
                cur = conn.cursor()
                cur.execute("PRAGMA journal_mode=WAL")
                cur.execute("PRAGMA synchronous=NORMAL")
                cur.execute("PRAGMA foreign_keys=ON")
                cur.execute("PRAGMA busy_timeout=30000")
                cur.close()
        else:
            _engine = create_engine(url, pool_pre_ping=True, pool_size=10, max_overflow=20)
        _factory = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)
    return _engine


def SessionLocal() -> Session:  # noqa: N802 - conventional name
    engine()
    assert _factory is not None
    return _factory()


@contextmanager
def session_scope() -> Iterator[Session]:
    s = SessionLocal()
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
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


def init_db() -> None:
    from . import models  # noqa: F401 - register tables

    models.Base.metadata.create_all(engine())


def reset_engine() -> None:
    """Tests: drop the cached engine after changing settings."""
    global _engine, _factory
    if _engine is not None:
        _engine.dispose()
    _engine, _factory = None, None
