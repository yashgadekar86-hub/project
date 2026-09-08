"""Deterministic technical indicators for the Gold system (Phase 4).

All functions are pure (pandas/numpy only) so every value is reproducible and
no LLM can invent numbers. Insufficient warm-up yields ``None`` / ``usable=
False`` — callers must treat that as DATA_UNAVAILABLE, never as zero.

Conventions:

* RSI / ATR use Wilder's smoothing (the industry standard MT5/RSI definition).
* MACD is EMA(12) - EMA(26) with a 9-period EMA signal.
* Stochastic %K uses a 3-period SMA smoothing of the raw %K; %D = SMA(%K, 3).
* Everything returns Python floats (or None) for the *latest* bar via
  :func:`compute_indicators`, plus the full series via the individual helpers
  when a caller (structure, backtest) needs history.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# Warm-up requirements per indicator (bars needed for a stable value).
WARMUP = {
    "ema_10": 10, "ema_20": 20, "ema_50": 50, "ema_100": 100, "ema_200": 200,
    "sma_20": 20, "sma_50": 50, "sma_200": 200,
    "rsi": 15, "macd": 35, "stoch": 17, "atr": 15, "boll": 20, "vwma": 20,
}


def _require(df: pd.DataFrame, rows: int) -> bool:
    return len(df) >= rows


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(period, min_periods=period).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    # Wilder's smoothing == EMA with alpha = 1/period.
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100.0 - (100.0 / (1.0 + rs))
    # Where avg_loss == 0 (straight uptrend) RSI is 100 by definition.
    out = out.where(~((avg_loss == 0) & (avg_gain > 0)), 100.0)
    out = out.where(~((avg_loss == 0) & (avg_gain == 0)), 50.0)
    return out


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    macd_line = ema(close, fast) - ema(close, slow)
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    hist = macd_line - signal_line
    return macd_line, signal_line, hist


def stochastic(high: pd.Series, low: pd.Series, close: pd.Series,
               k_period: int = 14, smooth_k: int = 3, d_period: int = 3):
    lowest = low.rolling(k_period, min_periods=k_period).min()
    highest = high.rolling(k_period, min_periods=k_period).max()
    rng = (highest - lowest).replace(0.0, np.nan)
    raw_k = 100.0 * (close - lowest) / rng
    k = raw_k.rolling(smooth_k, min_periods=smooth_k).mean()
    d = k.rolling(d_period, min_periods=d_period).mean()
    return k, d


def atr(high: pd.Series, low: pd.Series, close: pd.Series,
        period: int = 14) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def bollinger(close: pd.Series, period: int = 20, num_std: float = 2.0):
    mid = sma(close, period)
    std = close.rolling(period, min_periods=period).std(ddof=0)
    upper = mid + num_std * std
    lower = mid - num_std * std
    return mid, upper, lower


def vwma(close: pd.Series, volume: pd.Series, period: int = 20) -> pd.Series:
    vol = volume.replace(0.0, np.nan)
    return (close * vol).rolling(period, min_periods=period).sum() / \
        vol.rolling(period, min_periods=period).sum()


def compute_indicators(df: pd.DataFrame) -> tuple[dict[str, float | None], bool]:
    """Compute the full indicator set for the latest bar.

    Returns ``(values, warmup_ok)``. ``warmup_ok`` is False when there is not
    enough history even for the core set (EMA50/RSI/MACD/ATR) — the caller
    must mark the snapshot DATA_UNAVAILABLE in that case.

    Indicators that individually lack warm-up (e.g. EMA200 on a short window)
    are simply ``None``; that is NOT fatal, and must be reported as absent,
    never as zero.
    """
    values: dict[str, float | None] = {}
    close, high, low = df["close"], df["high"], df["low"]
    vol = df["volume"]

    def last_or_none(series: pd.Series, needed: int) -> float | None:
        if not _require(df, needed):
            return None
        v = series.iloc[-1]
        return None if pd.isna(v) else float(v)

    for period in (10, 20, 50, 100, 200):
        values[f"ema_{period}"] = last_or_none(ema(close, period), period)
    for period in (20, 50, 200):
        values[f"sma_{period}"] = last_or_none(sma(close, period), period)

    values["rsi"] = last_or_none(rsi(close), WARMUP["rsi"])
    macd_line, signal_line, hist = macd(close)
    values["macd"] = last_or_none(macd_line, WARMUP["macd"])
    values["macd_signal"] = last_or_none(signal_line, WARMUP["macd"])
    values["macd_hist"] = last_or_none(hist, WARMUP["macd"])
    k, d = stochastic(high, low, close)
    values["stoch_k"] = last_or_none(k, WARMUP["stoch"])
    values["stoch_d"] = last_or_none(d, WARMUP["stoch"])
    atr_series = atr(high, low, close)
    values["atr"] = last_or_none(atr_series, WARMUP["atr"])
    if values["atr"] is not None and float(close.iloc[-1]) > 0:
        values["atr_pct"] = values["atr"] / float(close.iloc[-1]) * 100.0
    else:
        values["atr_pct"] = None
    mid, upper, lower = bollinger(close)
    values["boll_mid"] = last_or_none(mid, WARMUP["boll"])
    values["boll_ub"] = last_or_none(upper, WARMUP["boll"])
    values["boll_lb"] = last_or_none(lower, WARMUP["boll"])
    values["vwma"] = last_or_none(vwma(close, vol), WARMUP["vwma"])
    values["close"] = float(close.iloc[-1])

    # Core warm-up: enough bars for EMA50 + RSI + MACD + ATR to be real.
    warmup_ok = all(
        values[k] is not None for k in ("ema_50", "rsi", "macd", "atr")
    )
    return values, warmup_ok


def ema_stack_bullish(values: dict[str, float | None]) -> bool | None:
    """True when EMA10 > EMA20 > EMA50 (bullish stack), False for the mirror.

    Returns None when any of the three is unavailable — callers must not
    infer a stack from partial data.
    """
    e10, e20, e50 = values.get("ema_10"), values.get("ema_20"), values.get("ema_50")
    if None in (e10, e20, e50):
        return None
    if e10 > e20 > e50:
        return True
    if e10 < e20 < e50:
        return False
    return None
