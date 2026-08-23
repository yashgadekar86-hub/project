"""AI Signals + analysis endpoints."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import get_current_user
from backend.api.schemas import AnalyzeRequest, SignalOut
from backend.core.state import STATE, get_state
from backend.database.session import get_db
from backend.models.user import User, UserSettings
from backend.services.market_data import fetch_candles
from ai_engine.inference.explainer import explain_signal
from ai_engine.inference.pipeline import analyze_symbol
from trading_engine.risk.position_sizer import SymbolSpec

router = APIRouter(prefix="/signals", tags=["AI Signals"])


def _state_symbol_spec(name: str) -> SymbolSpec:
    spec = STATE.symbol_specs.get(name)
    if spec is not None:
        return spec
    # Fallback generic FX spec
    is_jpy = name.endswith("JPY")
    digits = 3 if is_jpy else 5
    point = 10 ** (-digits)
    return SymbolSpec(
        name=name, digits=digits, point=point, pip_size=point * 10,
        contract_size=100000, lot_min=0.01, lot_max=100, lot_step=0.01,
        tick_size=point, tick_value=1.0 if name.startswith("USD") else (0.1 if is_jpy else 10.0),
        currency_base=name[:3], currency_profit=name[3:6], currency_margin=name[:3],
        spread=0, spread_float=True, stops_level=20, freeze_level=0,
        trade_mode=0, trade_exemode=0, trade_calc_mode=0, swap_mode=0,
        swap_long=0, swap_short=0, visible=True,
    )


def _build_market(symbol: str, bid: float, ask: float) -> Dict[str, Any]:
    tick = STATE.latest_ticks.get(symbol)
    from trading_engine.utils.sessions import current_sessions
    sessions = current_sessions()
    spread_pips = 0.0
    if ask and bid:
        spec = _state_symbol_spec(symbol)
        spread_pips = (ask - bid) / spec.pip_size if spec.pip_size else 0
    return {
        "symbol": symbol,
        "bid": bid,
        "ask": ask,
        "spread_pips": spread_pips or (tick.get("spread_pips", 1.0) if tick else 1.0),
        "avg_spread_pips": 1.5,
        "market_open": True,
        "data_fresh": True,
        "session": sessions[0] if sessions else "London",
        "high_news_risk": False,
        "volatility_pips": 0.0,
    }


@router.post("/analyze", response_model=List[SignalOut])
async def analyze(req: AnalyzeRequest, user: User = Depends(get_current_user),
                  db: AsyncSession = Depends(get_db)):
    from sqlalchemy import select
    settings = (await db.execute(select(UserSettings).where(UserSettings.user_id == user.id))).scalar_one_or_none()
    enabled_strategies = req.enabled_strategies or (settings.enabled_strategies if settings and settings.enabled_strategies else None)
    min_confidence = settings.min_ai_confidence if settings else 70
    min_rr = settings.min_risk_reward if settings else 2.0
    max_spread = settings.max_spread_pips_majors if settings else 3.0
    news_blocked = settings.news_filter_enabled if settings else False

    conn = STATE.mt5_connectors.get(f"user:{user.id}")
    if conn is None or not getattr(conn, "connected", False):
        raise HTTPException(status_code=400, detail="MT5 not connected. Connect via /api/v1/mt5/connect first.")

    results: List[SignalOut] = []
    for symbol in req.symbols:
        try:
            mtf: Dict[str, Any] = {}
            for tf in (req.timeframes or ["M15", "H1", "H4", "D1"]):
                df = await fetch_candles(conn, symbol, timeframe=tf, count=req.count)
                if df is not None and len(df) >= 60:
                    mtf[tf] = df
            if not mtf:
                continue
            tick = STATE.latest_ticks.get(symbol)
            if tick:
                bid, ask = tick["bid"], tick["ask"]
            else:
                def _t(s=symbol):
                    return conn.get_tick(s)
                t = await asyncio.to_thread(_t)
                bid, ask = t.bid, t.ask
            spec = _state_symbol_spec(symbol)
            market = _build_market(symbol, bid, ask)
            if news_blocked:
                market["high_news_risk"] = False  # no news feed configured — never claim coverage

            signal = analyze_symbol(
                symbol=symbol, mtf_candles=mtf, symbol_spec=spec, market=market,
                enabled_strategies=enabled_strategies, ml_model=None,
                min_confidence=min_confidence, min_rr=min_rr,
                max_spread_pips=max_spread, news_high_risk=market["high_news_risk"],
            )
            explanation = explain_signal(signal.to_dict())
            # Persist latest
            STATE.latest_signals[symbol] = signal.to_dict()
            await manager.broadcast(f"signal:{symbol}", __import__("backend.websocket.manager", fromlist=["WSMessage"]).WSMessage(type="signal", topic=f"signal:{symbol}", payload=signal.to_dict()))
            results.append(SignalOut(
                symbol=signal.symbol, direction=signal.direction, decision=signal.decision,
                confidence=signal.confidence, market_regime=signal.market_regime,
                entry=signal.entry, stop_loss=signal.stop_loss, take_profit=signal.take_profit,
                risk_reward=signal.risk_reward, reasoning=signal.reasoning, warnings=signal.warnings,
                trade_allowed=signal.trade_allowed, block_reasons=signal.block_reasons,
                scores=signal.scores, timeframe_alignment=signal.timeframe_alignment,
                indicators=signal.indicators_snapshot, strategy_name=signal.strategy_name,
                primary_timeframe=signal.primary_timeframe,
                explanation_why=explanation["why"], explanation_why_not=explanation["why_not"],
            ))
        except Exception as e:
            results.append(SignalOut(
                symbol=symbol, direction=None, decision="NO_TRADE", confidence=0,
                market_regime="UNKNOWN", entry=None, stop_loss=None, take_profit=None,
                risk_reward=None, reasoning=[f"Analysis error: {e}"], warnings=[],
                trade_allowed=False, block_reasons=["ANALYSIS_ERROR"], scores={},
                timeframe_alignment={}, indicators={}, strategy_name=None,
                primary_timeframe="", explanation_why=[], explanation_why_not=[str(e)],
            ))
    return results


@router.get("", response_model=List[SignalOut])
async def latest_signals(user: User = Depends(get_current_user)):
    out = []
    for sym, sig in STATE.latest_signals.items():
        exp = explain_signal(sig)
        out.append(SignalOut(
            symbol=sig["symbol"], direction=sig.get("direction"), decision=sig.get("decision","NO_TRADE"),
            confidence=sig.get("confidence",0), market_regime=sig.get("market_regime","UNKNOWN"),
            entry=sig.get("entry"), stop_loss=sig.get("stop_loss"), take_profit=sig.get("take_profit"),
            risk_reward=sig.get("risk_reward"), reasoning=sig.get("reasoning",[]), warnings=sig.get("warnings",[]),
            trade_allowed=sig.get("trade_allowed",False), block_reasons=sig.get("block_reasons",[]),
            scores=sig.get("scores",{}), timeframe_alignment=sig.get("timeframe_alignment",{}),
            indicators=sig.get("indicators_snapshot",{}), strategy_name=sig.get("strategy_name"),
            primary_timeframe=sig.get("primary_timeframe",""),
            explanation_why=exp["why"], explanation_why_not=exp["why_not"],
        ))
    return out
