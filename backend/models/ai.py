"""AI models / model versions / backtests / alerts / system logs."""
from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Enum as SAEnum, Float, ForeignKey, Integer, String, Text, JSON, Index
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.models.base import BaseModel


class ModelStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    TRAINING = "TRAINING"
    VALIDATED = "VALIDATED"
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"
    REJECTED = "REJECTED"


class AlertChannel(str, enum.Enum):
    WEB = "WEB"
    DESKTOP = "DESKTOP"
    TELEGRAM = "TELEGRAM"
    EMAIL = "EMAIL"
    WEBSOCKET = "WEBSOCKET"


class AlertSeverity(str, enum.Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class SystemHealth(str, enum.Enum):
    HEALTHY = "HEALTHY"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class ModelVersion(BaseModel):
    """Tracks trained ML model versions."""
    __tablename__ = "model_versions"

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    model_type: Mapped[str] = mapped_column(String(64), nullable=False)  # xgboost, lgbm, logistic, etc.
    strategy: Mapped[Optional[str]] = mapped_column(String(64))
    symbol_set: Mapped[list] = mapped_column(JSON, default=list)
    timeframe_set: Mapped[list] = mapped_column(JSON, default=list)

    status: Mapped[ModelStatus] = mapped_column(SAEnum(ModelStatus), default=ModelStatus.DRAFT, nullable=False)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)   # accuracy, precision, recall, sharpe, etc.
    hyperparams: Mapped[dict] = mapped_column(JSON, default=dict)
    feature_list: Mapped[list] = mapped_column(JSON, default=list)
    train_start: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    train_end: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    validation_start: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    validation_end: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    file_path: Mapped[Optional[str]] = mapped_column(String(500))
    training_hash: Mapped[Optional[str]] = mapped_column(String(128))

    notes: Mapped[Optional[str]] = mapped_column(Text)

    __table_args__ = (
        Index("ix_modelversions_name_version", "name", "version", unique=True),
    )


class Backtest(BaseModel):
    """Backtest run metadata."""
    __tablename__ = "backtests"

    account_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("accounts.id"), nullable=True)
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"), nullable=True)
    strategy_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("strategies.id"), nullable=True)
    model_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("model_versions.id"), nullable=True)

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    symbols: Mapped[list] = mapped_column(JSON, default=list)
    timeframes: Mapped[list] = mapped_column(JSON, default=list)
    start_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    initial_balance: Mapped[float] = mapped_column(Float, default=10000.0, nullable=False)
    commission_per_lot: Mapped[float] = mapped_column(Float, default=0.0)
    spread_pips: Mapped[float] = mapped_column(Float, default=1.0)
    slippage_pips: Mapped[float] = mapped_column(Float, default=0.5)
    swap_enabled: Mapped[bool] = mapped_column(Boolean, default=False)

    risk_per_trade_pct: Mapped[float] = mapped_column(Float, default=0.5)

    params: Mapped[dict] = mapped_column(JSON, default=dict)
    walk_forward: Mapped[bool] = mapped_column(Boolean, default=False)
    walk_forward_windows: Mapped[Optional[dict]] = mapped_column(JSON)

    status: Mapped[str] = mapped_column(String(32), default="PENDING")  # PENDING/RUNNING/COMPLETED/FAILED
    error_message: Mapped[Optional[str]] = mapped_column(Text)

    # Aggregate results
    net_profit: Mapped[Optional[float]] = mapped_column(Float)
    gross_profit: Mapped[Optional[float]] = mapped_column(Float)
    gross_loss: Mapped[Optional[float]] = mapped_column(Float)
    win_rate: Mapped[Optional[float]] = mapped_column(Float)
    profit_factor: Mapped[Optional[float]] = mapped_column(Float)
    expectancy: Mapped[Optional[float]] = mapped_column(Float)
    max_drawdown_pct: Mapped[Optional[float]] = mapped_column(Float)
    sharpe_ratio: Mapped[Optional[float]] = mapped_column(Float)
    sortino_ratio: Mapped[Optional[float]] = mapped_column(Float)
    avg_win: Mapped[Optional[float]] = mapped_column(Float)
    avg_loss: Mapped[Optional[float]] = mapped_column(Float)
    largest_win: Mapped[Optional[float]] = mapped_column(Float)
    largest_loss: Mapped[Optional[float]] = mapped_column(Float)
    consecutive_wins: Mapped[Optional[int]] = mapped_column(Integer)
    consecutive_losses: Mapped[Optional[int]] = mapped_column(Integer)
    total_trades: Mapped[Optional[int]] = mapped_column(Integer)

    equity_curve: Mapped[list] = mapped_column(JSON, default=list)
    drawdown_curve: Mapped[list] = mapped_column(JSON, default=list)
    monthly_returns: Mapped[dict] = mapped_column(JSON, default=dict)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)

    backtest_trades: Mapped[list["BacktestTrade"]] = relationship(back_populates="backtest", cascade="all,delete-orphan")


class BacktestTrade(BaseModel):
    """Single trade within a backtest."""
    __tablename__ = "backtest_trades"

    backtest_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("backtests.id", ondelete="CASCADE"), nullable=False)
    symbol_name: Mapped[str] = mapped_column(String(32), nullable=False)
    direction: Mapped[str] = mapped_column(String(8), nullable=False)  # BUY/SELL
    volume_lots: Mapped[float] = mapped_column(Float, nullable=False)

    entry_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    exit_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    exit_price: Mapped[Optional[float]] = mapped_column(Float)
    stop_loss: Mapped[Optional[float]] = mapped_column(Float)
    take_profit: Mapped[Optional[float]] = mapped_column(Float)

    pnl: Mapped[Optional[float]] = mapped_column(Float)
    pnl_pips: Mapped[Optional[float]] = mapped_column(Float)
    commission: Mapped[float] = mapped_column(Float, default=0.0)
    swap: Mapped[float] = mapped_column(Float, default=0.0)
    net_pnl: Mapped[Optional[float]] = mapped_column(Float)

    exit_reason: Mapped[Optional[str]] = mapped_column(String(120))
    r_multiple: Mapped[Optional[float]] = mapped_column(Float)
    strategy_name: Mapped[Optional[str]] = mapped_column(String(64))
    confidence: Mapped[Optional[int]] = mapped_column(Integer)

    backtest: Mapped["Backtest"] = relationship(back_populates="backtest_trades")


class Alert(BaseModel):
    """User-facing alerts (signals, executions, SL/TP hits, breaches, killswitch, etc.)."""
    __tablename__ = "alerts"

    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    account_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("accounts.id"), nullable=True)

    channel: Mapped[AlertChannel] = mapped_column(SAEnum(AlertChannel), default=AlertChannel.WEBSOCKET, nullable=False)
    severity: Mapped[AlertSeverity] = mapped_column(SAEnum(AlertSeverity), default=AlertSeverity.INFO, nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False)  # SIGNAL/EXECUTION/RISK/SYSTEM/etc.
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    symbol_name: Mapped[Optional[str]] = mapped_column(String(32))
    ticket: Mapped[Optional[int]] = mapped_column(Integer)
    extra_metadata: Mapped[dict] = mapped_column(JSON, default=dict)

    delivered: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    delivered_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    read: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class SystemLog(BaseModel):
    """Structured system log entries (mirrors the logging spec)."""
    __tablename__ = "system_logs"

    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    service: Mapped[str] = mapped_column(String(64), nullable=False)
    event: Mapped[str] = mapped_column(String(120), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)

    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"), nullable=True)
    symbol_name: Mapped[Optional[str]] = mapped_column(String(32))
    ticket: Mapped[Optional[int]] = mapped_column(Integer)
    extra_metadata: Mapped[dict] = mapped_column(JSON, default=dict)


class SystemHealthCheck(BaseModel):
    """Periodic health snapshots for monitoring page."""
    __tablename__ = "system_health"

    component: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[SystemHealth] = mapped_column(SAEnum(SystemHealth), nullable=False)
    latency_ms: Mapped[Optional[float]] = mapped_column(Float)
    message: Mapped[Optional[str]] = mapped_column(Text)
    extra_metadata: Mapped[dict] = mapped_column(JSON, default=dict)
    last_heartbeat: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
