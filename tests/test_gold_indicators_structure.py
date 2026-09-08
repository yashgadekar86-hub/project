"""Phase 4 tests: deterministic indicators, warm-up safety, market structure."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from tests.gold_helpers import candles_from_path, range_path, trend_path
from tradingagents.gold.indicators import (
    atr,
    bollinger,
    compute_indicators,
    ema,
    ema_stack_bullish,
    macd,
    rsi,
    sma,
    stochastic,
    vwma,
)
from tradingagents.gold.models import Bias
from tradingagents.gold.structure import (
    analyze_structure,
    classify_trend,
    cluster_levels,
    detect_swings,
    support_resistance,
)


# --------------------------------------------------------------------- EMA/SMA
def test_ema_matches_manual_computation():
    close = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
    out = ema(close, 3)
    # manual: seed 1; alpha=0.5 -> 1, 1.5, 2.25, 3.125, 4.0625
    assert abs(out.iloc[0] - 1.0) < 1e-9
    assert abs(out.iloc[1] - 1.5) < 1e-9
    assert abs(out.iloc[2] - 2.25) < 1e-9
    assert abs(out.iloc[4] - 4.0625) < 1e-9


def test_sma_window_and_warmup():
    close = pd.Series(range(1, 31), dtype=float)
    out = sma(close, 5)
    assert pd.isna(out.iloc[3])
    assert abs(out.iloc[4] - 3.0) < 1e-9
    assert abs(out.iloc[-1] - 28.0) < 1e-9


# ------------------------------------------------------------------------- RSI
def test_rsi_all_gains_is_100():
    close = pd.Series(np.linspace(100, 200, 60))
    out = rsi(close)
    assert out.iloc[-1] == 100.0


def test_rsi_all_losses_is_0():
    close = pd.Series(np.linspace(200, 100, 60))
    out = rsi(close)
    assert out.iloc[-1] == 0.0


def test_rsi_flat_is_50():
    close = pd.Series([100.0] * 60)
    out = rsi(close)
    assert abs(out.iloc[-1] - 50.0) < 1e-9


def test_rsi_in_bounds_random_walk():
    rng = np.random.default_rng(42)
    close = pd.Series(100 + np.cumsum(rng.standard_normal(500)))
    out = rsi(close).dropna()
    assert (out >= 0).all() and (out <= 100).all()


# ------------------------------------------------------------------------ MACD
def test_macd_bullish_uptrend_positive():
    close = pd.Series(trend_path(300, drift=1.0, seed=1))
    macd_line, signal_line, hist = macd(close)
    assert macd_line.iloc[-1] > 0
    assert hist.iloc[-1] != 0


def test_macd_bearish_downtrend_negative():
    close = pd.Series(trend_path(300, drift=-1.0, seed=2))
    macd_line, _s, _h = macd(close)
    assert macd_line.iloc[-1] < 0


# ------------------------------------------------------------------- Stochastic
def test_stochastic_bounds_and_extremes():
    path = list(range(100, 130))  # monotonic up -> %K pinned high
    df = candles_from_path(path)
    k, d = stochastic(df["high"], df["low"], df["close"])
    assert k.iloc[-1] > 95
    path_down = list(range(130, 100, -1))
    df2 = candles_from_path(path_down)
    k2, _ = stochastic(df2["high"], df2["low"], df2["close"])
    assert k2.iloc[-1] < 5


# ------------------------------------------------------------------------- ATR
def test_atr_constant_range_is_constant():
    # Identical candles -> TR is the same every bar -> ATR equals that TR.
    idx = pd.date_range("2026-01-01", periods=80, freq="1h", tz="UTC")
    df = pd.DataFrame({
        "open": 100.0, "high": 102.0, "low": 98.0, "close": 100.0,
        "volume": 1.0,
    }, index=idx)
    out = atr(df["high"], df["low"], df["close"])
    assert out.iloc[-1] == pytest.approx(4.0, abs=1e-9)
    assert out.iloc[-1] == pytest.approx(out.iloc[-10], abs=1e-9)


# -------------------------------------------------------------------- Bollinger
def test_bollinger_mid_equals_sma_and_band_order():
    rng = np.random.default_rng(0)
    close = pd.Series(100 + rng.standard_normal(200).cumsum())
    mid, upper, lower = bollinger(close)
    assert mid.equals(sma(close, 20))
    assert (upper.dropna() > mid.dropna()).all()
    assert (lower.dropna() < mid.dropna()).all()


# ------------------------------------------------------------------------ VWMA
def test_vwma_equals_sma_when_volume_constant():
    rng = np.random.default_rng(1)
    close = pd.Series(100 + rng.standard_normal(100).cumsum())
    vol = pd.Series([50.0] * 100)
    pd.testing.assert_series_equal(
        vwma(close, vol, 20).dropna(), sma(close, 20).dropna(),
        check_freq=False,
    )


# ------------------------------------------------------- compute_indicators set
def test_compute_indicators_full_set_present():
    df = candles_from_path(trend_path(320, seed=4))
    values, warmup_ok = compute_indicators(df)
    assert warmup_ok
    for key in ("ema_10", "ema_20", "ema_50", "ema_100", "ema_200",
                "sma_20", "sma_50", "sma_200", "rsi", "macd", "macd_signal",
                "macd_hist", "stoch_k", "stoch_d", "atr", "atr_pct",
                "boll_mid", "boll_ub", "boll_lb", "vwma", "close"):
        assert key in values, key
        assert values[key] is not None, key


def test_compute_indicators_insufficient_warmup_reports_not_ok():
    df = candles_from_path(trend_path(40, seed=4))  # < EMA50 warmup
    values, warmup_ok = compute_indicators(df)
    assert not warmup_ok
    assert values["ema_50"] is None
    assert values["ema_10"] is not None  # short ones still computed


def test_indicators_are_deterministic():
    df = candles_from_path(trend_path(300, seed=9))
    v1, _ = compute_indicators(df)
    v2, _ = compute_indicators(df)
    assert v1 == v2


def test_no_indicator_value_is_fabricated_when_data_missing():
    df = candles_from_path([100.0, 101.0, 102.0])
    values, warmup_ok = compute_indicators(df)
    assert not warmup_ok
    # None means DATA_UNAVAILABLE, never a made-up number
    assert values["rsi"] is None
    assert values["atr"] is None


def test_ema_stack_directions():
    up = candles_from_path(trend_path(300, drift=1.5, seed=5))
    v_up, _ = compute_indicators(up)
    assert ema_stack_bullish(v_up) is True
    down = candles_from_path(trend_path(300, drift=-1.5, seed=6))
    v_down, _ = compute_indicators(down)
    assert ema_stack_bullish(v_down) is False
    short = candles_from_path(trend_path(20, seed=7))
    v_short, _ = compute_indicators(short)
    assert ema_stack_bullish(v_short) is None


# ------------------------------------------------------------------- structure
def test_detect_swings_finds_pivots_in_zigzag():
    # explicit zigzag: peaks at 110, troughs at 90
    path = []
    for _ in range(40):
        path += [100, 95, 90, 95, 100, 105, 110, 105]
    df = candles_from_path(path, wick=0.1)
    highs, lows = detect_swings(df, fractal_k=2)
    assert len(highs) > 3
    assert all(abs(s.price - 110.3) < 1.0 for s in highs)  # 110 + wick
    assert all(abs(s.price - 89.7) < 1.0 for s in lows)


def test_detect_swings_needs_confirmation_window():
    # A spike in the last 2 bars cannot be a confirmed swing: it has no
    # right-side confirmation window yet (look-ahead safe by construction).
    path = [100, 101, 102, 103, 104, 105, 130, 131]
    df = candles_from_path(path, wick=0.05)
    highs, _ = detect_swings(df, fractal_k=2)
    assert all(s.price < 110 for s in highs)
    # once the right-side bars exist, the same spike IS a confirmed swing
    path2 = [100, 101, 102, 103, 104, 105, 130, 131, 104, 103]
    df2 = candles_from_path(path2, wick=0.05)
    highs2, _ = detect_swings(df2, fractal_k=2)
    assert any(s.price > 125 for s in highs2)


def test_classify_trend_uptrend_downtrend_range():
    df = candles_from_path(trend_path(200, 100.0, drift=1.0, seed=31), wick=0.2)
    highs, lows = detect_swings(df, fractal_k=2)
    assert len(highs) >= 2 and len(lows) >= 2
    assert classify_trend(highs, lows) == Bias.BULLISH
    df2 = candles_from_path(trend_path(200, 300.0, drift=-1.0, seed=32), wick=0.2)
    highs2, lows2 = detect_swings(df2, fractal_k=2)
    assert classify_trend(highs2, lows2) == Bias.BEARISH


def test_classify_trend_neutral_when_insufficient_swings():
    assert classify_trend([], []) == Bias.NEUTRAL
    assert classify_trend([object()], [object()]) == Bias.NEUTRAL


def test_support_resistance_split_around_price():
    path = range_path(200, 100.0, 110.0)
    df = candles_from_path(path)
    highs, lows = detect_swings(df)
    sup, res = support_resistance(df, highs, lows, atr_value=1.0)
    price = float(df["close"].iloc[-1])
    assert all(s < price for s in sup)
    assert all(r > price for r in res)


def test_cluster_levels_merges_close_levels():
    assert cluster_levels([100.0, 100.4, 100.8, 110.0], tolerance=0.5) == \
        pytest.approx([100.4, 110.0])


def test_analyze_structure_bullish_uptrend_has_bos():
    df = candles_from_path(trend_path(300, drift=1.2, seed=13))
    ms = analyze_structure(df)
    assert ms.trend == Bias.BULLISH
    assert ms.atr is not None and ms.atr > 0
    assert ms.atr_pct is not None and ms.atr_pct > 0
    assert ms.last_event in ("BOS", "CHOCH", "UNKNOWN")


def test_analyze_structure_insufficient_data_is_empty_not_fake():
    df = candles_from_path([100.0, 101.0, 102.0])
    ms = analyze_structure(df)
    assert ms.trend == Bias.NEUTRAL
    assert ms.support == [] and ms.resistance == []
    assert "insufficient" in ms.note
