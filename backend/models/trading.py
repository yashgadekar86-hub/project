"""Signals, orders, positions, trades, risk events, trade journal models."""
from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, Boolean, DateTime, Enum as SAEnum, Float, ForeignKey, Integer, String, Text, JSON, Index
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.models.base import BaseModel


class Direction(str, enum.Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, enum.Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"
    STOP_LIMIT = "STOP_LIMIT"


class OrderState(str, enum.Enum):
    PENDING = "PENDING"
    VALIDATING = "VALIDATING"
    SUBMITTED = "SUBMITTED"
    FILLED = "FILLED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    ERROR = "ERROR"


class PositionState(str, enum.Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    CLOSED_BY_SL = "CLOSED_BY_SL"
    CLOSED_BY_TP = "CLOSED_BY_TP"
    CLOSED_BY_TRAIL = "CLOSED_BY_TRAIL"
    CLOSED_BY_BREAKEVEN = "CLOSED_BY_BREAKEVEN"
    CLOSED_BY_KILLSWITCH = "CLOSED_BY_KILLSWITCH"
    CLOSED_MANUAL = "CLOSED_MANUAL"
    CLOSED_BY_TIMEOUT = "CLOSED_BY_TIMEOUT"
    CLOSED_BY_INVALIDATION = "CLOSED_BY_INVALIDATION"


class MarketRegime(str, enum.Enum):
    STRONG_BULL_TREND = "STRONG_BULL_TREND"
    WEAK_BULL_TREND = "WEAK_BULL_TREND"
    STRONG_BEAR_TREND = "STRONG_BEAR_TREND"
    WEAK_BEAR_TREND = "WEAK_BEAR_TREND"
    RANGE = "RANGE"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    LOW_VOLATILITY = "LOW_VOLATILITY"
    BREAKOUT = "BREAKOUT"
    BREAKOUT_FAILURE = "BREAKOUT_FAILURE"
    NEWS_RISK = "NEWS_RISK"
    ILLIQUID = "ILLIQUID"
    UNKNOWN = "UNKNOWN"


class TradeMode(str, enum.Enum):
    LIVE = "live"
    PAPER = "paper"
    BACKTEST = "backtest"


class Strategy(BaseModel):
    """Registered strategy plugins."""
    __tablename__ = "strategies"

    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    version: Mapped[str] = mapped_column(String(32), default="1.0.0", nullable=False)
    params: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class AISignal(BaseModel):
    """Structured AI-generated trade signal (or NO_TRADE)."""
    __tablename__ = "ai_signals"

    account_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("accounts.id"), nullable=True)
    symbol_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("symbols.id"), nullable=False)
    strategy_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("strategies.id"), nullable=True)
    model_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("model_versions.id"), nullable=True)

    symbol_name: Mapped[str] = mapped_column(String(32), nullable=False)
    direction: Mapped[Direction] = mapped_column(SAEnum(Direction), nullable=True)  # null for NO_TRADE
    decision: Mapped[str] = mapped_column(String(16), nullable=False)  # BUY / SELL / NO_TRADE
    confidence: Mapped[int] = mapped_column(Integer, nullable=False)  # 0-100

    market_regime: Mapped[MarketRegime] = mapped_column(SAEnum(MarketRegime), default=MarketRegime.UNKNOWN)

    entry: Mapped[Optional[float]] = mapped_column(Float)
    stop_loss: Mapped[Optional[float]] = mapped_column(Float)
    take_profit: Mapped[Optional[float]] = mapped_column(Float)
    risk_reward: Mapped[Optional[float]] = mapped_column(Float)

    # Scoring breakdown (0-100 components)
    trend_score: Mapped[Optional[int]] = mapped_column(Integer)
    momentum_score: Mapped[Optional[int]] = mapped_column(Integer)
    structure_score: Mapped[Optional[int]] = mapped_column(Integer)
    volatility_score: Mapped[Optional[int]] = mapped_column(Integer)
    entry_score: Mapped[Optional[int]] = mapped_column(Integer)
    session_score: Mapped[Optional[int]] = mapped_column(Integer)
    spread_score: Mapped[Optional[int]] = mapped_column(Integer)

    timeframe_alignment: Mapped[dict] = mapped_column(JSON, default=dict)
    indicators_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    reasoning: Mapped[list] = mapped_column(JSON, default=list)
    warnings: Mapped[list] = mapped_column(JSON, default=list)
    trade_allowed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    block_reasons: Mapped[list] = mapped_column(JSON, default=list)

    # Multi-timeframe context
    primary_timeframe: Mapped[str] = mapped_column(String(8), default="H1", nullable=False)

    trade_mode: Mapped[TradeMode] = mapped_column(SAEnum(TradeMode), default=TradeMode.PAPER, nullable=False)


class Order(BaseModel):
    """Order lifecycle (validation → MT5 → fill/reject)."""
    __tablename__ = "orders"

    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    signal_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("ai_signals.id"), nullable=True)
    strategy_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("strategies.id"), nullable=True)
    position_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("positions.id"), nullable=True)

    trade_mode: Mapped[TradeMode] = mapped_column(SAEnum(TradeMode), default=TradeMode.PAPER, nullable=False)

    ticket: Mapped[Optional[int]] = mapped_column(BigInteger, index=True)  # MT5 ticket (or internal for paper)
    symbol_name: Mapped[str] = mapped_column(String(32), nullable=False)

    order_type: Mapped[OrderType] = mapped_column(SAEnum(OrderType), default=OrderType.MARKET, nullable=False)
    direction: Mapped[Direction] = mapped_column(SAEnum(Direction), nullable=False)
    volume_lots: Mapped[float] = mapped_column(Float, nullable=False)
    requested_price: Mapped[Optional[float]] = mapped_column(Float)
    execution_price: Mapped[Optional[float]] = mapped_column(Float)
    stop_loss: Mapped[Optional[float]] = mapped_column(Float)
    take_profit: Mapped[Optional[float]] = mapped_column(Float)

    state: Mapped[OrderState] = mapped_column(SAEnum(OrderState), default=OrderState.PENDING, nullable=False)

    # Risk / validation
    risk_amount: Mapped[Optional[float]] = mapped_column(Float)
    risk_pct: Mapped[Optional[float]] = mapped_column(Float)
    estimated_loss_at_sl: Mapped[Optional[float]] = mapped_column(Float)
    estimated_profit_at_tp: Mapped[Optional[float]] = mapped_column(Float)
    slippage_pips: Mapped[Optional[float]] = mapped_column(Float)
    spread_at_entry_pips: Mapped[Optional[float]] = mapped_column(Float)

    # AI metadata
    ai_confidence: Mapped[Optional[int]] = mapped_column(Integer)
    market_regime: Mapped[Optional[MarketRegime]] = mapped_column(SAEnum(MarketRegime))
    strategy_name: Mapped[Optional[str]] = mapped_column(String(64))

    # Validation log
    validation_checks: Mapped[dict] = mapped_column(JSON, default=dict)
    rejection_reason: Mapped[Optional[str]] = mapped_column(Text)
    mt5_return_code: Mapped[Optional[int]] = mapped_column(Integer)
    mt5_comment: Mapped[Optional[str]] = mapped_column(String(255))
    error_message: Mapped[Optional[str]] = mapped_column(Text)

    # Execution times
    submitted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    filled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class Position(BaseModel):
    """Open (or recently closed) position."""
    __tablename__ = "positions"

    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    order_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("orders.id"), nullable=True)
    signal_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("ai_signals.id"), nullable=True)
    strategy_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("strategies.id"), nullable=True)

    trade_mode: Mapped[TradeMode] = mapped_column(SAEnum(TradeMode), default=TradeMode.PAPER, nullable=False)

    ticket: Mapped[Optional[int]] = mapped_column(BigInteger, index=True)
    symbol_name: Mapped[str] = mapped_column(String(32), nullable=False)
    direction: Mapped[Direction] = mapped_column(SAEnum(Direction), nullable=False)
    volume_lots: Mapped[float] = mapped_column(Float, nullable=False)

    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    current_price: Mapped[Optional[float]] = mapped_column(Float)
    stop_loss: Mapped[Optional[float]] = mapped_column(Float)
    take_profit: Mapped[Optional[float]] = mapped_column(Float)
    trailing_stop_pips: Mapped[Optional[float]] = mapped_column(Float)
    break_even_triggered: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    pnl: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    pnl_pips: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    r_multiple: Mapped[Optional[float]] = mapped_column(Float)
    commission: Mapped[float] = mapped_column(Float, default=0.0)
    swap: Mapped[float] = mapped_column(Float, default=0.0)

    state: Mapped[PositionState] = mapped_column(SAEnum(PositionState), default=PositionState.OPEN, nullable=False)
    exit_reason: Mapped[Optional[str]] = mapped_column(String(120))
    exit_price: Mapped[Optional[float]] = mapped_column(Float)

    ai_confidence: Mapped[Optional[int]] = mapped_column(Integer)
    market_regime: Mapped[Optional[MarketRegime]] = mapped_column(SAEnum(MarketRegime))
    strategy_name: Mapped[Optional[str]] = mapped_column(String(64))

    opened_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    # Management metadata
    partial_closes: Mapped[list] = mapped_column(JSON, default=list)
    management_log: Mapped[list] = mapped_column(JSON, default=list)


class Trade(BaseModel):
    """Closed-trade record (links to originating position + order)."""
    __tablename__ = "trades"

    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    position_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("positions.id"), nullable=False)
    signal_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("ai_signals.id"), nullable=True)
    strategy_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("strategies.id"), nullable=True)

    trade_mode: Mapped[TradeMode] = mapped_column(SAEnum(TradeMode), default=TradeMode.PAPER, nullable=False)

    ticket: Mapped[Optional[int]] = mapped_column(BigInteger)
    symbol_name: Mapped[str] = mapped_column(String(32), nullable=False)
    direction: Mapped[Direction] = mapped_column(SAEnum(Direction), nullable=False)
    volume_lots: Mapped[float] = mapped_column(Float, nullable=False)

    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    exit_price: Mapped[float] = mapped_column(Float, nullable=False)
    stop_loss: Mapped[Optional[float]] = mapped_column(Float)
    take_profit: Mapped[Optional[float]] = mapped_column(Float)

    pnl: Mapped[float] = mapped_column(Float, nullable=False)
    pnl_pips: Mapped[float] = mapped_column(Float, default=0.0)
    r_multiple: Mapped[Optional[float]] = mapped_column(Float)
    commission: Mapped[float] = mapped_column(Float, default=0.0)
    swap: Mapped[float] = mapped_column(Float, default=0.0)
    net_pnl: Mapped[float] = mapped_column(Float, nullable=False)

    is_win: Mapped[bool] = mapped_column(Boolean, nullable=False)
    exit_reason: Mapped[Optional[str]] = mapped_column(String(120))

    ai_confidence: Mapped[Optional[int]] = mapped_column(Integer)
    market_regime: Mapped[Optional[MarketRegime]] = mapped_column(SAEnum(MarketRegime))
    strategy_name: Mapped[Optional[str]] = mapped_column(String(64))

    duration_seconds: Mapped[Optional[float]] = mapped_column(Float)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Audit trail
    indicators_at_entry: Mapped[dict] = mapped_column(JSON, default=dict)
    reasoning_at_entry: Mapped[list] = mapped_column(JSON, default=list)
    validation_checks: Mapped[dict] = mapped_column(JSON, default=dict)
    slippage_pips: Mapped[Optional[float]] = mapped_column(Float)
    spread_at_entry_pips: Mapped[Optional[float]] = mapped_column(Float)


class RiskEvent(BaseModel):
    """Risk engine events (rejections, breaches, cooldowns, killswitch)."""
    __tablename__ = "risk_events"

    account_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("accounts.id"), nullable=True)
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"), nullable=True)

    event_type: Mapped[str] = mapped_column(String(64), nullable=False)  # e.g. "DAILY_LOSS_BREACH", "SPREAD_TOO_HIGH"
    severity: Mapped[str] = mapped_column(String(16), nullable=False)   # INFO / WARNING / ERROR / CRITICAL
    symbol_name: Mapped[Optional[str]] = mapped_column(String(32))
    message: Mapped[str] = mapped_column(Text, nullable=False)
    extra_metadata: Mapped[dict] = mapped_column(JSON, default=dict)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class TradeJournal(BaseModel):
    """Append-only journal of every AI decision + outcome."""
    __tablename__ = "trade_journal"

    account_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("accounts.id"), nullable=True)
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"), nullable=True)
    signal_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("ai_signals.id"), nullable=True)
    position_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("positions.id"), nullable=True)
    order_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("orders.id"), nullable=True)
    trade_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("trades.id"), nullable=True)

    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)  # SIGNAL / ORDER_SUBMIT / ORDER_FILL / etc.
    symbol_name: Mapped[Optional[str]] = mapped_column(String(32))
    direction: Mapped[Optional[Direction]] = mapped_column(SAEnum(Direction))
    decision: Mapped[Optional[str]] = mapped_column(String(16))  # BUY / SELL / NO_TRADE
    confidence: Mapped[Optional[int]] = mapped_column(Integer)

    entry: Mapped[Optional[float]] = mapped_column(Float)
    sl: Mapped[Optional[float]] = mapped_column(Float)
    tp: Mapped[Optional[float]] = mapped_column(Float)
    lot: Mapped[Optional[float]] = mapped_column(Float)
    risk_amount: Mapped[Optional[float]] = mapped_column(Float)

    market_regime: Mapped[Optional[MarketRegime]] = mapped_column(SAEnum(MarketRegime))
    indicators: Mapped[dict] = mapped_column(JSON, default=dict)
    reason: Mapped[Optional[str]] = mapped_column(Text)
    result: Mapped[Optional[str]] = mapped_column(String(64))  # WIN / LOSS / BREAKEVEN / REJECTED
    pnl: Mapped[Optional[float]] = mapped_column(Float)
    r_multiple: Mapped[Optional[float]] = mapped_column(Float)
    exit_reason: Mapped[Optional[str]] = mapped_column(String(120))

    session: Mapped[Optional[str]] = mapped_column(String(32))  # Sydney/Tokyo/London/NY
    trade_mode: Mapped[Optional[TradeMode]] = mapped_column(SAEnum(TradeMode))

    extra_metadata: Mapped[dict] = mapped_column(JSON, default=dict)
