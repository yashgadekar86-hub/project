"""Shared fixtures/helpers for the gold deterministic-engine tests.

Everything here is synthetic and offline: no network, no MT5, no LLM, no
API keys. Candle generators are seeded and reproducible.
"""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd

from tradingagents.gold.config import GoldConfig
from tradingagents.gold.models import (
    AccountContext,
    BrokerSymbolSpec,
    MarketContext,
    MultiTimeframeView,
)
from tradingagents.gold.timeframes import build_multi_timeframe_view

# A decision timestamp that is:
#  * inside London + NewYork + Overlap (13:00 UTC),
#  * NOT the first Friday of the month (NFP),
#  * NOT an FOMC decision day (Sep 16, 2026),
#  * a Wednesday (liquid weekday).
SAFE_NOW = datetime(2026, 9, 9, 13, 0, tzinfo=timezone.utc)

# FOMC Sep 2026 decision: 2026-09-16 14:00 ET == 18:00 UTC.
FOMC_NOW = datetime(2026, 9, 16, 17, 50, tzinfo=timezone.utc)

# First Friday of Oct 2026: NFP at 08:30 ET (EDT) == 12:30 UTC.
NFP_NOW = datetime(2026, 10, 2, 12, 20, tzinfo=timezone.utc)


def candles_from_path(
    path,
    start: datetime = datetime(2026, 1, 1, tzinfo=timezone.utc),
    freq: str = "1h",
    wick: float = 0.3,
    volume: float = 1000.0,
) -> pd.DataFrame:
    """Build OHLCV candles from an explicit close path.

    Each interior point becomes one candle: open=prev close, close=path[i],
    high/low extend beyond both by ``wick`` (plus a deterministic jitter so
    fractal swings are unambiguous).
    """
    path = [float(p) for p in path]
    rows = []
    idx = pd.date_range(start=start, periods=len(path), freq=freq, tz="UTC")
    for i in range(1, len(path)):
        o, c = path[i - 1], path[i]
        rng = np.random.default_rng(i)
        hi = max(o, c) + wick + 0.05 * rng.random()
        lo = min(o, c) - wick - 0.05 * rng.random()
        rows.append({"open": o, "high": hi, "low": lo, "close": c,
                     "volume": volume})
    df = pd.DataFrame(rows, index=idx[1:])
    return df


def trend_path(n: int = 260, start: float = 2400.0, drift: float = 1.0,
               noise: float = 0.05, seed: int = 7, leg: int = 8,
               pull: int = 3) -> list[float]:
    """A deterministic zigzag trend: legs with the trend, pullbacks against.

    Legs of ``leg`` bars at +drift alternate with pullbacks of ``pull`` bars
    at -1.2*drift, producing unambiguous higher-highs/higher-lows (drift>0)
    or lower-highs/lower-lows (drift<0). Always ENDS on a with-trend leg so
    the final read is the trend itself.
    """
    rng = np.random.default_rng(seed)
    out = [float(start)]
    while len(out) < n:
        for _ in range(leg):
            out.append(out[-1] + drift + noise * rng.standard_normal())
        for _ in range(pull):
            out.append(out[-1] - drift * 1.2 + noise * rng.standard_normal())
    out = out[:n]
    # ensure the path ends moving with the trend
    for _ in range(leg):
        out.append(out[-1] + drift)
    return out


def range_path(n: int = 260, lo: float = 100.0, hi: float = 110.0,
               seed: int = 3) -> list[float]:
    """A deterministic oscillating range path."""
    rng = np.random.default_rng(seed)
    mid = (lo + hi) / 2.0
    out = []
    for i in range(n):
        wave = (hi - lo) / 2.0 * np.sin(i / 6.0)
        out.append(mid + wave + 0.2 * rng.standard_normal())
    return out


def bullish_bearish_frames(bearish_m15: bool = False, bearish_h1: bool = False):
    """Multi-timeframe frames for the classic Phase 3 example.

    D1/H4/H1 bullish; M15 a sharp bearish pullback; M5 a bullish resumption.
    """
    up = trend_path(280, 2400.0, 1.5, seed=11)
    m15_path = trend_path(280, 2400.0, 1.5, seed=11)
    if bearish_m15:
        # long rally then a sharp 25-bar decline: recent structure turns bearish
        m15_path = m15_path[:-25] + [m15_path[-26] - i * 4.0 for i in range(1, 26)]
    h1_path = up if not bearish_h1 else up[:-20] + \
        [up[-21] - i * 6.0 for i in range(1, 21)]
    m5_path = trend_path(200, m15_path[-1] if bearish_m15 else 2450.0,
                         1.0, seed=5)
    return {
        "D1": candles_from_path(up, freq="1D"),
        "H4": candles_from_path(up, freq="4h"),
        "H1": candles_from_path(h1_path, freq="1h"),
        "M15": candles_from_path(m15_path, freq="15min"),
        "M5": candles_from_path(m5_path, freq="5min"),
    }


def breakout_frames():
    """Range then breakout: clear support below, clear resistance above.

    Ends mid-range so there IS resistance above the entry (for TP tests):
    range 100..110, last close ~104, resistance ~110, support ~100.
    """
    path = range_path(240, 100.0, 110.0, seed=9)
    path = path + [104.0, 104.5, 103.8, 104.2]  # settle mid-range
    h1 = candles_from_path(path, freq="1h")
    up = trend_path(200, 2400.0, 1.0, seed=21)
    return {
        "D1": candles_from_path(up, freq="1D"),
        "H4": candles_from_path(up, freq="4h"),
        "H1": h1,
        "M15": candles_from_path(path, freq="15min"),
        "M5": candles_from_path(path, freq="5min"),
    }


def gold_spec(**overrides) -> BrokerSymbolSpec:
    """A typical XAUUSD broker spec (contract 100, tick 0.01 = $1/lot)."""
    base = {
        "symbol": "XAUUSD", "contract_size": 100.0, "tick_size": 0.01,
        "tick_value": 1.0, "point": 0.01, "digits": 2, "volume_min": 0.01,
        "volume_max": 100.0, "volume_step": 0.01, "stops_level_points": 0.0,
        "freeze_level_points": 0.0, "spread_points": 30.0,
    }
    base.update(overrides)
    return BrokerSymbolSpec(**base)


def market_at(price: float, spread_points: float = 30.0,
              point: float = 0.01) -> MarketContext:
    half = spread_points * point / 2.0
    return MarketContext(
        bid=round(price - half, 2), ask=round(price + half, 2),
        spread_points=spread_points,
    )


def account(equity: float = 10_000.0, **kw) -> AccountContext:
    return AccountContext(equity=equity, balance=equity, **kw)


def cfg(**overrides) -> GoldConfig:
    """Isolated engine config: gates ON but confidence floor off."""
    base = {
        "symbol": "XAUUSD", "risk_per_trade": 0.5, "min_rr": 2.0,
        "min_confidence": 0.0, "allow_short": False, "dry_run": True,
        "timezone": "UTC", "working_timeframe": "H1",
    }
    base.update(overrides)
    return GoldConfig(**base)


def mtf_of(frames, min_candles: int = 60) -> MultiTimeframeView:
    return build_multi_timeframe_view(frames, min_candles)


def make_snapshot(
    tf: str = "H1",
    bias: str = "BULLISH",
    support: list | None = None,
    resistance: list | None = None,
    atr: float = 2.0,
    close: float = 104.5,
    usable: bool = True,
    trend: str = "BULLISH",
    last_event: str = "BOS",
):
    """A fully-controlled TechnicalSnapshot for engine unit tests."""
    from tradingagents.gold.models import (
        Bias as _B,
        MarketStructure,
        TechnicalSnapshot,
    )
    structure = MarketStructure(
        trend=_B(trend), last_event=last_event,
        last_event_dir=_B(trend),
        support=list(support or []), resistance=list(resistance or []),
        atr=atr, atr_pct=(atr / close * 100.0) if close else None,
    )
    return TechnicalSnapshot(
        timeframe=tf, candles=300, usable=usable,
        indicators={"close": close, "atr": atr, "rsi": 58.0, "macd_hist": 0.5},
        structure=structure, bias=_B(bias),
    )


def controlled_mtf(
    h1_support=(102.0,), h1_resistance=(112.0,), h1_atr: float = 2.0,
    close: float = 104.5, d1="BULLISH", h4="BULLISH", h1="BULLISH",
    m15="BULLISH", m5="BULLISH", h1_usable: bool = True, h1_trend="BULLISH",
):
    """An MTF view with hand-set biases + H1 structure (engine unit tests)."""
    view = MultiTimeframeView()
    view.per_tf = {
        "D1": make_snapshot("D1", d1, usable=True, close=close, atr=h1_atr * 4),
        "H4": make_snapshot("H4", h4, usable=True, close=close, atr=h1_atr * 2),
        "H1": make_snapshot("H1", h1, support=list(h1_support),
                            resistance=list(h1_resistance), atr=h1_atr,
                            close=close, usable=h1_usable, trend=h1_trend),
        "M15": make_snapshot("M15", m15, usable=True, close=close),
        "M5": make_snapshot("M5", m5, usable=True, close=close),
    }
    view.macro_trend = _bias(d1)
    view.major_structure = _bias(h4)
    view.bias = _bias(h1)
    view.setup = _bias(m15)
    view.entry_confirmation = _bias(m5)
    from tradingagents.gold.models import Bias
    if d1 == h4 and d1 != "NEUTRAL":
        view.aligned_direction = _bias(d1)
    else:
        view.aligned_direction = Bias.NEUTRAL
    view.alignment_score = 1.0 if view.aligned_direction != Bias.NEUTRAL else 0.0
    view.usable = h1_usable
    view.description = (
        f"D1 macro trend: {d1}; H4 structure: {h4}; H1 bias: {h1}; "
        f"M15 setup: {m15}; M5 entry confirmation: {m5}"
    )
    return view


def _bias(name: str):
    from tradingagents.gold.models import Bias
    return Bias(name)
