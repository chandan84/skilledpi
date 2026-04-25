"""Alembic environment — supports both sync (SQLite) and async (PostgreSQL) engines."""

from __future__ import annotations

import os
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

# ── Alembic config ────────────────────────────────────────────────────────────

alembic_cfg = context.config

if alembic_cfg.config_file_name is not None:
    fileConfig(alembic_cfg.config_file_name)

# Chibu ORM models — all tables must be imported for autogenerate to work
from chibu.db.models import Base  # noqa: E402

target_metadata = Base.metadata

# ── DB URL ────────────────────────────────────────────────────────────────────

def _sync_db_url() -> str:
    """Convert the async DB URL to a sync one for Alembic's synchronous engine."""
    url = os.getenv("CHIBU_DB_URL", "sqlite:///./chibu.db")
    # Strip asyncio driver prefixes so SQLAlchemy can create a sync engine
    return (
        url.replace("sqlite+aiosqlite", "sqlite")
           .replace("postgresql+asyncpg", "postgresql+psycopg2")
    )


# ── Offline mode ──────────────────────────────────────────────────────────────

def run_migrations_offline() -> None:
    """Generate SQL without connecting to the DB (useful for review/dry-run)."""
    context.configure(
        url=_sync_db_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


# ── Online mode ───────────────────────────────────────────────────────────────

def run_migrations_online() -> None:
    """Connect to the DB and run migrations."""
    cfg = alembic_cfg.get_section(alembic_cfg.config_ini_section, {})
    cfg["sqlalchemy.url"] = _sync_db_url()

    connectable = engine_from_config(
        cfg,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
