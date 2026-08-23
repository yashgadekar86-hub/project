"""
Market Regime Detection.

Classifies the current market into one of the regimes in the spec using a
combination of ADX, slope of EMA, Bollinger Band width, ATR ratio, price
location relative to EMAs/BBs, and optional news risk flag. This is a
quantitative/rule-based classifier — the ML layer can refine or re-label it.
"""
from __future__ import annotations

from typing import Dict, Optional

import numpy as np
import pandas as pd

from trading_engine.indicators.technical import adx, atr, bollinger_bands, ema


REGIMES = [
    "STRONG_BULL_TREND", "WEAK_BULL_TREND",
    "STRONG_BEAR_TREND", "WEAK_BEAR_TREND",
    "RANGE", "HIGH_VOLATILITY", "LOW_VOLATILITY",
    "BREAKOUT", "BREAKOUT_FAILURE",
    "NEWS_RISK", "ILLIQUID", "UNKNOWN",
]


def _safe(v, default=0.0) -> float:
    try:
        f = float(v)
        return f if np.isfinite(f) else default
    except Exception:
        return default


def detect_regime(
    df: pd.DataFrame,
    *,
    news_high_risk: bool = False,
    spread_pips: float = 0.0,
    avg_spread_pips: float = 1.0,
) -> Dict[str, object]:
    """
    Return {"regime": str, "confidence": 0-100, "sub_metrics": {...}}.

    Expects an OHLC DataFrame; computes indicators if missing.
    """
    if df is None or len(df) < 60:
        return {"regime": "UNKNOWN", "confidence": 0, "sub_metrics": {"reason": "insufficient data"}}

    d = df.copy()
    if "adx" not in d.columns:
        d = d.join(adx(d["high"], d["low"], d["close"]))
    if "atr" not in d.columns:
        d["atr"] = atr(d["high"], d["low"], d["close"])
    bb = bollinger_bands(d["close"])
    for c in bb.columns:
        if c not in d.columns:
            d[c] = bb[c]
    for p in (20, 50):
        if f"ema_{p}" not in d.columns:
            d[f"ema_{p}"] = ema(d["close"], p)

    last = d.iloc[-1]
    prev = d.iloc[-10] if len(d) > 10 else d.iloc[0]
    close = _safe(last["close"])
    ema20 = _safe(last["ema_20"])
    ema50 = _safe(last["ema_50"])
    adx_val = _safe(last["adx"])
    atr_now = _safe(last["atr"])
    atr_avg = _safe(d["atr"].iloc[-50:].mean() if len(d) >= 50 else d["atr"].mean())
    bb_width = _safe(last.get("bb_width"), 0)
    bb_width_avg = _safe(d["bb_width"].iloc[-50:].mean() if "bb_width" in d.columns else 0)

    # Slope of EMA50 (normalized)
    ema50_slope_pct = (ema50 - _safe(prev["ema_50"])) / ema50 * 100 if ema50 else 0

    # Volatility ratios
    atr_ratio = atr_now / atr_avg if atr_avg > 0 else 1.0
    bb_ratio = bb_width / bb_width_avg if bb_width_avg > 0 else 1.0

    # Price relative to bands
    bb_pct = _safe(last.get("bb_pct"), 0.5)

    # Illiquid check first
    if avg_spread_pips > 0 and spread_pips > avg_spread_pips * 3:
        return {"regime": "ILLIQUID", "confidence": 80,
                "sub_metrics": {"spread_pips": spread_pips, "avg_spread": avg_spread_pips}}

    # News risk overrides (informational; strategies should still respect the news filter)
    if news_high_risk:
        return {"regime": "NEWS_RISK", "confidence": 95,
                "sub_metrics": {"note": "high-impact news event"}}

    # High volatility
    if atr_ratio > 2.0 or bb_ratio > 1.8:
        return {"regime": "HIGH_VOLATILITY", "confidence": min(95, int(50 + atr_ratio * 15)),
                "sub_metrics": {"atr_ratio": atr_ratio, "bb_ratio": bb_ratio}}

    # Low volatility / squeeze
    if atr_ratio < 0.6 and bb_ratio < 0.7:
        return {"regime": "LOW_VOLATILITY", "confidence": int(60 + (0.7 - bb_ratio) * 100),
                "sub_metrics": {"atr_ratio": atr_ratio, "bb_ratio": bb_ratio}}

    # Trend regimes
    if adx_val >= 25:
        bull = close > ema20 > ema50 and ema50_slope_pct > 0
        bear = close < ema20 < ema50 and ema50_slope_pct < 0
        strong = adx_val >= 30 and abs(ema50_slope_pct) > 0.05
        if bull and strong:
            regime = "STRONG_BULL_TREND"; conf = min(95, int(40 + adx_val))
        elif bull:
            regime = "WEAK_BULL_TREND"; conf = int(40 + adx_val)
        elif bear and strong:
            regime = "STRONG_BEAR_TREND"; conf = min(95, int(40 + adx_val))
        elif bear:
            regime = "WEAK_BEAR_TREND"; conf = int(40 + adx_val)
        else:
            # ADX high but no EMA alignment = potential breakout
            if bb_ratio > 1.2 and (bb_pct > 0.95 or bb_pct < 0.05):
                regime = "BREAKOUT"; conf = 70
            else:
                regime = "RANGE"; conf = 55
        return {"regime": regime, "confidence": conf,
                "sub_metrics": {"adx": adx_val, "atr_ratio": atr_ratio,
                                "ema50_slope_pct": ema50_slope_pct, "bb_pct": bb_pct}}

    # Breakout detection: band expansion + price piercing band
    if bb_ratio > 1.3 and (bb_pct > 0.92 or bb_pct < 0.08):
        return {"regime": "BREAKOUT", "confidence": int(60 + bb_ratio * 10),
                "sub_metrics": {"bb_ratio": bb_ratio, "bb_pct": bb_pct}}

    # Breakout failure: was at band extreme 3-5 bars ago and now back inside
    if len(d) >= 10:
        recent_pct = d["bb_pct"].iloc[-6:-1] if "bb_pct" in d.columns else pd.Series([0.5])
        if (recent_pct > 0.95).any() and 0.3 < bb_pct < 0.7:
            return {"regime": "BREAKOUT_FAILURE", "confidence": 65,
                    "sub_metrics": {"note": "price re-entered band after extreme"}}

    # Default: range
    return {"regime": "RANGE", "confidence": int(50 + (25 - min(25, adx_val))),
            "sub_metrics": {"adx": adx_val, "atr_ratio": atr_ratio, "bb_pct": bb_pct}}
