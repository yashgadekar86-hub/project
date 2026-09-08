"""Multi-timeframe decision hierarchy for gold (Phase 3).

Roles (fixed, NOT equal-weight voting):

    D1  = macro trend
    H4  = major structure
    H1  = trading bias
    M15 = setup
    M5  = entry confirmation

Direction is only proposed when the higher timeframes (D1+H4) agree; lower
timeframes refine the setup and confirm entry. The output is both a
human-readable description ("Higher-timeframe bullish trend with a bearish
pullback and potential bullish entry confirmation") and an alignment score in
[0, 1] used by the confidence engine.
"""

from __future__ import annotations

import pandas as pd

from .indicators import compute_indicators, ema_stack_bullish
from .models import (
    Bias,
    DataProvenance,
    MultiTimeframeView,
    TechnicalSnapshot,
)
from .structure import analyze_structure

# Role of each timeframe in the hierarchy.
TF_ROLE = {
    "D1": "macro_trend",
    "H4": "major_structure",
    "H1": "bias",
    "M15": "setup",
    "M5": "entry_confirmation",
}

# Weight of each timeframe toward the alignment score. Higher timeframes
# dominate by design.
_TF_WEIGHT = {"D1": 0.35, "H4": 0.25, "H1": 0.20, "M15": 0.12, "M5": 0.08}


def _classify_bias(values: dict[str, float | None]) -> Bias:
    """Single-timeframe bias from EMA stack + momentum (deterministic)."""
    stack = ema_stack_bullish(values)
    close, ema50 = values.get("close"), values.get("ema_50")
    rsi, macd_hist = values.get("rsi"), values.get("macd_hist")

    bull_points = 0
    bear_points = 0
    if stack is True:
        bull_points += 2
    elif stack is False:
        bear_points += 2
    if close is not None and ema50 is not None:
        if close > ema50:
            bull_points += 1
        elif close < ema50:
            bear_points += 1
    if rsi is not None:
        if rsi >= 55:
            bull_points += 1
        elif rsi <= 45:
            bear_points += 1
    if macd_hist is not None:
        if macd_hist > 0:
            bull_points += 1
        elif macd_hist < 0:
            bear_points += 1

    if bull_points >= 3 and bear_points == 0:
        return Bias.BULLISH
    if bear_points >= 3 and bull_points == 0:
        return Bias.BEARISH
    return Bias.NEUTRAL


def analyze_timeframe(tf: str, df: pd.DataFrame, min_candles: int,
                      provenance: DataProvenance | None = None) -> TechnicalSnapshot:
    """Deterministic read of one timeframe; never raises on bad data."""
    try:
        if df is None or len(df) < min_candles:
            return TechnicalSnapshot(
                timeframe=tf, candles=0 if df is None else len(df),
                usable=False, reason="DATA_UNAVAILABLE: insufficient candles",
                provenance=provenance,
            )
        values, warmup_ok = compute_indicators(df)
        structure = analyze_structure(df)
        bias = _classify_bias(values)
        snap = TechnicalSnapshot(
            timeframe=tf, candles=len(df), indicators=values,
            structure=structure, bias=bias, usable=warmup_ok,
            reason="" if warmup_ok else "DATA_UNAVAILABLE: indicator warm-up",
            provenance=provenance,
        )
        # Structure trend refines bias when the indicator read is neutral.
        if bias == Bias.NEUTRAL and structure.trend != Bias.NEUTRAL:
            snap.bias = structure.trend
        return snap
    except (ValueError, KeyError) as exc:
        return TechnicalSnapshot(
            timeframe=tf, candles=0, usable=False,
            reason=f"DATA_UNAVAILABLE: {exc}", provenance=provenance,
        )


def _alignment_score(per_tf: dict[str, TechnicalSnapshot],
                     direction: Bias) -> float:
    """Weighted agreement with ``direction``; missing TFs score 0."""
    if direction == Bias.NEUTRAL:
        return 0.0
    total = 0.0
    for tf, weight in _TF_WEIGHT.items():
        snap = per_tf.get(tf)
        if snap is None or not snap.usable:
            continue
        if snap.bias == direction:
            total += weight
        elif snap.bias == Bias.NEUTRAL:
            total += weight * 0.25
    return round(total, 4)


def describe(view: MultiTimeframeView) -> str:
    """Human-readable hierarchy description (Phase 3 example format)."""
    bits: list[str] = []
    role_words = {
        "D1": "D1 macro trend", "H4": "H4 structure", "H1": "H1 bias",
        "M15": "M15 setup", "M5": "M5 entry confirmation",
    }
    for tf in ("D1", "H4", "H1", "M15", "M5"):
        snap = view.per_tf.get(tf)
        if snap is None:
            bits.append(f"{role_words[tf]}: NO DATA")
        elif not snap.usable:
            bits.append(f"{role_words[tf]}: DATA_UNAVAILABLE")
        else:
            bits.append(f"{role_words[tf]}: {snap.bias.value}")
    return "; ".join(bits)


def build_multi_timeframe_view(
    candles_by_tf: dict[str, pd.DataFrame],
    min_candles: int,
    provenance: DataProvenance | None = None,
) -> MultiTimeframeView:
    """Assemble the hierarchical view from per-timeframe candle frames."""
    view = MultiTimeframeView()
    for tf, df in candles_by_tf.items():
        view.per_tf[tf] = analyze_timeframe(tf, df, min_candles, provenance)

    def role_tf(role: str) -> str:
        for tf, r in TF_ROLE.items():
            if r == role:
                return tf
        return ""

    def bias_of(role: str) -> Bias:
        snap = view.per_tf.get(role_tf(role))
        if snap is None or not snap.usable:
            return Bias.NEUTRAL
        return snap.bias

    view.macro_trend = bias_of("macro_trend")
    view.major_structure = bias_of("major_structure")
    view.bias = bias_of("bias")
    view.setup = bias_of("setup")
    view.entry_confirmation = bias_of("entry_confirmation")

    # Direction requires D1 AND H4 agreement (the hierarchy rule).
    if view.macro_trend == view.major_structure and view.macro_trend != Bias.NEUTRAL:
        direction = view.macro_trend
    elif view.macro_trend != Bias.NEUTRAL and view.major_structure == Bias.NEUTRAL:
        direction = view.macro_trend  # HTF trend with no H4 contradiction
    elif view.major_structure != Bias.NEUTRAL and view.macro_trend == Bias.NEUTRAL:
        direction = view.major_structure
    else:
        direction = Bias.NEUTRAL

    # Conflicting H1 cancels the trade direction (it is the trading bias).
    if direction != Bias.NEUTRAL and view.bias != Bias.NEUTRAL and view.bias != direction:
        direction = Bias.NEUTRAL

    view.aligned_direction = direction
    view.alignment_score = _alignment_score(view.per_tf, direction)
    view.description = describe(view)

    # Usable only when at least D1 and H1 delivered data.
    d1 = view.per_tf.get("D1")
    h1 = view.per_tf.get("H1")
    view.usable = bool(
        d1 is not None and d1.usable and h1 is not None and h1.usable
    )
    return view
