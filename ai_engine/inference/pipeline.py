"""
AI Inference Pipeline.

End-to-end path:
  market data → features → (optional ML model) → strategies → regime → scoring
  → structured signal JSON.

LLM usage: ONLY for post-hoc explanation of the structured output — never for
price/SL/TP calculation. If no LLM is configured, reasons/warnings are built
deterministically from the structured data.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from ai_engine.models.baseline import BaseFXModel, ModelPrediction
from ai_engine.regime.regime import detect_regime
from trading_engine.indicators.technical import compute_standard_indicators
from trading_engine.risk.position_sizer import SymbolSpec
from trading_engine.risk.scoring import ScoreWeights, score_signal
from trading_engine.strategies.base import SignalOpinion, list_strategies
from trading_engine.strategies import (  # noqa: F401  (triggers registration)
    trend_following, breakout, pullback, mean_reversion, market_structure,
)
from trading_engine.utils.sessions import current_sessions


@dataclass
class AISignalOutput:
    symbol: str
    direction: Optional[str]    # BUY / SELL / None (NO_TRADE)
    decision: str              # BUY / SELL / NO_TRADE
    confidence: int
    market_regime: str
    entry: Optional[float]
    stop_loss: Optional[float]
    take_profit: Optional[float]
    risk_reward: Optional[float]
    reasoning: List[str]
    warnings: List[str]
    trade_allowed: bool
    block_reasons: List[str]
    scores: Dict[str, int]
    timeframe_alignment: Dict[str, str]
    indicators_snapshot: Dict[str, Any]
    strategy_name: Optional[str]
    primary_timeframe: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _to_ohlc(df: pd.DataFrame) -> pd.DataFrame:
    expected = ["open", "high", "low", "close"]
    missing = [c for c in expected if c not in df.columns]
    if missing:
        raise ValueError(f"OHLC missing columns: {missing}")
    return df.copy()


def _indicators_snapshot(dfs: Dict[str, pd.DataFrame]) -> Dict[str, Any]:
    snap: Dict[str, Any] = {}
    for tf, df in dfs.items():
        if df is None or len(df) == 0:
            continue
        last = df.iloc[-1]
        snap[tf] = {
            "close": float(last["close"]) if "close" in df else None,
            "rsi": float(last["rsi"]) if "rsi" in df and pd.notna(last.get("rsi")) else None,
            "adx": float(last["adx"]) if "adx" in df and pd.notna(last.get("adx")) else None,
            "atr": float(last["atr"]) if "atr" in df and pd.notna(last.get("atr")) else None,
            "ema_9": float(last["ema_9"]) if "ema_9" in df and pd.notna(last.get("ema_9")) else None,
            "ema_20": float(last["ema_20"]) if "ema_20" in df and pd.notna(last.get("ema_20")) else None,
            "ema_50": float(last["ema_50"]) if "ema_50" in df and pd.notna(last.get("ema_50")) else None,
            "bb_pct": float(last["bb_pct"]) if "bb_pct" in df and pd.notna(last.get("bb_pct")) else None,
        }
    return snap


def _eval_strategies(symbol: str, dfs: Dict[str, pd.DataFrame],
                     spec: SymbolSpec, market: Dict[str, Any],
                     enabled: Optional[List[str]] = None) -> List[SignalOpinion]:
    from trading_engine.strategies.base import STRATEGY_REGISTRY
    opinions: List[SignalOpinion] = []
    for name, cls in STRATEGY_REGISTRY.items():
        if enabled and name not in enabled:
            continue
        try:
            strat = cls()
            op = strat.evaluate(symbol, dfs, spec, market)
            opinions.append(op)
        except Exception as e:
            opinions.append(SignalOpinion(
                strategy_name=name, direction=None, confidence=0,
                reasons=[f"Strategy error: {e}"], warnings=["Strategy failed with exception"],
            ))
    return opinions


def _select_best_opinion(opinions: List[SignalOpinion]) -> Optional[SignalOpinion]:
    valid = [op for op in opinions if op.direction in ("BUY", "SELL") and op.confidence > 0]
    if not valid:
        return None
    valid.sort(key=lambda o: (o.confidence, o.risk_reward or 0), reverse=True)
    return valid[0]


def _timeframe_alignment(dfs: Dict[str, pd.DataFrame]) -> Dict[str, str]:
    """Determine bias per timeframe (bullish / bearish / neutral)."""
    align: Dict[str, str] = {}
    for tf, df in dfs.items():
        if df is None or len(df) < 50:
            align[tf] = "unknown"; continue
        d = compute_standard_indicators(df) if "ema_20" not in df.columns else df
        last = d.iloc[-1]
        try:
            ema9 = float(last["ema_9"]); ema20 = float(last["ema_20"]); ema50 = float(last["ema_50"])
            close = float(last["close"])
            adx_val = float(last["adx"]) if "adx" in d.columns and pd.notna(last.get("adx")) else 0
            if adx_val > 20 and ema9 > ema20 > ema50 and close > ema9:
                align[tf] = "bullish"
            elif adx_val > 20 and ema9 < ema20 < ema50 and close < ema9:
                align[tf] = "bearish"
            else:
                align[tf] = "neutral"
        except Exception:
            align[tf] = "neutral"
    return align


def _mtf_aligned(alignment: Dict[str, str], direction: str) -> bool:
    """Higher TFs must agree with direction; entry TF can be the trigger."""
    order = ["D1", "H4", "H1", "M30", "M15", "M5", "M1"]
    present = [tf for tf in order if tf in alignment]
    if not present:
        return False
    desired = "bullish" if direction == "BUY" else "bearish"
    # At least the two highest available TFs should align
    htf = present[:2]
    agree = sum(1 for tf in htf if alignment[tf] == desired)
    return agree >= max(1, len(htf) - 1)


def analyze_symbol(
    *,
    symbol: str,
    mtf_candles: Dict[str, pd.DataFrame],   # timeframe -> OHLC DataFrame
    symbol_spec: SymbolSpec,
    market: Dict[str, Any],                 # bid, ask, spread_pips, market_open, etc.
    enabled_strategies: Optional[List[str]] = None,
    ml_model: Optional[BaseFXModel] = None,
    score_weights: Optional[ScoreWeights] = None,
    min_confidence: int = 70,
    min_rr: float = 2.0,
    max_spread_pips: float = 3.0,
    news_high_risk: bool = False,
) -> AISignalOutput:
    """Run full analysis and return a structured AI signal (possibly NO_TRADE)."""
    # 1. Normalize + ensure indicators
    dfs: Dict[str, pd.DataFrame] = {}
    for tf, df in mtf_candles.items():
        if df is None or len(df) < 30:
            continue
        d = _to_ohlc(df)
        d = compute_standard_indicators(d)
        dfs[tf] = d

    if not dfs:
        return AISignalOutput(
            symbol=symbol, direction=None, decision="NO_TRADE", confidence=0,
            market_regime="UNKNOWN", entry=None, stop_loss=None, take_profit=None,
            risk_reward=None, reasoning=["No market data available"], warnings=[],
            trade_allowed=False, block_reasons=["NO_DATA"], scores={},
            timeframe_alignment={}, indicators_snapshot={},
            strategy_name=None, primary_timeframe="",
        )

    # 2. Regime detection (using primary TF)
    primary_tf = _select_primary_tf(dfs)
    regime = detect_regime(
        dfs[primary_tf],
        news_high_risk=news_high_risk,
        spread_pips=float(market.get("spread_pips", 0)),
        avg_spread_pips=max(1.0, float(market.get("avg_spread_pips", 1.0))),
    )

    # 3. Optional ML signal (informational; doesn't directly set prices)
    ml_pred: Optional[ModelPrediction] = None
    if ml_model is not None:
        try:
            from ai_engine.features.feature_engine import build_multi_timeframe_features
            feats = build_multi_timeframe_features(dfs, base_tf=primary_tf)
            feats = feats.drop(columns=[c for c in feats.columns if isinstance(feats[c].iloc[-1], (pd.Timestamp,))], errors="ignore")
            ml_pred = ml_model.predict(feats.tail(1))
        except Exception as e:
            ml_pred = None  # Fail safe — never let ML crash the pipeline

    # 4. Strategy opinions
    opinions = _eval_strategies(symbol, dfs, symbol_spec, market, enabled=enabled_strategies)
    best = _select_best_opinion(opinions)

    # 5. NO-TRADE gating (spread, market open, news, regime)
    block_reasons: List[str] = []
    if not market.get("market_open", True):
        block_reasons.append("MARKET_CLOSED")
    if market.get("spread_pips", 0) > max_spread_pips:
        block_reasons.append("SPREAD_TOO_HIGH")
    if news_high_risk:
        block_reasons.append("NEWS_RISK")
    if regime["regime"] in ("ILLIQUID", "HIGH_VOLATILITY"):
        block_reasons.append(f"REGIME_{regime['regime']}")

    if best is None:
        return AISignalOutput(
            symbol=symbol, direction=None, decision="NO_TRADE",
            confidence=int(regime["confidence"]),
            market_regime=regime["regime"],
            entry=None, stop_loss=None, take_profit=None, risk_reward=None,
            reasoning=["No strategy produced a directional setup"] + [r for op in opinions for r in op.reasons[:2]],
            warnings=[w for op in opinions for w in op.warnings],
            trade_allowed=False,
            block_reasons=block_reasons + ["NO_SETUP"],
            scores={}, timeframe_alignment=_timeframe_alignment(dfs),
            indicators_snapshot=_indicators_snapshot(dfs),
            strategy_name=None, primary_timeframe=primary_tf,
        )

    # 6. MTF alignment scoring
    alignment = _timeframe_alignment(dfs)
    mtf_ok = _mtf_aligned(alignment, best.direction)

    # 7. Score components
    entry_tf = dfs.get(best.timeframe, dfs[primary_tf])
    last_entry = entry_tf.iloc[-1]
    rsi_val = float(last_entry.get("rsi", 50))
    macd_hist = float(last_entry.get("macd_hist", 0))
    stoch_k = float(last_entry.get("stoch_k", 50))
    atr_now = float(last_entry.get("atr", 0))
    atr_avg = float(entry_tf["atr"].tail(50).mean()) if "atr" in entry_tf.columns else atr_now
    atr_ratio = atr_now / atr_avg if atr_avg > 0 else 1.0
    rsi_aligned = (best.direction == "BUY" and 40 <= rsi_val <= 68) or (best.direction == "SELL" and 32 <= rsi_val <= 60)
    macd_aligned = (best.direction == "BUY" and macd_hist >= 0) or (best.direction == "SELL" and macd_hist <= 0)
    stoch_aligned = (best.direction == "BUY" and stoch_k < 70) or (best.direction == "SELL" and stoch_k > 30)
    atr_healthy = 0.7 <= atr_ratio <= 2.0
    entry_at_pullback = any("pullback" in r.lower() or "retest" in r.lower() for r in best.reasons)
    entry_at_key_level = any("ema" in r.lower() or "level" in r.lower() or "bb" in r.lower() for r in best.reasons)
    rr = best.risk_reward or 0
    sessions_now = current_sessions()
    session_q = 1.0 if ("London" in sessions_now or "NewYork" in sessions_now) else 0.6
    if "London" in sessions_now and "NewYork" in sessions_now:
        session_q = 1.0

    breakdown = score_signal(
        mtf_aligned=mtf_ok,
        higher_tf_trend_strength=min(1.0, float(last_entry.get("adx", 20)) / 50.0),
        structure_bos=any("bos" in r.lower() or "break of structure" in r.lower() for r in best.reasons),
        structure_choch=any("choch" in r.lower() or "change of character" in r.lower() for r in best.reasons),
        rsi_aligned=rsi_aligned,
        macd_aligned=macd_aligned,
        stoch_aligned=stoch_aligned,
        atr_healthy=atr_healthy,
        atr_ratio=atr_ratio,
        entry_at_pullback=entry_at_pullback,
        entry_at_key_level=entry_at_key_level,
        risk_reward=rr,
        spread_pips=float(market.get("spread_pips", 0)),
        max_spread_pips=max_spread_pips,
        session_quality=session_q,
        weights=score_weights,
    )

    # 8. ML overlay — if model strongly disagrees, downgrade confidence
    final_confidence = breakdown.total
    reasoning = list(best.reasons)
    warnings = list(best.warnings)
    if ml_pred is not None:
        desired = "BUY" if best.direction == "BUY" else "SELL"
        if ml_pred.label == desired and ml_pred.confidence > 55:
            final_confidence = min(100, int(final_confidence * 0.7 + ml_pred.confidence * 0.3))
            reasoning.append(f"ML model confirms {desired} (p={ml_pred.confidence}%)")
        elif ml_pred.label == "NO_TRADE" and ml_pred.confidence > 65:
            final_confidence = int(final_confidence * 0.6)
            warnings.append(f"ML model leans NO_TRADE ({ml_pred.confidence}%)")
        elif ml_pred.label != desired and ml_pred.confidence > 65:
            final_confidence = int(final_confidence * 0.5)
            warnings.append(f"ML model disagrees: predicts {ml_pred.label} ({ml_pred.confidence}%)")

    final_confidence = max(0, min(100, final_confidence))

    # 9. Decision gates
    decision = best.direction
    trade_allowed = True
    if final_confidence < min_confidence:
        block_reasons.append("LOW_CONFIDENCE")
        decision = None; trade_allowed = False
    if rr < min_rr:
        block_reasons.append("INSUFFICIENT_RR")
        decision = None; trade_allowed = False
    if block_reasons:
        decision = None; trade_allowed = False

    # 10. Regime/strategy alignment warnings
    if regime["regime"].startswith("STRONG") and best.strategy_name in ("mean_reversion",):
        warnings.append("Mean-reversion strategy in a strong trend — high risk")
    if regime["regime"] == "RANGE" and best.strategy_name in ("trend_following", "breakout"):
        warnings.append("Trend/breakout strategy firing in a range — expect lower win rate")

    reasoning.append(f"Market regime: {regime['regime']} ({regime['confidence']}%)")
    reasoning.append(f"Multi-timeframe alignment: {alignment}")

    return AISignalOutput(
        symbol=symbol,
        direction=best.direction if trade_allowed else None,
        decision=decision if decision else "NO_TRADE",
        confidence=final_confidence,
        market_regime=regime["regime"],
        entry=best.entry if trade_allowed else None,
        stop_loss=best.stop_loss if trade_allowed else None,
        take_profit=best.take_profit if trade_allowed else None,
        risk_reward=best.risk_reward if trade_allowed else None,
        reasoning=reasoning,
        warnings=warnings,
        trade_allowed=trade_allowed,
        block_reasons=block_reasons,
        scores={
            "trend": breakdown.trend_alignment,
            "momentum": breakdown.momentum,
            "structure": breakdown.market_structure,
            "volatility": breakdown.volatility,
            "entry": breakdown.entry_quality,
            "risk_reward": breakdown.risk_reward,
            "spread": breakdown.spread_liquidity,
            "session": breakdown.session_quality,
            "overall": breakdown.total,
            "grade": 0,
        },
        timeframe_alignment=alignment,
        indicators_snapshot=_indicators_snapshot(dfs),
        strategy_name=best.strategy_name,
        primary_timeframe=primary_tf,
    )


def _select_primary_tf(dfs: Dict[str, pd.DataFrame]) -> str:
    for pref in ("H1", "M15", "M30", "H4", "M5", "D1", "M1"):
        if pref in dfs:
            return pref
    return next(iter(dfs))
