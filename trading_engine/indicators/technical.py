"""
Technical indicator library.

Wraps the `ta` library where possible with pure-pandas fallbacks. All
functions accept pandas Series/DataFrames and return pandas objects, so the
indicator engine is modular & independently configurable.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import numpy as np
import pandas as pd


# ----- Trend -----

def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period, min_periods=period).mean()


def adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.DataFrame:
    try:
        import ta
        df = ta.trend.ADXIndicator(high=high, low=low, close=close, window=period, fillna=False)
        return pd.DataFrame({"adx": df.adx(), "plus_di": df.adx_pos(), "minus_di": df.adx_neg()})
    except Exception:
        return _adx_fallback(high, low, close, period)


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    try:
        import ta
        m = ta.trend.MACD(close=close, window_fast=fast, window_slow=slow, window_sign=signal, fillna=False)
        return pd.DataFrame({"macd": m.macd(), "signal": m.macd_signal(), "histogram": m.macd_diff()})
    except Exception:
        ema_fast = ema(close, fast)
        ema_slow = ema(close, slow)
        macd_line = ema_fast - ema_slow
        sig = ema(macd_line, signal)
        return pd.DataFrame({"macd": macd_line, "signal": sig, "histogram": macd_line - sig})


# ----- Momentum -----

def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    try:
        import ta
        return ta.momentum.RSIIndicator(close=close, window=period, fillna=False).rsi()
    except Exception:
        return _rsi_fallback(close, period)


def stochastic(high: pd.Series, low: pd.Series, close: pd.Series,
               k_period: int = 14, d_period: int = 3, smooth_k: int = 3) -> pd.DataFrame:
    try:
        import ta
        st = ta.momentum.StochasticOscillator(high=high, low=low, close=close,
                                              window=k_period, smooth_window=smooth_k, fillna=False)
        k = st.stoch()
        d = k.rolling(d_period).mean()
        return pd.DataFrame({"stoch_k": k, "stoch_d": d})
    except Exception:
        low_min = low.rolling(k_period).min()
        high_max = high.rolling(k_period).max()
        k = 100 * (close - low_min) / (high_max - low_min).replace(0, np.nan)
        if smooth_k > 1:
            k = k.rolling(smooth_k).mean()
        d = k.rolling(d_period).mean()
        return pd.DataFrame({"stoch_k": k, "stoch_d": d})


def cci(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 20) -> pd.Series:
    try:
        import ta
        return ta.trend.CCIIndicator(high=high, low=low, close=close, window=period, fillna=False).cci()
    except Exception:
        tp = (high + low + close) / 3
        sma_tp = tp.rolling(period).mean()
        mad = tp.rolling(period).apply(lambda x: np.mean(np.abs(x - np.mean(x))), raw=True)
        return (tp - sma_tp) / (0.015 * mad.replace(0, np.nan))


# ----- Volatility -----

def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    try:
        import ta
        return ta.volatility.AverageTrueRange(high=high, low=low, close=close, window=period, fillna=False).average_true_range()
    except Exception:
        prev_close = close.shift(1)
        tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
        return tr.ewm(alpha=1 / period, adjust=False).mean()


def bollinger_bands(close: pd.Series, period: int = 20, std_dev: float = 2.0) -> pd.DataFrame:
    try:
        import ta
        bb = ta.volatility.BollingerBands(close=close, window=period, window_dev=std_dev, fillna=False)
        return pd.DataFrame({
            "bb_upper": bb.bollinger_hband(),
            "bb_mid": bb.bollinger_mavg(),
            "bb_lower": bb.bollinger_lband(),
            "bb_width": bb.bollinger_wband(),
            "bb_pct": bb.bollinger_pband(),
        })
    except Exception:
        mid = close.rolling(period).mean()
        std = close.rolling(period).std()
        upper = mid + std_dev * std
        lower = mid - std_dev * std
        width = (upper - lower) / mid.replace(0, np.nan)
        pct = (close - lower) / (upper - lower).replace(0, np.nan)
        return pd.DataFrame({"bb_upper": upper, "bb_mid": mid, "bb_lower": lower,
                             "bb_width": width, "bb_pct": pct})


def standard_deviation(close: pd.Series, period: int = 20) -> pd.Series:
    return close.rolling(period).std()


# ----- Market structure -----

def swing_highs_lows(high: pd.Series, low: pd.Series, length: int = 2) -> pd.DataFrame:
    swing_high = pd.Series(False, index=high.index)
    swing_low = pd.Series(False, index=low.index)
    for i in range(length, len(high) - length):
        if high.iloc[i] == high.iloc[i - length:i + length + 1].max():
            swing_high.iloc[i] = True
        if low.iloc[i] == low.iloc[i - length:i + length + 1].min():
            swing_low.iloc[i] = True
    return pd.DataFrame({"swing_high": swing_high, "swing_low": swing_low})


def market_structure(high: pd.Series, low: pd.Series, close: pd.Series,
                     length: int = 2) -> Dict[str, pd.Series]:
    swings = swing_highs_lows(high, low, length=length)
    sh = swings["swing_high"]
    sl = swings["swing_low"]

    hh = pd.Series(False, index=high.index)
    hl = pd.Series(False, index=high.index)
    lh = pd.Series(False, index=high.index)
    ll = pd.Series(False, index=high.index)
    bos_up = pd.Series(False, index=high.index)
    bos_down = pd.Series(False, index=high.index)
    choch = pd.Series(False, index=high.index)

    prev_sh_px: Optional[float] = None
    prev_sl_px: Optional[float] = None
    structure_bias: Optional[str] = None
    last_structure_high: Optional[float] = None
    last_structure_low: Optional[float] = None

    for i in range(len(high)):
        if sh.iloc[i]:
            px = float(high.iloc[i])
            if prev_sh_px is not None:
                if px > prev_sh_px:
                    hh.iloc[i] = True
                    if structure_bias == "bear":
                        choch.iloc[i] = True
                    structure_bias = "bull"
                elif px < prev_sh_px:
                    lh.iloc[i] = True
            prev_sh_px = px
            last_structure_high = px
        if sl.iloc[i]:
            px = float(low.iloc[i])
            if prev_sl_px is not None:
                if px > prev_sl_px:
                    hl.iloc[i] = True
                elif px < prev_sl_px:
                    ll.iloc[i] = True
                    if structure_bias == "bull":
                        choch.iloc[i] = True
                    structure_bias = "bear"
            prev_sl_px = px
            last_structure_low = px

        if last_structure_high is not None and close.iloc[i] > last_structure_high:
            bos_up.iloc[i] = True
            structure_bias = "bull"
        if last_structure_low is not None and close.iloc[i] < last_structure_low:
            bos_down.iloc[i] = True
            structure_bias = "bear"

    return {
        "hh": hh, "hl": hl, "lh": lh, "ll": ll,
        "bos_up": bos_up, "bos_down": bos_down, "choch": choch,
        "swing_high": sh, "swing_low": sl,
    }


# ----- Pivots -----

def pivot_levels(prev_high: float, prev_low: float, prev_close: float) -> Dict[str, float]:
    pivot = (prev_high + prev_low + prev_close) / 3
    r1 = 2 * pivot - prev_low
    s1 = 2 * pivot - prev_high
    r2 = pivot + (prev_high - prev_low)
    s2 = pivot - (prev_high - prev_low)
    return {"pivot": pivot, "r1": r1, "s1": s1, "r2": r2, "s2": s2}


# ----- Fallbacks -----

def _rsi_fallback(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _adx_fallback(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.DataFrame:
    prev_close = close.shift(1)
    up_move = high - high.shift(1)
    down_move = low.shift(1) - low
    plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=high.index)
    minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=high.index)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    atr_s = tr.ewm(alpha=1 / period, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr_s.replace(0, np.nan)
    minus_di = 100 * minus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr_s.replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx_s = dx.ewm(alpha=1 / period, adjust=False).mean()
    return pd.DataFrame({"adx": adx_s, "plus_di": plus_di, "minus_di": minus_di})


def compute_standard_indicators(df: pd.DataFrame, config: Optional[Dict[str, Any]] = None) -> pd.DataFrame:
    """Compute the standard indicator pack for an OHLC DataFrame."""
    cfg = config or {}
    out = df.copy()

    for p in cfg.get("ema_periods", [9, 20, 50, 100, 200]):
        out[f"ema_{p}"] = ema(df["close"], p)
    out["sma_50"] = sma(df["close"], 50)

    adx_df = adx(df["high"], df["low"], df["close"], cfg.get("adx_period", 14))
    out = out.join(adx_df)

    out["rsi"] = rsi(df["close"], cfg.get("rsi_period", 14))
    out = out.join(macd(df["close"], cfg.get("macd_fast", 12), cfg.get("macd_slow", 26), cfg.get("macd_signal", 9)))
    out = out.join(stochastic(df["high"], df["low"], df["close"]))
    out["cci"] = cci(df["high"], df["low"], df["close"])

    out["atr"] = atr(df["high"], df["low"], df["close"], cfg.get("atr_period", 14))
    out = out.join(bollinger_bands(df["close"], cfg.get("bb_period", 20), cfg.get("bb_std", 2.0)))
    out["stdev"] = standard_deviation(df["close"], 20)

    for name, s in market_structure(df["high"], df["low"], df["close"],
                                    length=cfg.get("swing_length", 2)).items():
        out[f"struct_{name}"] = s.astype("int8")
    return out
