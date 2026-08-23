"""Market data, symbols, indicators models."""
from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, Boolean, DateTime, Enum as SAEnum, Float, ForeignKey, Integer, String, Text, JSON, Index
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.models.base import BaseModel


class Symbol(BaseModel):
    """Discovered symbol (e.g. EURUSD) — properties read from MT5, not hard-coded."""
    __tablename__ = "symbols"

    account_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), nullable=True)
    name: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    broker_name: Mapped[Optional[str]] = mapped_column(String(32))  # broker-reported name (may have suffix)
    description: Mapped[Optional[str]] = mapped_column(String(255))
    path: Mapped[Optional[str]] = mapped_column(String(120))

    # Specs from MT5
    digits: Mapped[Optional[int]] = mapped_column(Integer)
    point: Mapped[Optional[float]] = mapped_column(Float)
    pip_size: Mapped[Optional[float]] = mapped_column(Float)
    pip_value_per_lot: Mapped[Optional[float]] = mapped_column(Float)
    contract_size: Mapped[Optional[float]] = mapped_column(Float)
    lot_min: Mapped[Optional[float]] = mapped_column(Float)
    lot_max: Mapped[Optional[float]] = mapped_column(Float)
    lot_step: Mapped[Optional[float]] = mapped_column(Float)
    tick_size: Mapped[Optional[float]] = mapped_column(Float)
    tick_value: Mapped[Optional[float]] = mapped_column(Float)
    spread: Mapped[Optional[float]] = mapped_column(Float)  # last seen spread in points
    spread_float: Mapped[Optional[bool]] = mapped_column(Boolean)
    stops_level: Mapped[Optional[int]] = mapped_column(Integer)
    freeze_level: Mapped[Optional[int]] = mapped_column(Integer)

    trade_calc_mode: Mapped[Optional[int]] = mapped_column(Integer)  # ENUM_SYMBOL_CALC_MODE
    trade_mode: Mapped[Optional[int]] = mapped_column(Integer)         # ENUM_SYMBOL_TRADE_MODE
    trade_exemode: Mapped[Optional[int]] = mapped_column(Integer)      # execution mode
    swap_mode: Mapped[Optional[int]] = mapped_column(Integer)
    swap_long: Mapped[Optional[float]] = mapped_column(Float)
    swap_short: Mapped[Optional[float]] = mapped_column(Float)

    currency_base: Mapped[Optional[str]] = mapped_column(String(8))
    currency_profit: Mapped[Optional[str]] = mapped_column(String(8))
    currency_margin: Mapped[Optional[str]] = mapped_column(String(8))

    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_major: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    extra: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    candles: Mapped[list["Candle"]] = relationship(back_populates="symbol", cascade="all,delete-orphan")
    ticks: Mapped[list["Tick"]] = relationship(back_populates="symbol", cascade="all,delete-orphan")
    indicator_cache: Mapped[list["IndicatorSnapshot"]] = relationship(back_populates="symbol", cascade="all,delete-orphan")

    __table_args__ = (
        Index("ix_symbols_account_name", "account_id", "name", unique=True),
    )


class Timeframe(str, enum.Enum):
    M1 = "M1"
    M5 = "M5"
    M15 = "M15"
    M30 = "M30"
    H1 = "H1"
    H4 = "H4"
    D1 = "D1"


class Candle(BaseModel):
    """OHLCV bar."""
    __tablename__ = "market_data"

    symbol_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("symbols.id", ondelete="CASCADE"), nullable=False)
    timeframe: Mapped[Timeframe] = mapped_column(SAEnum(Timeframe), nullable=False, index=True)
    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)

    open: Mapped[float] = mapped_column(Float, nullable=False)
    high: Mapped[float] = mapped_column(Float, nullable=False)
    low: Mapped[float] = mapped_column(Float, nullable=False)
    close: Mapped[float] = mapped_column(Float, nullable=False)
    tick_volume: Mapped[float] = mapped_column(Float, default=0)
    volume: Mapped[float] = mapped_column(Float, default=0)
    spread: Mapped[Optional[float]] = mapped_column(Float)

    symbol: Mapped["Symbol"] = relationship(back_populates="candles")

    __table_args__ = (
        Index("ix_marketdata_symbol_tf_time", "symbol_id", "timeframe", "time", unique=True),
    )


class Tick(BaseModel):
    """Last-price tick (short-term cache; older ticks rolled off)."""
    __tablename__ = "ticks"

    symbol_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("symbols.id", ondelete="CASCADE"), nullable=False)
    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    bid: Mapped[float] = mapped_column(Float, nullable=False)
    ask: Mapped[float] = mapped_column(Float, nullable=False)
    last: Mapped[Optional[float]] = mapped_column(Float)
    volume: Mapped[Optional[float]] = mapped_column(Float)
    flags: Mapped[Optional[int]] = mapped_column(Integer)

    symbol: Mapped["Symbol"] = relationship(back_populates="ticks")

    __table_args__ = (
        Index("ix_ticks_symbol_time", "symbol_id", "time"),
    )


class IndicatorSnapshot(BaseModel):
    """Cached indicator values per (symbol, timeframe)."""
    __tablename__ = "indicators"

    symbol_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("symbols.id", ondelete="CASCADE"), nullable=False)
    timeframe: Mapped[Timeframe] = mapped_column(SAEnum(Timeframe), nullable=False)
    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)

    indicator_name: Mapped[str] = mapped_column(String(64), nullable=False)
    values: Mapped[dict] = mapped_column(JSON, nullable=False)  # e.g. {"ema9": 1.08, ...}

    symbol: Mapped["Symbol"] = relationship(back_populates="indicator_cache")

    __table_args__ = (
        Index("ix_ind_sym_tf_name_time", "symbol_id", "timeframe", "indicator_name", "time"),
    )
