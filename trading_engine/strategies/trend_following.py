"""Strategy 1 — Trend Following: EMA alignment + ADX + structure."""
from __future__ import annotations

from typing import Any, Dict

import pandas as pd

from trading_engine.strategies.base import BaseStrategy, SignalOpinion, register_strategy


@register_strategy
class TrendFollowingStrategy(BaseStrategy):
    name = "trend_following"
    display_name = "Trend Following"
    description = "EMA alignment across multiple timeframes + ADX filter + structure confirmation."
    default_params = {
        "trend_tf": "H1",
        "entry_tf": "M15",
        "adx_min": 20,
        "adx_strong": 25,
        "rsi_ob": 70,
        "rsi_os": 30,
        "sl_atr_mult": 1.5,
        "tp_atr_mult": 3.0,
    }

    def evaluate(self, symbol, mtf_data, symbol_spec, market) -> SignalOpinion:
        p = self.params
        reasons = []
        warnings = []
        direction = None
        confidence = 0
        entry = sl = tp = None
        rr = None
        regime = "UNKNOWN"

        df_trend = mtf_data.get(p["trend_tf"])
        df_entry = mtf_data.get(p["entry_tf"])
        if df_trend is None or len(df_trend) < 50 or df_entry is None or len(df_entry) < 50:
            return SignalOpinion(
                strategy_name=self.name, direction=None, confidence=0,
                reasons=["Insufficient data for trend following"],
                warnings=["Not enough bars loaded"],
                timeframe=p["entry_tf"],
            )

        # Ensure EMAs exist
        for period in (9, 20, 50):
            if f"ema_{period}" not in df_trend.columns:
                from trading_engine.indicators.technical import ema
                df_trend[f"ema_{period}"] = ema(df_trend["close"], period)
        if "adx" not in df_trend.columns:
            from trading_engine.indicators.technical import adx
            df_trend = df_trend.join(adx(df_trend["high"], df_trend["low"], df_trend["close"]))
        if "atr" not in df_entry.columns:
            from trading_engine.indicators.technical import atr
            df_entry["atr"] = atr(df_entry["high"], df_entry["low"], df_entry["close"])

        last = df_trend.iloc[-1]
        price = float(last["close"])
        ema9, ema20, ema50 = float(last["ema_9"]), float(last["ema_20"]), float(last["ema_50"])
        adx_val = float(last["adx"]) if pd.notna(last["adx"]) else 0
        plus_di = float(last["plus_di"]) if "plus_di" in df_trend.columns and pd.notna(last.get("plus_di")) else 0
        minus_di = float(last["minus_di"]) if "minus_di" in df_trend.columns and pd.notna(last.get("minus_di")) else 0

        bull_align = ema9 > ema20 > ema50 and price > ema9
        bear_align = ema9 < ema20 < ema50 and price < ema9
        trend_strong = adx_val >= p["adx_min"]

        if bull_align and trend_strong and plus_di > minus_di:
            direction = "BUY"
            confidence += 30
            reasons.append(f"{p['trend_tf']} EMAs 9>20>50 bullish-aligned")
            reasons.append(f"ADX={adx_val:.1f} confirms trend (+DI > -DI)")
            regime = "STRONG_BULL_TREND" if adx_val >= p["adx_strong"] else "WEAK_BULL_TREND"
        elif bear_align and trend_strong and minus_di > plus_di:
            direction = "SELL"
            confidence += 30
            reasons.append(f"{p['trend_tf']} EMAs 9<20<50 bearish-aligned")
            reasons.append(f"ADX={adx_val:.1f} confirms trend (-DI > +DI)")
            regime = "STRONG_BEAR_TREND" if adx_val >= p["adx_strong"] else "WEAK_BEAR_TREND"

        if direction is None:
            return SignalOpinion(
                strategy_name=self.name, direction=None, confidence=0,
                reasons=["EMA alignment / ADX / DI criteria not met"],
                timeframe=p["entry_tf"],
            )

        # Entry TF pullback + momentum confirmation
        e = df_entry.iloc[-1]
        if "rsi" not in df_entry.columns:
            from trading_engine.indicators.technical import rsi
            df_entry["rsi"] = rsi(df_entry["close"])
        rsi_val = float(df_entry["rsi"].iloc[-1])
        atr_val = float(df_entry["atr"].iloc[-1]) if pd.notna(e.get("atr")) else price * 0.001

        bid = market.get("bid", price)
        ask = market.get("ask", price)
        entry_price = ask if direction == "BUY" else bid

        if direction == "BUY":
            # Pullback condition: price retests ema20 or ema9 and holds
            ema20_entry = float(df_entry["ema_20"].iloc[-1]) if "ema_20" in df_entry.columns else ema20
            in_pullback = entry_price <= ema9 * 1.001 + atr_val * 0.5 or entry_price <= ema20_entry * 1.002
            rsi_ok = 40 <= rsi_val <= 68
            if in_pullback:
                confidence += 15
                reasons.append(f"{p['entry_tf']} price near EMA pullback zone")
            else:
                reasons.append("No shallow pullback — chasing extended move")
                warnings.append("Entry not at ideal pullback level")
            if rsi_ok:
                confidence += 10
                reasons.append(f"RSI {rsi_val:.0f} supports continuation (not overbought)")
            else:
                reasons.append(f"RSI {rsi_val:.0f} is extreme")
                warnings.append("RSI at overbought levels for long")
            sl = entry_price - p["sl_atr_mult"] * atr_val
            tp = entry_price + p["tp_atr_mult"] * atr_val
        else:
            ema20_entry = float(df_entry["ema_20"].iloc[-1]) if "ema_20" in df_entry.columns else ema20
            in_pullback = entry_price >= ema9 * 0.999 - atr_val * 0.5 or entry_price >= ema20_entry * 0.998
            rsi_ok = 32 <= rsi_val <= 60
            if in_pullback:
                confidence += 15
                reasons.append(f"{p['entry_tf']} price near EMA pullback zone (short)")
            else:
                warnings.append("Entry not at ideal pullback level")
            if rsi_ok:
                confidence += 10
                reasons.append(f"RSI {rsi_val:.0f} supports continuation (not oversold)")
            else:
                warnings.append("RSI at oversold levels for short")
            sl = entry_price + p["sl_atr_mult"] * atr_val
            tp = entry_price - p["tp_atr_mult"] * atr_val

        # Structure add
        if direction == "BUY" and int(e.get("struct_bos_up", 0)) == 1:
            confidence += 15
            reasons.append("Recent bullish Break of Structure")
        elif direction == "SELL" and int(e.get("struct_bos_down", 0)) == 1:
            confidence += 15
            reasons.append("Recent bearish Break of Structure")

        # Risk/reward
        if sl and tp and abs(entry_price - sl) > 0:
            rr = round(abs(tp - entry_price) / abs(entry_price - sl), 2)
            if rr >= 2:
                confidence += 10
            else:
                confidence -= 5

        confidence = max(0, min(100, confidence))
        entry = entry_price

        return SignalOpinion(
            strategy_name=self.name,
            direction=direction,
            confidence=confidence,
            entry=entry,
            stop_loss=sl,
            take_profit=tp,
            risk_reward=rr,
            reasons=reasons,
            warnings=warnings,
            market_regime=regime,
            timeframe=p["entry_tf"],
        )
