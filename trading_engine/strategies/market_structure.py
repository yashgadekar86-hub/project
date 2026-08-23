"""Strategy 5 — Market Structure: BOS + CHoCH + retest of broken level."""
from __future__ import annotations

import pandas as pd

from trading_engine.strategies.base import BaseStrategy, SignalOpinion, register_strategy


@register_strategy
class MarketStructureStrategy(BaseStrategy):
    name = "market_structure"
    display_name = "Market Structure (BOS/CHoCH)"
    description = "Trade break-of-structure or change-of-character after a retest of the broken level."
    default_params = {
        "htf": "H4",
        "ltf": "M15",
        "sl_buffer_atr": 0.5,
        "tp_atr_mult": 2.5,
        "retest_tolerance_pct": 0.15,
    }

    def _last_swing(self, df: pd.DataFrame, kind: str):
        col = f"struct_swing_{kind}"
        hits = df.index[df[col] == 1].tolist()
        if not hits:
            return None, None
        idx = hits[-1]
        px = df.loc[idx, "high"] if kind == "high" else df.loc[idx, "low"]
        return idx, float(px)

    def evaluate(self, symbol, mtf_data, symbol_spec, market):
        p = self.params
        htf = mtf_data.get(p["htf"])
        ltf = mtf_data.get(p["ltf"])
        if htf is None or ltf is None or len(htf) < 50 or len(ltf) < 50:
            return SignalOpinion(self.name, None, 0, reasons=["Insufficient data"], timeframe=p["ltf"])

        # Ensure structure columns exist
        for df in (htf, ltf):
            need = ("struct_swing_high", "struct_swing_low", "struct_bos_up", "struct_bos_down", "struct_choch")
            if not all(c in df.columns for c in need):
                from trading_engine.indicators.technical import market_structure
                struct = market_structure(df["high"], df["low"], df["close"])
                for name, s in struct.items():
                    df[f"struct_{name}"] = s.astype("int8")
            if "atr" not in df.columns:
                from trading_engine.indicators.technical import atr
                df["atr"] = atr(df["high"], df["low"], df["close"])

        # Detect recent BOS on HTF
        htf_bos_up = htf.index[htf["struct_bos_up"] == 1].tolist()
        htf_bos_dn = htf.index[htf["struct_bos_down"] == 1].tolist()
        choch_idx = htf.index[htf["struct_choch"] == 1].tolist()

        direction = None
        confidence = 0
        reasons = []
        warnings = []
        sl = tp = rr = None

        bid = market.get("bid", float(ltf["close"].iloc[-1]))
        ask = market.get("ask", float(ltf["close"].iloc[-1]))
        atr_ltf = float(ltf["atr"].iloc[-1])
        entry = ask

        last_htf_close = float(htf["close"].iloc[-1])

        if htf_bos_up and (not htf_bos_dn or htf_bos_up[-1] > htf_bos_dn[-1]):
            # Bullish structure; look for retest of the last broken swing high (now support)
            _, broken_level = self._last_swing(htf.iloc[:htf_bos_up[-1] + 1], "high")
            if broken_level is None:
                return SignalOpinion(self.name, None, 0,
                                     reasons=["Bullish BOS detected but no swing high reference"],
                                     timeframe=p["ltf"])
            tol = broken_level * p["retest_tolerance_pct"] / 100.0
            retest_recent = (float(ltf["low"].iloc[-5:].min()) <= broken_level + tol)
            if retest_recent and bid >= broken_level - tol:
                direction = "BUY"
                entry = ask
                confidence = 65
                reasons.append(f"H4 bullish BOS — broken swing high {broken_level:.5f} acting as support")
                reasons.append("M15 retested the level and held")
                sl = broken_level - p["sl_buffer_atr"] * atr_ltf
                tp = entry + p["tp_atr_mult"] * atr_ltf
                if choch_idx and choch_idx[-1] == htf_bos_up[-1]:
                    confidence += 10
                    reasons.append("CHoCH confirmed structural shift")
            else:
                reasons.append("No retest yet after bullish BOS")

        elif htf_bos_dn and (not htf_bos_up or htf_bos_dn[-1] > htf_bos_up[-1]):
            _, broken_level = self._last_swing(htf.iloc[:htf_bos_dn[-1] + 1], "low")
            if broken_level is None:
                return SignalOpinion(self.name, None, 0,
                                     reasons=["Bearish BOS detected but no swing low reference"],
                                     timeframe=p["ltf"])
            tol = abs(broken_level) * p["retest_tolerance_pct"] / 100.0
            retest_recent = float(ltf["high"].iloc[-5:].max()) >= broken_level - tol
            if retest_recent and ask <= broken_level + tol:
                direction = "SELL"
                entry = bid
                confidence = 65
                reasons.append(f"H4 bearish BOS — broken swing low {broken_level:.5f} acting as resistance")
                reasons.append("M15 retested the level and held")
                sl = broken_level + p["sl_buffer_atr"] * atr_ltf
                tp = entry - p["tp_atr_mult"] * atr_ltf
                if choch_idx and choch_idx[-1] == htf_bos_dn[-1]:
                    confidence += 10
                    reasons.append("CHoCH confirmed structural shift")
            else:
                reasons.append("No retest yet after bearish BOS")
        else:
            reasons.append("No clear BOS on higher timeframe")

        if direction is None:
            return SignalOpinion(self.name, None, 0, reasons=reasons,
                                 market_regime="RANGE", timeframe=p["ltf"])

        if sl and tp and abs(entry - sl) > 0:
            rr = round(abs(tp - entry) / abs(entry - sl), 2)
            if rr >= 2:
                confidence += 10
        if last_htf_close and direction == "BUY" and last_htf_close < broken_level:
            warnings.append("HTF close below broken level — structure invalidated")
            confidence -= 20

        return SignalOpinion(
            self.name, direction, max(0, min(100, confidence)),
            entry=entry, stop_loss=sl, take_profit=tp, risk_reward=rr,
            reasons=reasons, warnings=warnings,
            market_regime="BREAKOUT", timeframe=p["ltf"],
        )
