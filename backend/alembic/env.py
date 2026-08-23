"""Alembic environment configuration."""
from __future__ import annotations

import asyncio
import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

# Ensure backend/ is on sys.path
BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
# Also ensure repo root is on sys.path for `trading-engine`, `ai-engine`, etc.
REPO_ROOT = BACKEND_DIR.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.core.config import settings  # noqa: E402
from backend.database.session import Base  # noqa: E402
import backend.models  # noqa: E402,F401  # ensure all models are imported & registered

config = context.config
if config.config_file_name is not None:
    try:
        fileConfig(config.config_file_name)
    except Exception:
        pass

target_metadata = Base.metadata


def _get_url() -> str:
    url = settings.DATABASE_URL
    # Alembic migrations can also use sync psycopg2; we use async engine for online
    # so just use the async url — sqlalchemy 2 + asyncpg works via `run_async`.
    return url


def run_migrations_offline() -> None:
    url = _get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    url = _get_url()
    engine = create_async_engine(url, poolclass=pool.NullPool, future=True)
    try:
        async with engine.connect() as conn:
            await conn.run_sync(do_run_migrations)
    finally:
        await engine.dispose()


def run_migrations_online() -> None:
    try:
        asyncio.run(run_async_migrations())
    except RuntimeError as e:
        if "asyncio.run" in str(e):
            loop = asyncio.get_event_loop()
            loop.run_until_complete(run_async_migrations())
        else:
            raise


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
