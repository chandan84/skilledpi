"""Database engine factory — SQLite (default) or PostgreSQL, via SQLAlchemy 2.0.

Swap backend by setting CHIBU_DB_URL in the environment:

  SQLite (default):   sqlite+aiosqlite:///./chibu.db
  PostgreSQL:         postgresql+asyncpg://user:pass@host:5432/chibu

Connection pooling is configured for both backends.  The single *engine*
instance is shared across the process via module-level state.
"""

from __future__ import annotations

import os
from typing import AsyncGenerator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool, QueuePool

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker | None = None

# ── defaults ──────────────────────────────────────────────────────────────────

_SQLITE_POOL_CONFIG = {
    "poolclass": NullPool,  # aiosqlite handles its own connection internally
}

_PG_POOL_CONFIG = {
    "poolclass": QueuePool,
    "pool_size": 10,
    "max_overflow": 20,
    "pool_timeout": 30,
    "pool_recycle": 1800,
    "pool_pre_ping": True,
}


def _build_engine(db_url: str) -> AsyncEngine:
    if db_url.startswith("sqlite"):
        kwargs: dict = dict(_SQLITE_POOL_CONFIG)
        kwargs["connect_args"] = {"check_same_thread": False}
        engine = create_async_engine(db_url, echo=False, **kwargs)

        @event.listens_for(engine.sync_engine, "connect")
        def _set_wal(conn, _record):
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")

        return engine
    else:
        kwargs = dict(_PG_POOL_CONFIG)
        return create_async_engine(db_url, echo=False, **kwargs)


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        db_url = os.getenv(
            "CHIBU_DB_URL", "sqlite+aiosqlite:///./chibu.db"
        )
        _engine = _build_engine(db_url)
    return _engine


def get_session_factory() -> async_sessionmaker:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
    return _session_factory


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency — yields a session, commits on success, rolls back on error."""
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db() -> None:
    """Create all tables (idempotent)."""
    from chibu.db.models import Base

    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def dispose_engine() -> None:
    global _engine
    if _engine is not None:
        await _engine.dispose()
        _engine = None
