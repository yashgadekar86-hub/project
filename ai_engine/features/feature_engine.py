"""
Feature engineering for ML models.

Builds a structured feature matrix from multi-timeframe OHLC + indicator data.
All features are derived only from the `lookback` window ending at each bar to
prevent look-ahead bias.
"""
from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from trading_engine.indicators.technical import (
    ema, rsi, macd, atr, bollinger_bands, adx, stochastic, cci,
)


def _returns(s: pd.Series, n: int) -> pd.Series:
    return s.pct_change(n)


def _log_returns(s: pd.Series, n: int) -> pd.Series:
    return np.log(s / s.shift(n))


def build_features(df: pd.DataFrame, *, lookback: int = 50) -> pd.DataFrame:
    """
    Given a raw OHLC DataFrame (columns open/high/low/close/(tick_)volume),
    return a DataFrame of model-ready features (no NaNs in final rows when
    sufficient data is provided).
    """
    d = df.copy()
    c = d["close"]; h = d["high"]; l = d["low"]; o = d["open"]; v = d.get("tick_volume", d.get("volume", pd.Series(1, index=d.index)))

    # Price/volume basics
    for n in (1, 2, 3, 5, 10, 20):
        d[f"ret_{n}"] = _returns(c, n)
        d[f"logret_{n}"] = _log_returns(c, n)
        d[f"hl_range_{n}"] = (h.rolling(n).max() - l.rolling(n).min()) / c
    d["body_pct"] = (c - o) / (h - l).replace(0, np.nan)
    d["upper_wick"] = (h - pd.concat([c, o], axis=1).max(axis=1)) / (h - l).replace(0, np.nan)
    d["lower_wick"] = (pd.concat([c, o], axis=1).min(axis=1) - l) / (h - l).replace(0, np.nan)

    # Trend
    for p in (9, 20, 50, 100, 200):
        e = ema(c, p)
        d[f"ema_{p}_dist"] = (c - e) / c
        d[f"ema_{p}_slope"] = e.diff(5) / e
    d["sma50_dist"] = (c - c.rolling(50).mean()) / c

    a = adx(h, l, c, 14)
    d["adx"] = a["adx"]; d["plus_di"] = a["plus_di"]; d["minus_di"] = a["minus_di"]
    d["di_delta"] = d["plus_di"] - d["minus_di"]

    # Momentum
    d["rsi"] = rsi(c, 14)
    m = macd(c); d["macd"] = m["macd"]; d["macd_signal"] = m["signal"]; d["macd_hist"] = m["histogram"]
    st = stochastic(h, l, c); d["stoch_k"] = st["stoch_k"]; d["stoch_d"] = st["stoch_d"]
    d["cci"] = cci(h, l, c, 20)

    # Volatility
    d["atr"] = atr(h, l, c, 14); d["atr_pct"] = d["atr"] / c
    bb = bollinger_bands(c); d["bb_pct"] = bb["bb_pct"]; d["bb_width"] = bb["bb_width"]
    d["stdev20"] = c.rolling(20).std() / c

    # Volume (normalized)
    d["vol_ratio"] = v / v.rolling(20).mean().replace(0, np.nan)

    # Session/time features (cyclic) — requires a tz-aware 'time' index
    if isinstance(d.index, pd.DatetimeIndex):
        idx = d.index
        d["hour_sin"] = np.sin(2 * np.pi * idx.hour / 24)
        d["hour_cos"] = np.cos(2 * np.pi * idx.hour / 24)
        d["dow_sin"] = np.sin(2 * np.pi * idx.dayofweek / 7)
        d["dow_cos"] = np.cos(2 * np.pi * idx.dayofweek / 7)

    # Drop rows that couldn't be computed due to lookback warm-up
    d = d.replace([np.inf, -np.inf], np.nan)
    return d


def build_multi_timeframe_features(
    mtf_data: Dict[str, pd.DataFrame],
    *,
    base_tf: str = "M15",
) -> pd.DataFrame:
    """
    Combine per-timeframe feature matrices by asof-merging onto `base_tf`.
    Each non-base TF's features are suffixed with _TF.
    """
    if base_tf not in mtf_data:
        raise ValueError(f"base_tf {base_tf} not present")
    base = build_features(mtf_data[base_tf]).add_suffix(f"_{base_tf}")
    for tf, df in mtf_data.items():
        if tf == base_tf:
            continue
        feats = build_features(df).add_suffix(f"_{tf}")
        base = pd.merge_asof(
            base.sort_index(), feats.sort_index(),
            left_index=True, right_index=True, direction="backward",
        )
    return base


def generate_labels(df: pd.DataFrame, *, horizon: int = 12, tp_mult: float = 1.5,
                    sl_mult: float = 1.0, atr_col: str = "atr") -> pd.Series:
    """
    Triple-barrier style labels:
       +1 = BUY (upper barrier hit first within `horizon` bars)
       -1 = SELL (lower barrier hit first)
        0 = NO TRADE (sideways / no clear edge / barrier not hit)

    Uses ATR-based barriers so labels adapt to volatility.
    """
    c = df["close"]; a = df[atr_col] if atr_col in df.columns else df["close"].pct_change().rolling(20).std()
    labels = pd.Series(0, index=df.index, dtype=int)
    for i in range(len(df) - horizon):
        up = c.iloc[i] + tp_mult * a.iloc[i]
        dn = c.iloc[i] - sl_mult * a.iloc[i]
        future = c.iloc[i + 1:i + 1 + horizon]
        hit_up = (future >= up).any()
        hit_dn = (future <= dn).any()
        first_up = future[future >= up].index.min() if hit_up else None
        first_dn = future[future <= dn].index.min() if hit_dn else None
        if first_up is not None and (first_dn is None or first_up < first_dn):
            labels.iloc[i] = 1
        elif first_dn is not None and (first_up is None or first_dn < first_up):
            labels.iloc[i] = -1
        else:
            labels.iloc[i] = 0
    return labels
