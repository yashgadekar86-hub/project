"""Pydantic schemas for API request/response bodies."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional
import uuid

from pydantic import BaseModel, EmailStr, Field


# ---- Auth ----
class UserCreate(BaseModel):
    email: EmailStr
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=8, max_length=128)
    full_name: Optional[str] = None


class UserLogin(BaseModel):
    email_or_username: str
    password: str


class TokenOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: "UserOut"


class UserOut(BaseModel):
    id: uuid.UUID
    email: EmailStr
    username: str
    full_name: Optional[str]
    role: str
    is_active: bool
    is_verified: bool

    class Config:
        from_attributes = True


class UserSettingsIn(BaseModel):
    trading_mode: Optional[str] = None
    risk_per_trade_pct: Optional[float] = Field(None, ge=0, le=10)
    max_daily_loss_pct: Optional[float] = Field(None, ge=0, le=100)
    max_drawdown_pct: Optional[float] = Field(None, ge=0, le=100)
    max_simultaneous_trades: Optional[int] = Field(None, ge=1, le=50)
    max_lot_size: Optional[float] = Field(None, gt=0)
    min_risk_reward: Optional[float] = Field(None, ge=0)
    max_spread_pips_majors: Optional[float] = Field(None, ge=0)
    max_slippage_pips: Optional[float] = Field(None, ge=0)
    daily_trade_limit: Optional[int] = Field(None, ge=0)
    max_consecutive_losses: Optional[int] = Field(None, ge=0)
    cooldown_after_losses_minutes: Optional[int] = Field(None, ge=0)
    min_ai_confidence: Optional[int] = Field(None, ge=0, le=100)
    auto_trade_enabled: Optional[bool] = None
    break_even_enabled: Optional[bool] = None
    trailing_stop_enabled: Optional[bool] = None
    atr_trailing_stop_enabled: Optional[bool] = None
    partial_close_enabled: Optional[bool] = None
    news_filter_enabled: Optional[bool] = None
    kill_switch_active: Optional[bool] = None
    enabled_strategies: Optional[List[str]] = None
    enabled_symbols: Optional[List[str]] = None
    trading_sessions: Optional[Dict[str, Any]] = None
    extra_config: Optional[Dict[str, Any]] = None


class UserSettingsOut(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    trading_mode: str
    risk_per_trade_pct: float
    max_daily_loss_pct: float
    max_drawdown_pct: float
    max_simultaneous_trades: int
    max_lot_size: float
    min_risk_reward: float
    max_spread_pips_majors: float
    max_slippage_pips: float
    daily_trade_limit: int
    max_consecutive_losses: int
    cooldown_after_losses_minutes: int
    min_ai_confidence: int
    auto_trade_enabled: bool
    break_even_enabled: bool
    trailing_stop_enabled: bool
    atr_trailing_stop_enabled: bool
    partial_close_enabled: bool
    news_filter_enabled: bool
    kill_switch_active: bool
    enabled_strategies: List[str]
    enabled_symbols: List[str]
    trading_sessions: Dict[str, Any]
    extra_config: Dict[str, Any]

    class Config:
        from_attributes = True


# ---- MT5 / Account ----
class MT5ConnectRequest(BaseModel):
    login: int
    password: str
    server: str
    terminal_path: Optional[str] = None
    label: Optional[str] = None
    account_type: Optional[str] = None

class MT5ConnectionStatus(BaseModel):
    connected: bool
    broker: Optional[str] = None
    server: Optional[str] = None
    login: Optional[int] = None
    balance: Optional[float] = None
    equity: Optional[float] = None
    margin_free: Optional[float] = None
    margin_level: Optional[float] = None
    leverage: Optional[int] = None
    currency: Optional[str] = None
    account_type: Optional[str] = None
    symbols_count: Optional[int] = None


# ---- Market ----
class SymbolOut(BaseModel):
    name: str
    broker_name: Optional[str]
    digits: Optional[int]
    point: Optional[float]
    pip_size: Optional[float]
    contract_size: Optional[float]
    lot_min: Optional[float]
    lot_max: Optional[float]
    lot_step: Optional[float]
    spread: Optional[float]
    currency_base: Optional[str]
    currency_profit: Optional[str]
    is_enabled: bool
    is_major: bool

    class Config:
        from_attributes = True


class TickOut(BaseModel):
    symbol: str
    time: datetime
    bid: float
    ask: float
    spread_pips: float


# ---- Signals / AI ----
class AnalyzeRequest(BaseModel):
    symbols: List[str]
    timeframes: Optional[List[str]] = ["M5", "M15", "M30", "H1", "H4", "D1"]
    count: int = Field(300, ge=50, le=2000)
    enabled_strategies: Optional[List[str]] = None
    use_ml: bool = False
    model_name: Optional[str] = None


class SignalOut(BaseModel):
    symbol: str
    direction: Optional[str]
    decision: str
    confidence: int
    market_regime: str
    entry: Optional[float]
    stop_loss: Optional[float]
    take_profit: Optional[float]
    risk_reward: Optional[float]
    reasoning: List[str]
    warnings: List[str]
    trade_allowed: bool
    block_reasons: List[str]
    scores: Dict[str, Any]
    timeframe_alignment: Dict[str, str]
    indicators: Dict[str, Any]
    strategy_name: Optional[str]
    primary_timeframe: str
    explanation_why: List[str]
    explanation_why_not: List[str]


# ---- Orders ----
class OrderValidateRequest(BaseModel):
    symbol: str
    direction: str
    entry: Optional[float] = None
    stop_loss: float
    take_profit: float
    ai_confidence: Optional[int] = None
    signal_id: Optional[uuid.UUID] = None
    strategy_name: Optional[str] = None


class OrderExecuteRequest(OrderValidateRequest):
    trade_mode: str = "paper"   # paper | live (must be explicitly live)
    confirm_live: bool = False


class OrderOut(BaseModel):
    success: bool
    trade_mode: str
    ticket: Optional[int] = None
    execution_price: Optional[float] = None
    volume_lots: float
    risk_amount: float
    estimated_loss_at_sl: float
    estimated_profit_at_tp: float
    risk_reward: Optional[float]
    validation_passed: List[str]
    failures: List[str]
    warnings: List[str]
    rejection_reason: Optional[str] = None


# ---- Positions / Trades ----
class PositionOut(BaseModel):
    ticket: Optional[int]
    symbol: str
    direction: str
    volume_lots: float
    entry_price: float
    current_price: Optional[float]
    stop_loss: Optional[float]
    take_profit: Optional[float]
    pnl: float
    pnl_pips: float
    r_multiple: Optional[float]
    commission: float
    swap: float
    state: str
    exit_reason: Optional[str]
    ai_confidence: Optional[int]
    strategy_name: Optional[str]
    opened_at: Optional[datetime]


class TradeOut(BaseModel):
    id: uuid.UUID
    ticket: Optional[int]
    symbol_name: str
    direction: str
    volume_lots: float
    entry_price: float
    exit_price: float
    stop_loss: Optional[float]
    take_profit: Optional[float]
    net_pnl: float
    is_win: bool
    exit_reason: Optional[str]
    r_multiple: Optional[float]
    ai_confidence: Optional[int]
    strategy_name: Optional[str]
    opened_at: datetime
    closed_at: datetime

    class Config:
        from_attributes = True


# ---- Dashboard / Analytics ----
class DashboardOut(BaseModel):
    balance: float
    equity: float
    today_pnl: float
    open_pnl: float
    drawdown_pct: float
    win_rate: float
    profit_factor: float
    active_trades: int
    ai_confidence_avg: float
    risk_used_pct: float


# ---- Backtest ----
class BacktestRequest(BaseModel):
    symbol: str
    strategy: str
    timeframe: str = "H1"
    start_date: datetime
    end_date: datetime
    initial_balance: float = 10000.0
    commission_per_lot: float = 0.0
    spread_pips: float = 1.0
    slippage_pips: float = 0.5
    risk_per_trade_pct: float = 0.5
    params: Optional[Dict[str, Any]] = None


class BacktestOut(BaseModel):
    net_profit: float
    gross_profit: float
    gross_loss: float
    win_rate: float
    profit_factor: float
    expectancy: float
    max_drawdown_pct: float
    sharpe_ratio: float
    sortino_ratio: float
    avg_win: float
    avg_loss: float
    largest_win: float
    largest_loss: float
    consecutive_wins: int
    consecutive_losses: int
    total_trades: int
    equity_curve: List[Dict[str, Any]]
    drawdown_curve: List[Dict[str, Any]]
    trades: List[Dict[str, Any]]
    monthly_returns: Dict[str, float]


# ---- System / Kill switch ----
class KillSwitchRequest(BaseModel):
    active: bool
    close_positions: bool = False
    reason: Optional[str] = None
    confirmation: str = "CONFIRM"


class SystemHealthOut(BaseModel):
    component: str
    status: str
    latency_ms: Optional[float]
    message: Optional[str]
    last_heartbeat: datetime


TokenOut.model_rebuild()
