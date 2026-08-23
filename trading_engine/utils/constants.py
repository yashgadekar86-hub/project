"""Shared constants and enumerations across the trading engine."""
from __future__ import annotations

from enum import Enum


class Timeframe(str, Enum):
    M1 = "M1"
    M5 = "M5"
    M15 = "M15"
    M30 = "M30"
    H1 = "H1"
    H4 = "H4"
    D1 = "D1"
    W1 = "W1"
    MN1 = "MN1"


class Direction(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"
    STOP_LIMIT = "STOP_LIMIT"


class OrderState(str, Enum):
    PENDING = "PENDING"
    VALIDATING = "VALIDATING"
    SUBMITTED = "SUBMITTED"
    FILLED = "FILLED"
    PARTIAL = "PARTIAL"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    ERROR = "ERROR"


class PositionState(str, Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"


class TradingMode(str, Enum):
    BACKTEST = "backtest"
    PAPER = "paper"
    LIVE = "live"


# Forex trading sessions (UTC hours, approximate)
SESSIONS = {
    "Sydney":  {"start_utc": 22, "end_utc": 7},
    "Tokyo":   {"start_utc": 0,  "end_utc": 9},
    "London":  {"start_utc": 7,  "end_utc": 16},
    "NewYork": {"start_utc": 13, "end_utc": 22},
}


# Forex majors for default watchlist (broker symbols are discovered at runtime)
MAJOR_PAIRS_BASE = [
    "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD", "NZDUSD",
]
