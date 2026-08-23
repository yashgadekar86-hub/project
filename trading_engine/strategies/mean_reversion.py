"""Strategy 4 — Mean Reversion: range detection + RSI extremes + Bollinger Bands."""
from __future__ import annotations

import pandas as pd

from trading_engine.strategies.base import BaseStrategy, SignalOpinion, register_strategy


@register_strategy
class MeanReversionStrategy(BaseStrategy):
    name = "mean_reversion"
    display_name = "Mean Reversion"
    description = "Range-bound markets: fade extremes at Bollinger Bands with RSI confirmation."
    default_params = {
        "tf": "M15",
        "bb_period": 20,
        "bb_std": 2.0,
        "rsi_ob": 70,
        "rsi_os": 30,
        "adx_max": 20,
        "sl_atr_mult": 1.0,
        "tp_target": "mid",  # mid / opposite_band
    }

    def evaluate(self, symbol, mtf_data, symbol_spec, market):
        p = self.params
        df = mtf_data.get(p["tf"])
        if df is None or len(df) < 40:
            return SignalOpinion(self.name, None, 0, reasons=["Insufficient data"], timeframe=p["tf"])

        for col, fn, args in [
            ("bb_upper", None, None),
            ("bb_lower", None, None),
            ("bb_mid", None, None),
        ]:
            if col not in df.columns:
                from trading_engine.indicators.technical import bollinger_bands, atr, rsi, adx
                df_inds = bollinger_bands(df["close"], p["bb_period"], p["bb_std"])
                for c in df_inds.columns:
                    df[c] = df_inds[c]
                df["atr"] = atr(df["high"], df["low"], df["close"])
                df["rsi"] = rsi(df["close"])
                adx_df = adx(df["high"], df["low"], df["close"])
                df["adx"] = adx_df["adx"]
                break

        last = df.iloc[-1]
        close = float(last["close"])
        bb_upper = float(last["bb_upper"])
        bb_lower = float(last["bb_lower"])
        bb_mid = float(last["bb_mid"])
        rsi_val = float(last["rsi"])
        adx_val = float(last["adx"])
        atr_val = float(last["atr"])
        bid = market.get("bid", close)
        ask = market.get("ask", close)

        if adx_val >= p["adx_max"]:
            return SignalOpinion(self.name, None, 0,
                                 reasons=[f"ADX {adx_val:.1f}>{p['adx_max']} — market is trending, not ranging"],
                                 market_regime="TRENDING", timeframe=p["tf"])

        # Band width as % of mid — very narrow bands → squeeze, wide bands → already moved
        bb_width = (bb_upper - bb_lower) / bb_mid if bb_mid > 0 else 0
        direction = None
        confidence = 0
        reasons = []
        warnings = []
        sl = tp = rr = None
        entry = None

        if close <= bb_lower and rsi_val <= p["rsi_os"]:
            direction = "BUY"
            entry = ask
            confidence = 60
            reasons.append(f"Price at lower BB ({bb_lower:.5f}), RSI={rsi_val:.0f} (oversold)")
            sl = entry - p["sl_atr_mult"] * atr_val
            tp = bb_mid if p["tp_target"] == "mid" else bb_upper
        elif close >= bb_upper and rsi_val >= p["rsi_ob"]:
            direction = "SELL"
            entry = bid
            confidence = 60
            reasons.append(f"Price at upper BB ({bb_upper:.5f}), RSI={rsi_val:.0f} (overbought)")
            sl = entry + p["sl_atr_mult"] * atr_val
            tp = bb_mid if p["tp_target"] == "mid" else bb_lower

        if direction is None:
            return SignalOpinion(self.name, None, 0, reasons=["No mean-reversion extreme"],
                                 market_regime="RANGE", timeframe=p["tf"])

        if bb_width > 0.005:
            warnings.append("Bollinger Band width is wide — reversal risk elevated")
            confidence -= 10

        if sl and tp and abs(entry - sl) > 0:
            rr = round(abs(tp - entry) / abs(entry - sl), 2)
            if rr >= 1.5:
                confidence += 10

        return SignalOpinion(
            self.name, direction, max(0, min(100, confidence)),
            entry=entry, stop_loss=sl, take_profit=tp, risk_reward=rr,
            reasons=reasons, warnings=warnings,
            market_regime="RANGE", timeframe=p["tf"],
        )
