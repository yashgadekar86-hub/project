"""
ORM models package — import all models here so Alembic auto-generation
discovers them through Base.metadata.
"""
from backend.database.session import Base  # re-export for convenience
from .base import BaseModel, TimestampMixin, UUIDPKMixin

# User / Account / Settings
from .user import (
    User, UserRole, TradingMode, Broker, TradingAccount, UserSettings, ApiKey,
)

# Market
from .market import Symbol, Candle, Tick, IndicatorSnapshot, Timeframe

# Trading
from .trading import (
    Direction, OrderType, OrderState, PositionState, MarketRegime, TradeMode,
    Strategy, AISignal, Order, Position, Trade, RiskEvent, TradeJournal,
)

# AI / Backtest / System
from .ai import (
    ModelVersion, ModelStatus, Alert, AlertChannel, AlertSeverity,
    Backtest, BacktestTrade, SystemLog, SystemHealthCheck, SystemHealth,
)

__all__ = [
    "Base", "BaseModel", "TimestampMixin", "UUIDPKMixin",
    # user
    "User", "UserRole", "TradingMode", "Broker", "TradingAccount", "UserSettings", "ApiKey",
    # market
    "Symbol", "Candle", "Tick", "IndicatorSnapshot", "Timeframe",
    # trading
    "Direction", "OrderType", "OrderState", "PositionState", "MarketRegime", "TradeMode",
    "Strategy", "AISignal", "Order", "Position", "Trade", "RiskEvent", "TradeJournal",
    # ai
    "ModelVersion", "ModelStatus", "Alert", "AlertChannel", "AlertSeverity",
    "Backtest", "BacktestTrade", "SystemLog", "SystemHealthCheck", "SystemHealth",
]
