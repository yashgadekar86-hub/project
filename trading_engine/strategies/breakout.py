"""Strategy 2 — Breakout: prev high/low + volatility expansion + volume confirmation."""
from __future__ import annotations

from typing import Any, Dict

import pandas as pd

from trading_engine.strategies.base import BaseStrategy, SignalOpinion, register_strategy


@register_strategy
class BreakoutStrategy(BaseStrategy):
    name = "breakout"
    display_name = "Breakout"
    description = "Break of recent high/low with ATR/volatility expansion and close confirmation."
    default_params = {
        "tf": "M15",
        "lookback": 20,
        "sl_atr_mult": 1.2,
        "tp_atr_mult": 2.5,
        "atr_expansion_min": 1.1,
    }

    def evaluate(self, symbol, mtf_data, symbol_spec, market):
        p = self.params
        df = mtf_data.get(p["tf"])
        if df is None or len(df) < p["lookback"] + 5:
            return SignalOpinion(self.name, None, 0, reasons=["Insufficient data"], timeframe=p["tf"])

        for col in ("atr",):
            if col not in df.columns:
                from trading_engine.indicators.technical import atr
                df["atr"] = atr(df["high"], df["low"], df["close"])

        recent = df.iloc[-p["lookback"] - 1:-1]
        last = df.iloc[-1]
        prev_high = float(recent["high"].max())
        prev_low = float(recent["low"].min())
        atr_val = float(last["atr"]) if pd.notna(last["atr"]) else 0.0
        atr_prev = float(df["atr"].iloc[-p["lookback"] - 5:-5].mean()) if len(df) >= p["lookback"] + 10 else atr_val
        close = float(last["close"])
        bid = market.get("bid", close)
        ask = market.get("ask", close)

        direction = None
        confidence = 0
        reasons = []
        warnings = []
        sl = tp = rr = None

        # Vol expansion
        vol_expansion = atr_val >= atr_prev * p["atr_expansion_min"]

        if close > prev_high and float(last["close"]) > float(last["open"]):
            direction = "BUY"
            entry = ask
            confidence = 50
            reasons.append(f"Bullish breakout above {p['lookback']}-bar high ({prev_high:.5f})")
            if vol_expansion:
                confidence += 15
                reasons.append("ATR expanding — volatility confirms breakout")
            else:
                warnings.append("No volatility expansion — breakout may fail")
            # Don't chase if already far above breakout level
            if close > prev_high + atr_val * 0.5:
                warnings.append("Price already extended above breakout — poor RR")
                confidence -= 10
            sl = entry - p["sl_atr_mult"] * atr_val
            tp = entry + p["tp_atr_mult"] * atr_val
        elif close < prev_low and float(last["close"]) < float(last["open"]):
            direction = "SELL"
            entry = bid
            confidence = 50
            reasons.append(f"Bearish breakout below {p['lookback']}-bar low ({prev_low:.5f})")
            if vol_expansion:
                confidence += 15
                reasons.append("ATR expanding — volatility confirms breakout")
            else:
                warnings.append("No volatility expansion — breakout may fail")
            if close < prev_low - atr_val * 0.5:
                warnings.append("Price already extended below breakout — poor RR")
                confidence -= 10
            sl = entry + p["sl_atr_mult"] * atr_val
            tp = entry - p["tp_atr_mult"] * atr_val

        if direction is None:
            return SignalOpinion(self.name, None, 0, reasons=["No breakout confirmed"],
                                 market_regime="RANGE", timeframe=p["tf"])

        if sl and tp and abs(entry - sl) > 0:
            rr = round(abs(tp - entry) / abs(entry - sl), 2)
            if rr >= 2:
                confidence += 10

        return SignalOpinion(
            self.name, direction, max(0, min(100, confidence)),
            entry=entry, stop_loss=sl, take_profit=tp, risk_reward=rr,
            reasons=reasons, warnings=warnings,
            market_regime="BREAKOUT" if direction else "RANGE",
            timeframe=p["tf"],
            metadata={"prev_high": prev_high, "prev_low": prev_low, "atr": atr_val},
        )
