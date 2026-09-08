"""Deterministic market structure for gold candles (Phase 4).

Computes swing highs/lows, support/resistance clusters, BOS/CHOCH events and
trend — all from price alone, fully reproducible.

Causality (look-ahead safety): a swing at bar ``j`` requires ``fractal_k``
bars on EACH side, so it is only *confirmed* once bar ``j + fractal_k`` is
known. Everything here operates on the frame it is handed; the backtester
hands it only candles up to the decision time, so no future bar can leak in.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .indicators import atr
from .models import Bias, MarketStructure, Swing


def detect_swings(df: pd.DataFrame, fractal_k: int = 2,
                  max_swings: int = 30) -> tuple[list[Swing], list[Swing]]:
    """Detect confirmed fractal swing highs/lows.

    A swing high at bar ``j`` has ``high[j]`` strictly above the ``fractal_k``
    highs on both sides; lows are the mirror. Only swings whose full right-side
    context exists inside ``df`` are returned (i.e. confirmed ones).
    """
    highs: list[Swing] = []
    lows: list[Swing] = []
    if len(df) < 2 * fractal_k + 1:
        return highs, lows

    high = df["high"].to_numpy()
    low = df["low"].to_numpy()
    times = df.index
    last_confirmed = len(df) - 1 - fractal_k

    for j in range(fractal_k, last_confirmed + 1):
        window_hi = np.concatenate([high[j - fractal_k:j], high[j + 1:j + fractal_k + 1]])
        if high[j] > window_hi.max():
            highs.append(Swing(index=j, time=times[j].to_pydatetime(),
                               price=float(high[j]), kind="high"))
        window_lo = np.concatenate([low[j - fractal_k:j], low[j + 1:j + fractal_k + 1]])
        if low[j] < window_lo.min():
            lows.append(Swing(index=j, time=times[j].to_pydatetime(),
                              price=float(low[j]), kind="low"))
    return highs[-max_swings:], lows[-max_swings:]


def classify_trend(swing_highs: list[Swing], swing_lows: list[Swing]) -> Bias:
    """Trend from the last two swings of each kind (HH+HL vs LH+LL)."""
    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return Bias.NEUTRAL
    hh = swing_highs[-1].price > swing_highs[-2].price
    lh = swing_highs[-1].price < swing_highs[-2].price
    hl = swing_lows[-1].price > swing_lows[-2].price
    ll = swing_lows[-1].price < swing_lows[-2].price
    if hh and hl:
        return Bias.BULLISH
    if lh and ll:
        return Bias.BEARISH
    return Bias.NEUTRAL


def last_break_event(df: pd.DataFrame, swing_highs: list[Swing],
                     swing_lows: list[Swing], trend: Bias) -> tuple[str, Bias]:
    """Most recent structural break: BOS (continuation) or CHOCH (reversal).

    A *break* is the newest confirmed close beyond the prior opposite swing.
    We compare the two latest swing highs/lows chronologically and inspect the
    close action after the earlier of them.
    """
    if not swing_highs or not swing_lows or len(df) == 0:
        return "UNKNOWN", Bias.NEUTRAL

    ref_high = swing_highs[-2] if len(swing_highs) >= 2 else None
    ref_low = swing_lows[-2] if len(swing_lows) >= 2 else None

    events: list[tuple[int, str, Bias]] = []
    close = df["close"].to_numpy()

    if ref_high is not None:
        after = range(ref_high.index + 1, len(df))
        for i in after:
            if close[i] > ref_high.price:
                kind = "BOS" if trend == Bias.BULLISH else "CHOCH"
                events.append((i, kind, Bias.BULLISH))
                break
    if ref_low is not None:
        after = range(ref_low.index + 1, len(df))
        for i in after:
            if close[i] < ref_low.price:
                kind = "BOS" if trend == Bias.BEARISH else "CHOCH"
                events.append((i, kind, Bias.BEARISH))
                break

    if not events:
        return "UNKNOWN", Bias.NEUTRAL
    events.sort(key=lambda e: e[0])
    _, kind, direction = events[-1]
    return kind, direction


def cluster_levels(levels: list[float], tolerance: float) -> list[float]:
    """Merge price levels closer than ``tolerance`` into single clusters."""
    if not levels:
        return []
    ordered = sorted(levels)
    clusters: list[list[float]] = [[ordered[0]]]
    for lvl in ordered[1:]:
        if lvl - clusters[-1][-1] <= tolerance:
            clusters[-1].append(lvl)
        else:
            clusters.append([lvl])
    return [float(np.mean(c)) for c in clusters]


def support_resistance(df: pd.DataFrame, swing_highs: list[Swing],
                       swing_lows: list[Swing], atr_value: float | None,
                       max_levels: int = 3) -> tuple[list[float], list[float]]:
    """Cluster swing levels into support (below price) / resistance (above)."""
    price = float(df["close"].iloc[-1])
    tol = max((atr_value or 0.0) * 0.35, price * 0.0005)
    all_levels = [s.price for s in swing_highs] + [s.price for s in swing_lows]
    clustered = cluster_levels(all_levels, tol)
    support = sorted([lvl for lvl in clustered if lvl < price], reverse=True)[:max_levels]
    resistance = sorted([lvl for lvl in clustered if lvl > price])[:max_levels]
    return support, resistance


def analyze_structure(df: pd.DataFrame, fractal_k: int = 2) -> MarketStructure:
    """Full deterministic structure read for one timeframe."""
    ms = MarketStructure()
    if len(df) < 2 * fractal_k + 3:
        ms.note = "insufficient candles for structure"
        return ms

    highs, lows = detect_swings(df, fractal_k=fractal_k)
    ms.swing_highs = highs
    ms.swing_lows = lows
    ms.trend = classify_trend(highs, lows)
    ms.last_event, ms.last_event_dir = last_break_event(df, highs, lows, ms.trend)

    atr_series = atr(df["high"], df["low"], df["close"])
    if len(atr_series) and not pd.isna(atr_series.iloc[-1]):
        ms.atr = float(atr_series.iloc[-1])
        price = float(df["close"].iloc[-1])
        ms.atr_pct = ms.atr / price * 100.0 if price > 0 else None

    ms.support, ms.resistance = support_resistance(df, highs, lows, ms.atr)
    return ms
