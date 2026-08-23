"""
Database engine — defaults to async SQLite (zero-config) if DATABASE_URL
is not set or points to a local file. PostgreSQL is used automatically when
DATABASE_URL starts with `postgresql+asyncpg://...`.
"""
from __future__ import annotations

import os
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from backend.core.config import settings, BASE_DIR


def _resolve_db_url(url: str) -> str:
    if url.startswith(("postgresql", "mysql", "sqlite+aiosqlite:///", "sqlite:///")):
        if url.startswith("sqlite") and not url.startswith("sqlite+aiosqlite"):
            url = url.replace("sqlite://", "sqlite+aiosqlite:///", 1)
        # If relative sqlite path, resolve against repo root
        if url.startswith("sqlite") and "/./" in url:
            rel = url.split("sqlite+aiosqlite:///", 1)[1] if url.startswith("sqlite+aiosqlite") else url.split("sqlite:///", 1)[1]
            abs_path = os.path.abspath(os.path.join(BASE_DIR, rel))
            os.makedirs(os.path.dirname(abs_path), exist_ok=True)
            return f"sqlite+aiosqlite:///{abs_path}"
        return url
    # Default SQLite in repo data/ dir
    data_dir = os.path.join(BASE_DIR, "data")
    os.makedirs(data_dir, exist_ok=True)
    return f"sqlite+aiosqlite:///{os.path.join(data_dir, 'aifx.db')}"


DATABASE_URL = _resolve_db_url(settings.DATABASE_URL)
_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    pool_pre_ping=not DATABASE_URL.startswith("sqlite"),
    future=True,
    connect_args=_connect_args,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=False,
)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()
