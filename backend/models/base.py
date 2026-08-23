"""Common ORM mixins: primary key, timestamps."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TypeDecorator, CHAR, String
import uuid as _uuid

from backend.database.session import Base


class GUID(TypeDecorator):
    """Platform-independent GUID type. Uses PostgreSQL UUID, BINARY(16) elsewhere, falls back to CHAR(32)."""
    impl = CHAR
    cache_ok = True
    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(PG_UUID(as_uuid=True))
        return dialect.type_descriptor(CHAR(32))
    def process_bind_param(self, value, dialect):
        if value is None: return None
        if isinstance(value, uuid.UUID): return str(value) if dialect.name != "postgresql" else value
        return str(value)
    def process_result_value(self, value, dialect):
        if value is None: return None
        if isinstance(value, uuid.UUID): return value
        return uuid.UUID(value)


def _utcnow():
    return datetime.now(timezone.utc)


class UUIDPKMixin:
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)


class BaseModel(Base, UUIDPKMixin, TimestampMixin):
    __abstract__ = True
