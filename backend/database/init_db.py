"""Auto-create DB tables at startup for dev/standalone (SQLite) mode.

In production prefer Alembic migrations (`alembic upgrade head`).
"""
from __future__ import annotations

import logging
from backend.database.session import Base, engine

# Import every model module so Base.metadata knows about all tables
import backend.models  # noqa: F401

log = logging.getLogger("aifx.db")


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    log.info("Database tables initialized.")
