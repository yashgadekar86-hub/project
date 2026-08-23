"""Dashboard, analytics, trade journal endpoints."""
from __future__ import annotations

from typing import Any, Dict, List
import uuid as uuid_lib

from fastapi import APIRouter, Depends
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import get_current_user
from backend.api.schemas import DashboardOut
from backend.core.state import STATE
from backend.database.session import get_db
from backend.models.trading import Trade, TradeJournal
from backend.models.user import User, UserSettings

router = APIRouter(tags=["Dashboard & Analytics"])


@router.get("/dashboard", response_model=DashboardOut)
async def dashboard(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    settings = (await db.execute(select(UserSettings).where(UserSettings.user_id == user.id))).scalar_one_or_none()
    bal = STATE.paper_balance.get(str(user.id), 10_000.0)
    open_pnl = 0.0
    active_trades = len(STATE.paper_positions.get(str(user.id), []))
    for p in STATE.paper_positions.get(str(user.id), []):
        tick = STATE.latest_ticks.get(p.symbol)
        if tick:
            spec = STATE.symbol_specs.get(p.symbol)
            pip_size = spec.pip_size if spec else 1e-4
            tick_val = spec.tick_value if spec else 10
            pv_per_lot = tick_val * (pip_size / (spec.tick_size or pip_size)) if spec and spec.tick_size else 10
            diff = (tick["bid"] - p.entry_price) if p.direction == "BUY" else (p.entry_price - tick["ask"])
            open_pnl += (diff / pip_size) * pv_per_lot * p.volume if pip_size else 0
    equity = bal + open_pnl
    start_bal = STATE.paper_start_balance.get(str(user.id), 10_000.0)
    peak = max(start_bal, equity)
    dd_pct = (peak - equity) / peak * 100 if peak else 0

    # Closed trades (paper in-memory + DB live)
    paper_trades = STATE.paper_trades.get(str(user.id), [])
    today_pnl = 0.0
    wins = losses = 0
    gross_profit = gross_loss = 0.0
    confs = []
    for t in paper_trades:
        pnl = t.get("pnl", 0)
        if pnl > 0: wins += 1; gross_profit += pnl
        else: losses += 1; gross_loss += abs(pnl)
        today_pnl += pnl  # (paper trades don't all have dates; approx)
    total = wins + losses
    win_rate = (wins / total * 100) if total else 0.0
    pf = gross_profit / gross_loss if gross_loss > 0 else (float("inf") if gross_profit > 0 else 0.0)
    avg_conf = sum(confs) / len(confs) if confs else (
        (sum((s.get("confidence",0) for s in STATE.latest_signals.values())) / max(1,len(STATE.latest_signals)))
        if STATE.latest_signals else 0
    )
    risk_used_pct = min(100.0, (settings.risk_per_trade_pct * active_trades / max(0.01, settings.max_daily_loss_pct)) * 100) if settings else (0.5 * active_trades / 2 * 100)
    return DashboardOut(
        balance=round(bal,2), equity=round(equity,2), today_pnl=round(today_pnl,2),
        open_pnl=round(open_pnl,2), drawdown_pct=round(dd_pct,2),
        win_rate=round(win_rate,2), profit_factor=round(pf,2) if pf != float("inf") else 99.0,
        active_trades=active_trades, ai_confidence_avg=round(avg_conf,1),
        risk_used_pct=round(min(100.0, risk_used_pct),1),
    )


@router.get("/trades", response_model=List[Dict[str, Any]])
async def list_trades(user: User = Depends(get_current_user)):
    out = list(STATE.paper_trades.get(str(user.id), []))
    return out


@router.get("/trade-journal")
async def trade_journal(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
                         symbol: str | None = None, strategy: str | None = None,
                         limit: int = 200):
    q = select(TradeJournal).where(TradeJournal.user_id == user.id).order_by(TradeJournal.timestamp.desc()).limit(limit)
    rows = (await db.execute(q)).scalars().all()
    return [
        {
            "id": str(r.id), "timestamp": r.timestamp, "event_type": r.event_type,
            "symbol": r.symbol_name, "direction": r.direction.value if r.direction else None,
            "decision": r.decision, "confidence": r.confidence, "entry": r.entry,
            "sl": r.sl, "tp": r.tp, "lot": r.lot, "risk_amount": r.risk,
            "market_regime": r.market_regime.value if r.market_regime else None,
            "result": r.result, "pnl": r.pnl, "r_multiple": r.r_multiple,
            "exit_reason": r.exit_reason,
        } for r in rows
    ] + [
        {
            "timestamp": t["closed_at"], "event_type": "CLOSE",
            "symbol": t["symbol"], "direction": t["direction"],
            "lot": t["volume"], "entry": t["entry"], "exit": t["exit"],
            "pnl": t.get("pnl"), "result": "WIN" if t.get("pnl",0) > 0 else "LOSS",
            "exit_reason": t.get("exit_reason"),
        } for t in STATE.paper_trades.get(str(user.id), [])
    ]
