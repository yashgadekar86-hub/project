"""
Trade Scoring Engine.

Combines multi-timeframe signal opinions into a 0–100 weighted score broken
down across trend, structure, momentum, volatility, entry quality, R:R,
spread/liquidity, and session. Default thresholds (configurable):

  < 60      → NO TRADE
  60 – 69   → Weak setup
  70 – 79   → Valid setup
  80 – 89   → Strong setup
  90 – 100  → Exceptional setup
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class ScoreBreakdown:
    trend_alignment: int = 0
    market_structure: int = 0
    momentum: int = 0
    volatility: int = 0
    entry_quality: int = 0
    risk_reward: int = 0
    spread_liquidity: int = 0
    session_quality: int = 0
    total: int = 0
    grade: str = "NO TRADE"


@dataclass
class ScoreWeights:
    trend_alignment: int = 20
    market_structure: int = 20
    momentum: int = 15
    volatility: int = 10
    entry_quality: int = 15
    risk_reward: int = 10
    spread_liquidity: int = 5
    session_quality: int = 5

    def total_weight(self) -> int:
        return (self.trend_alignment + self.market_structure + self.momentum
                + self.volatility + self.entry_quality + self.risk_reward
                + self.spread_liquidity + self.session_quality)


DEFAULT_THRESHOLDS = {
    "no_trade_below": 60,
    "weak_min": 60,
    "valid_min": 70,
    "strong_min": 80,
    "exceptional_min": 90,
}


def _clamp(v: float, lo: int = 0, hi: int = 100) -> int:
    return int(max(lo, min(hi, round(v))))


def score_signal(
    *,
    mtf_aligned: bool,
    higher_tf_trend_strength: float,   # 0-1 (e.g., adx-normalized)
    structure_bos: bool,
    structure_choch: bool,
    rsi_aligned: bool,
    macd_aligned: bool,
    stoch_aligned: bool,
    atr_healthy: bool,                 # enough volatility for RR, but not wild
    atr_ratio: float,                  # recent_atr / avg_atr
    entry_at_pullback: bool,
    entry_at_key_level: bool,
    risk_reward: float,
    spread_pips: float,
    max_spread_pips: float,
    session_quality: float,            # 0-1 (overlap = higher)
    weights: Optional[ScoreWeights] = None,
    thresholds: Optional[Dict[str, int]] = None,
) -> ScoreBreakdown:
    w = weights or ScoreWeights()
    th = {**DEFAULT_THRESHOLDS, **(thresholds or {})}

    # ---- Component sub-scores (0-100) ----
    trend_score = 0
    if mtf_aligned:
        trend_score += 60
    trend_score += higher_tf_trend_strength * 40

    structure_score = 0
    if structure_choch:
        structure_score += 50
    if structure_bos:
        structure_score += 50

    momentum_score = 0
    if rsi_aligned:
        momentum_score += 35
    if macd_aligned:
        momentum_score += 35
    if stoch_aligned:
        momentum_score += 30

    # Volatility: want ATR between 0.7x and 1.5x its recent avg; too low = no movement, too high = dangerous
    if atr_ratio < 0.6:
        vol_score = 30
    elif 0.7 <= atr_ratio <= 1.5 and atr_healthy:
        vol_score = 100
    elif 1.5 < atr_ratio <= 2.0:
        vol_score = 70
    elif atr_ratio > 3.0:
        vol_score = 25
    else:
        vol_score = 60

    entry_score = 0
    if entry_at_pullback:
        entry_score += 60
    if entry_at_key_level:
        entry_score += 40

    # Risk/reward score: RR 1 → 40, 2 → 80, 3+ → 100
    if risk_reward >= 3:
        rr_score = 100
    elif risk_reward >= 2:
        rr_score = 80
    elif risk_reward >= 1.5:
        rr_score = 60
    elif risk_reward >= 1:
        rr_score = 40
    else:
        rr_score = 10

    # Spread score: 0 spread → 100, at max → 40, above → 0
    if spread_pips <= 0:
        spread_score = 100
    elif spread_pips >= max_spread_pips * 1.5:
        spread_score = 0
    else:
        spread_score = _clamp(100 * (1 - spread_pips / (max_spread_pips * 1.5)))

    session_score = _clamp(session_quality * 100)

    # ---- Weighted total (scale weighted sum by max possible = 100 * total_weight / 100) ----
    components = {
        "trend_alignment": (trend_score, w.trend_alignment),
        "market_structure": (structure_score, w.market_structure),
        "momentum": (momentum_score, w.momentum),
        "volatility": (vol_score, w.volatility),
        "entry_quality": (entry_score, w.entry_quality),
        "risk_reward": (rr_score, w.risk_reward),
        "spread_liquidity": (spread_score, w.spread_liquidity),
        "session_quality": (session_score, w.session_quality),
    }
    total_weight = sum(weight for _, weight in components.values())
    weighted_sum = sum(score * weight for score, weight in components.values())
    total = _clamp(weighted_sum / total_weight) if total_weight else 0

    if total >= th["exceptional_min"]:
        grade = "EXCEPTIONAL"
    elif total >= th["strong_min"]:
        grade = "STRONG"
    elif total >= th["valid_min"]:
        grade = "VALID"
    elif total >= th["weak_min"]:
        grade = "WEAK"
    else:
        grade = "NO TRADE"

    return ScoreBreakdown(
        trend_alignment=_clamp(trend_score),
        market_structure=_clamp(structure_score),
        momentum=_clamp(momentum_score),
        volatility=_clamp(vol_score),
        entry_quality=_clamp(entry_score),
        risk_reward=_clamp(rr_score),
        spread_liquidity=_clamp(spread_score),
        session_quality=_clamp(session_score),
        total=total,
        grade=grade,
    )
