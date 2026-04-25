"""Lazy singleton Honker database — SQLite only, control plane only."""

from __future__ import annotations

import os
import re

import honker

_hdb: honker.Database | None = None


def _sqlite_path_from_url(db_url: str) -> str:
    m = re.match(r"sqlite\+aiosqlite:///(.+)", db_url)
    if not m:
        raise ValueError(f"Honker requires a SQLite URL; got: {db_url!r}")
    return m.group(1)


def init_honker() -> honker.Database:
    """Open the Honker database. Called once from the app lifespan."""
    global _hdb
    if _hdb is not None:
        return _hdb
    db_url = os.getenv("CHIBU_DB_URL", "sqlite+aiosqlite:///./chibu.db")
    if not db_url.startswith("sqlite"):
        raise RuntimeError(
            "Honker is only supported with SQLite. "
            "Set CHIBU_HONKER_ENABLED=false or switch to SQLite."
        )
    path = _sqlite_path_from_url(db_url)
    _hdb = honker.open(path)
    # Bootstrap schema and warm up named primitives
    _hdb.queue("snapshot", max_attempts=5, visibility_timeout_s=30)
    _hdb.queue("hot_reload", max_attempts=3, visibility_timeout_s=20)
    _hdb.stream("agent_events")
    return _hdb


def get_hdb() -> honker.Database:
    if _hdb is None:
        raise RuntimeError("Honker not initialised — call init_honker() first")
    return _hdb
