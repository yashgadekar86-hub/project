"""Strategy 3 — Pullback: established trend + retracement to key EMA/pivot + momentum confirmation."""
from __future__ import annotations

import pandas as pd

from trading_engine.strategies.base import BaseStrategy, SignalOpinion, register_strategy


@register_strategy
class PullbackStrategy(BaseStrategy):
    name = "pullback"
    display_name = "Trend Pullback"
    description = "Wait for a shallow retracement in an established trend, enter on momentum re-ignition."
    default_params = {
        "trend_tf": "H4",
        "entry_tf": "M15",
        "adx_min": 22,
        "pullback_zone_ema": 20,
        "rsi_pullback_min": 40,
        "rsi_pullback_max": 60,
        "sl_atr_mult": 1.5,
        "tp_atr_mult": 3.0,
    }

    def evaluate(self, symbol, mtf_data, symbol_spec, market):
        p = self.params
        reasons, warnings = [], []
        trend_df = mtf_data.get(p["trend_tf"])
        entry_df = mtf_data.get(p["entry_tf"])
        if trend_df is None or entry_df is None or len(trend_df) < 60 or len(entry_df) < 40:
            return SignalOpinion(self.name, None, 0, reasons=["Insufficient data"], timeframe=p["entry_tf"])

        for col in ("ema_20", "ema_50"):
            if col not in trend_df.columns:
                from trading_engine.indicators.technical import ema
                period = int(col.split("_")[1])
                trend_df[col] = ema(trend_df["close"], period)
        if "adx" not in trend_df.columns:
            from trading_engine.indicators.technical import adx
            trend_df = trend_df.join(adx(trend_df["high"], trend_df["low"], trend_df["close"]))
        if "atr" not in entry_df.columns:
            from trading_engine.indicators.technical import atr
            entry_df["atr"] = atr(entry_df["high"], entry_df["low"], entry_df["close"])
        if "rsi" not in entry_df.columns:
            from trading_engine.indicators.technical import rsi
            entry_df["rsi"] = rsi(entry_df["close"])

        t = trend_df.iloc[-1]
        e = entry_df.iloc[-1]
        price = float(t["close"])
        ema20 = float(t["ema_20"])
        ema50 = float(t["ema_50"])
        adx_val = float(t["adx"]) if pd.notna(t["adx"]) else 0

        direction = None
        if adx_val < p["adx_min"]:
            return SignalOpinion(self.name, None, 0,
                                 reasons=[f"ADX {adx_val:.1f} below {p['adx_min']} — no strong trend"],
                                 timeframe=p["entry_tf"])
        bull_trend = ema20 > ema50 and price > ema50
        bear_trend = ema20 < ema50 and price < ema50

        entry_price = market.get("ask", float(e["close"]))
        bid = market.get("bid", float(e["close"]))
        atr_val = float(e["atr"]) if pd.notna(e.get("atr")) else price * 0.001
        rsi_val = float(e["rsi"]) if pd.notna(e.get("rsi")) else 50
        ema20_entry = float(entry_df["ema_20"].iloc[-1]) if "ema_20" in entry_df.columns else ema20
        confidence = 0

        if bull_trend:
            in_pullback = bid <= ema20_entry + atr_val * 0.3
            rsi_ok = p["rsi_pullback_min"] <= rsi_val <= p["rsi_pullback_max"] + 5
            if in_pullback and rsi_ok:
                direction = "BUY"
                confidence = 60
                reasons.append(f"H4 uptrend (EMA20>{ema50:.5f}, ADX={adx_val:.0f})")
                reasons.append(f"M15 pulled back to EMA20 zone (RSI={rsi_val:.0f})")
                if int(e.get("struct_hl", 0)) == 1:
                    confidence += 10
                    reasons.append("Higher Low structure formed on entry TF")
                sl = entry_price - p["sl_atr_mult"] * atr_val
                tp = entry_price + p["tp_atr_mult"] * atr_val
            else:
                reasons.append("No valid bull pullback to EMA20")
        elif bear_trend:
            in_pullback = entry_price >= ema20_entry - atr_val * 0.3
            rsi_ok = p["rsi_pullback_min"] - 5 <= rsi_val <= p["rsi_pullback_max"]
            if in_pullback and rsi_ok:
                direction = "SELL"
                confidence = 60
                reasons.append(f"H4 downtrend (EMA20<{ema50:.5f}, ADX={adx_val:.0f})")
                reasons.append(f"M15 pulled back to EMA20 zone (RSI={rsi_val:.0f})")
                if int(e.get("struct_lh", 0)) == 1:
                    confidence += 10
                    reasons.append("Lower High structure formed on entry TF")
                sl = entry_price + p["sl_atr_mult"] * atr_val
                tp = entry_price - p["tp_atr_mult"] * atr_val
            else:
                reasons.append("No valid bear pullback to EMA20")
        else:
            reasons.append("No established trend on higher timeframe")

        if direction is None:
            return SignalOpinion(self.name, None, 0, reasons=reasons, timeframe=p["entry_tf"])

        rr = None
        if sl and tp and abs(entry_price - sl) > 0:
            rr = round(abs(tp - entry_price) / abs(entry_price - sl), 2)
            if rr >= 2:
                confidence += 10

        return SignalOpinion(
            self.name, direction, max(0, min(100, confidence)),
            entry=entry_price if direction == "BUY" else bid,
            stop_loss=sl, take_profit=tp, risk_reward=rr,
            reasons=reasons, warnings=warnings,
            market_regime="STRONG_BULL_TREND" if direction == "BUY" else "STRONG_BEAR_TREND",
            timeframe=p["entry_tf"],
        )
