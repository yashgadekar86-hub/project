"""Market data service: streaming ticks/candles from MT5 into memory/Redis."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from backend.core.config import settings
from backend.core.state import STATE
from backend.websocket.manager import WSMessage, manager
from trading_engine.mt5.connector import MT5Connector
from trading_engine.utils.constants import Timeframe

log = logging.getLogger("aifx.market_data")

_TF_MAP = {
    "M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440,
}


async def fetch_candles(conn: MT5Connector, symbol: str, timeframe: str = "H1",
                        count: int = 500) -> pd.DataFrame:
    """Fetch OHLC candles from MT5 and return a properly indexed DataFrame."""
    def _fetch():
        return conn.get_candles(symbol, timeframe=timeframe, count=count)
    candles = await asyncio.to_thread(_fetch)
    if not candles:
        return pd.DataFrame(columns=["open", "high", "low", "close", "tick_volume", "spread", "volume"])
    df = pd.DataFrame([{
        "time": c.time, "open": c.open, "high": c.high, "low": c.low,
        "close": c.close, "tick_volume": c.tick_volume, "spread": c.spread,
        "volume": getattr(c, "volume", 0),
    } for c in candles]).set_index("time").sort_index()
    return df


async def stream_prices(user_id: str, account_key: str, symbols: List[str], poll_interval: float = 1.0):
    """Background task: poll MT5 ticks for `symbols` and push updates over WebSocket."""
    conn = STATE.mt5_connectors.get(account_key)
    if conn is None:
        return
    from trading_engine.indicators.technical import atr as calc_atr, rsi as calc_rsi, ema as calc_ema
    from trading_engine.utils.sessions import current_sessions

    while True:
        if STATE.kill_switch_active:
            await asyncio.sleep(poll_interval * 5)
            continue
        try:
            for sym in symbols:
                def _tick(s=sym):
                    return conn.get_tick(s)
                tick = await asyncio.to_thread(_tick)
                spec = STATE.symbol_specs.get(sym)
                pip_size = spec.pip_size if spec else max(1e-4, float(tick.ask - tick.bid) or 1e-4)
                spread_pips = (tick.ask - tick.bid) / pip_size if pip_size else 0
                msg = {
                    "symbol": sym,
                    "bid": tick.bid,
                    "ask": tick.ask,
                    "spread_pips": round(spread_pips, 2),
                    "time": tick.time.isoformat(),
                    "session": current_sessions(),
                }
                STATE.latest_ticks[sym] = {
                    **msg, "market_open": True, "data_fresh": True,
                }
                await manager.broadcast(f"tick:{sym}", WSMessage(type="tick", topic=f"tick:{sym}", payload=msg))
                await manager.broadcast(f"user:{user_id}", WSMessage(type="tick", topic=f"tick:{sym}", payload=msg))
        except Exception as e:
            log.warning("stream_prices error: %s", e)
            await manager.broadcast(f"user:{user_id}", WSMessage(type="system", topic="mt5", payload={"event":"stream_error","error":str(e)}))
        await asyncio.sleep(poll_interval)
