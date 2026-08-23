"""User, Account, Broker, Settings models."""
from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Enum as SAEnum, Float, ForeignKey, Integer, String, Text, JSON, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.models.base import BaseModel


class UserRole(str, enum.Enum):
    ADMIN = "ADMIN"
    TRADER = "TRADER"
    VIEWER = "VIEWER"


class TradingMode(str, enum.Enum):
    BACKTEST = "backtest"
    PAPER = "paper"
    LIVE = "live"


class User(BaseModel):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[Optional[str]] = mapped_column(String(255))
    role: Mapped[UserRole] = mapped_column(SAEnum(UserRole), default=UserRole.TRADER, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    accounts: Mapped[list["TradingAccount"]] = relationship(back_populates="user", cascade="all,delete-orphan")
    settings: Mapped[Optional["UserSettings"]] = relationship(back_populates="user", uselist=False, cascade="all,delete-orphan")
    api_keys: Mapped[list["ApiKey"]] = relationship(back_populates="user", cascade="all,delete-orphan")


class Broker(BaseModel):
    """Broker metadata. FortressFX (or any MT5 broker) is a row here — never hard-coded."""
    __tablename__ = "brokers"

    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    server_name: Mapped[Optional[str]] = mapped_column(String(120))
    mt5_compatible: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    symbol_suffix: Mapped[Optional[str]] = mapped_column(String(16))
    default_leverage: Mapped[Optional[int]] = mapped_column(Integer)
    notes: Mapped[Optional[str]] = mapped_column(Text)
    config: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    accounts: Mapped[list["TradingAccount"]] = relationship(back_populates="broker")


class TradingAccount(BaseModel):
    """One MT5 (demo/live) account connection per user. Encrypted credentials."""
    __tablename__ = "accounts"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    broker_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("brokers.id"), nullable=True)

    label: Mapped[str] = mapped_column(String(120), nullable=False)
    mt5_login: Mapped[Optional[int]] = mapped_column(Integer)
    mt5_server: Mapped[Optional[str]] = mapped_column(String(120))
    mt5_password_encrypted: Mapped[Optional[str]] = mapped_column(Text)
    mt5_path: Mapped[Optional[str]] = mapped_column(String(500))
    account_type: Mapped[Optional[str]] = mapped_column(String(60))
    currency: Mapped[Optional[str]] = mapped_column(String(8))
    leverage: Mapped[Optional[int]] = mapped_column(Integer)
    balance_snapshot: Mapped[float] = mapped_column(Float, default=0.0)
    equity_snapshot: Mapped[float] = mapped_column(Float, default=0.0)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    live_trading_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    live_activated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    live_activated_by: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)  # username of activator (audit only)

    user: Mapped["User"] = relationship(back_populates="accounts", foreign_keys=[user_id])
    broker: Mapped[Optional["Broker"]] = relationship(back_populates="accounts")


class UserSettings(BaseModel):
    """Per-user trading/risk settings (overrides global defaults)."""
    __tablename__ = "settings"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)

    trading_mode: Mapped[TradingMode] = mapped_column(SAEnum(TradingMode), default=TradingMode.PAPER, nullable=False)

    risk_per_trade_pct: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)
    max_daily_loss_pct: Mapped[float] = mapped_column(Float, default=2.0, nullable=False)
    max_drawdown_pct: Mapped[float] = mapped_column(Float, default=5.0, nullable=False)
    max_simultaneous_trades: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    max_lot_size: Mapped[float] = mapped_column(Float, default=10.0, nullable=False)
    min_risk_reward: Mapped[float] = mapped_column(Float, default=2.0, nullable=False)
    max_spread_pips_majors: Mapped[float] = mapped_column(Float, default=3.0, nullable=False)
    max_slippage_pips: Mapped[float] = mapped_column(Float, default=2.0, nullable=False)
    daily_trade_limit: Mapped[int] = mapped_column(Integer, default=20, nullable=False)
    max_consecutive_losses: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    cooldown_after_losses_minutes: Mapped[int] = mapped_column(Integer, default=60, nullable=False)

    min_ai_confidence: Mapped[int] = mapped_column(Integer, default=70, nullable=False)

    auto_trade_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    break_even_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    trailing_stop_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    atr_trailing_stop_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    partial_close_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    news_filter_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    kill_switch_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    enabled_strategies: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    enabled_symbols: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    trading_sessions: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    extra_config: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    user: Mapped["User"] = relationship(back_populates="settings")


class ApiKey(BaseModel):
    """API keys for programmatic access. Stored hashed like passwords."""
    __tablename__ = "api_keys"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    key_prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    user: Mapped["User"] = relationship(back_populates="api_keys")
