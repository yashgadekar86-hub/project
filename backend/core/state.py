"""
Per-process application state: MT5 connectors, running paper-trading state,
in-memory caches of the latest ticks / signals. For multi-process deployments
this should be moved to Redis-backed services; for the monorepo dev setup
this keeps the app simple without sacrificing correctness of the logic.
"""
from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pandas as pd

from trading_engine.mt5.connector import MT5Connector, MT5AccountInfo


@dataclass
class PaperPosition:
    ticket: int
    symbol: str
    direction: str
    volume: float
    entry_price: float
    sl: float
    tp: float
    open_time: datetime
    strategy: Optional[str] = None
    ai_confidence: Optional[int] = None
    comment: str = "PAPER"


@dataclass
class AppState:
    # MT5 connector per (user_id, account_id)
    mt5_connectors: Dict[str, MT5Connector] = field(default_factory=dict)
    mt5_lock: threading.Lock = field(default_factory=threading.Lock)

    # In-memory latest ticks + candles
    latest_ticks: Dict[str, Dict[str, Any]] = field(default_factory=dict)     # symbol -> {bid, ask, time, ...}
    candle_cache: Dict[str, Dict[str, pd.DataFrame]] = field(default_factory=dict)  # symbol -> {tf -> df}
    symbol_specs: Dict[str, Any] = field(default_factory=dict)

    # Paper trading positions
    paper_positions: Dict[str, List[PaperPosition]] = field(default_factory=dict)  # user_id -> positions
    paper_next_ticket: int = 9_000_001
    paper_balance: Dict[str, float] = field(default_factory=dict)
    paper_start_balance: Dict[str, float] = field(default_factory=dict)
    paper_trades: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)

    # Latest AI signals
    latest_signals: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)

    # Market data streaming task state
    streaming_tasks: Dict[str, asyncio.Task] = field(default_factory=dict)
    loop: Optional[asyncio.AbstractEventLoop] = None

    # Kill switch (in-memory; mirrored to Redis if available)
    kill_switch_active: bool = False

    # System health
    component_status: Dict[str, Any] = field(default_factory=dict)


STATE = AppState()


def get_state() -> AppState:
    return STATE


def get_or_create_connector(key: str, *, login: int, password: str,
                            server: str, terminal_path: Optional[str] = None) -> MT5Connector:
    with STATE.mt5_lock:
        existing = STATE.mt5_connectors.get(key)
        if existing and existing.connected:
            return existing
        conn = MT5Connector(login=login, password=password, server=server, terminal_path=terminal_path)
        conn.initialize()
        STATE.mt5_connectors[key] = conn
        return conn


def disconnect_connector(key: str) -> None:
    with STATE.mt5_lock:
        conn = STATE.mt5_connectors.pop(key, None)
        if conn is not None:
            try:
                conn.shutdown()
            except Exception:
                pass
