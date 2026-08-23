"""Orders, positions, execution endpoints."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import List, Optional
import uuid as uuid_lib

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import get_current_user
from backend.api.schemas import (
    OrderExecuteRequest, OrderOut, OrderValidateRequest, PositionOut,
)
from backend.core.logging import log_trade_event
from backend.core.state import STATE, PaperPosition, get_state
from backend.database.session import get_db
from backend.models.market import Symbol as DBSymbol
from backend.models.trading import (
    AISignal, Direction as DBDirection, MarketRegime, Order as DBOrder,
    OrderState, OrderType, Position as DBPosition, PositionState, TradeMode,
)
from backend.models.user import TradingAccount, User, UserSettings
from trading_engine.execution.executor import (
    ExecutionRequest, execute_order, validate_order as exec_validate,
)
from trading_engine.risk.engine import (
    AccountState, MarketConditions, RiskSettings,
)
from trading_engine.risk.position_sizer import SymbolSpec

router = APIRouter(prefix="", tags=["Orders & Positions"])


def _load_spec(symbol_name: str) -> SymbolSpec:
    spec = STATE.symbol_specs.get(symbol_name)
    if spec is None:
        is_jpy = symbol_name.endswith("JPY")
        digits = 3 if is_jpy else 5
        point = 10 ** (-digits)
        spec = SymbolSpec(
            name=symbol_name, digits=digits, point=point, pip_size=point*10,
            contract_size=100000, lot_min=0.01, lot_max=100, lot_step=0.01,
            tick_size=point, tick_value=1.0,
            currency_base=symbol_name[:3], currency_profit=symbol_name[3:6], currency_margin=symbol_name[:3],
            spread=0, spread_float=True, stops_level=20, freeze_level=0,
            trade_mode=0, trade_exemode=0, trade_calc_mode=0, swap_mode=0,
            swap_long=0, swap_short=0, visible=True,
        )
    return spec


async def _load_risk_context(user: User, db: AsyncSession):
    settings = (await db.execute(select(UserSettings).where(UserSettings.user_id == user.id))).scalar_one_or_none()
    if settings is None:
        settings = UserSettings(user_id=user.id); db.add(settings); await db.commit()
    conn = STATE.mt5_connectors.get(f"user:{user.id}")
    balance = equity = margin_free = 10_000.0
    margin_level = None; currency = "USD"
    if conn and conn.connected:
        try:
            info = await asyncio.to_thread(conn.get_account_info)
            balance, equity, margin_free = info.balance, info.equity, info.margin_free
            margin_level = info.margin_level; currency = info.currency
        except Exception:
            pass
    else:
        # Paper defaults from persisted balance or fresh
        balance = STATE.paper_balance.get(str(user.id), 10_000.0)
        equity = balance
        margin_free = equity

    risk = RiskSettings(
        risk_per_trade_pct=settings.risk_per_trade_pct,
        max_daily_loss_pct=settings.max_daily_loss_pct,
        max_drawdown_pct=settings.max_drawdown_pct,
        max_simultaneous_trades=settings.max_simultaneous_trades,
        max_lot_size=settings.max_lot_size,
        min_risk_reward=settings.min_risk_reward,
        max_spread_pips_majors=settings.max_spread_pips_majors,
        max_slippage_pips=settings.max_slippage_pips,
        daily_trade_limit=settings.daily_trade_limit,
        max_consecutive_losses=settings.max_consecutive_losses,
        cooldown_after_losses_minutes=settings.cooldown_after_losses_minutes,
        min_ai_confidence=settings.min_ai_confidence,
        news_filter_enabled=settings.news_filter_enabled,
    )
    # Positions (MT5 live or paper)
    positions = list(STATE.paper_positions.get(str(user.id), []))
    if conn and conn.connected and settings.trading_mode == TradeMode.LIVE:
        try:
            mt_pos = await asyncio.to_thread(conn.get_positions)
            # (MT5 positions would be synced here; for now paper uses STATE)
        except Exception:
            pass
    open_count = len(positions)
    # Open P&L for paper
    open_pnl = 0.0
    for p in positions:
        tick = STATE.latest_ticks.get(p.symbol)
        if tick:
            diff = (tick["bid"] - p.entry_price) if p.direction == "BUY" else (p.entry_price - tick["ask"])
            pip_size = _load_spec(p.symbol).pip_size
            pip_val = _load_spec(p.symbol).tick_value * (pip_size / _load_spec(p.symbol).tick_size) if _load_spec(p.symbol).tick_size else 10
            open_pnl += diff / pip_size * pip_val * p.volume if pip_size else 0
    equity = balance + open_pnl

    acct = AccountState(
        balance=balance, equity=equity, margin_free=margin_free,
        margin_level=margin_level, currency=currency, open_trades_count=open_count,
        open_positions=[{"symbol": p.symbol, "direction": p.direction, "volume": p.volume} for p in positions],
        kill_switch_active=STATE.kill_switch_active or settings.kill_switch_active,
        mt5_connected=(conn is not None and getattr(conn, "connected", False)) or settings.trading_mode != TradeMode.LIVE,
        trading_enabled_by_user=not settings.kill_switch_active,
        peak_equity=max(equity, balance),
    )
    return risk, acct, settings


def _build_market(symbol: str, entry_dir: Optional[str] = None) -> MarketConditions:
    tick = STATE.latest_ticks.get(symbol)
    spec = _load_spec(symbol)
    if tick:
        bid, ask = tick["bid"], tick["ask"]
        spread_pips = tick.get("spread_pips", (ask - bid) / spec.pip_size if spec.pip_size else 1.0)
    else:
        bid = ask = 0; spread_pips = 999
    from trading_engine.utils.sessions import current_sessions
    return MarketConditions(
        symbol=symbol, bid=bid, ask=ask, spread_pips=spread_pips,
        market_open=True, data_fresh=tick is not None,
        session=current_sessions()[0] if current_sessions() else "Unknown",
        high_news_risk=False,
    )


@router.post("/orders/validate", response_model=OrderOut)
async def validate(req: OrderValidateRequest, user: User = Depends(get_current_user),
                   db: AsyncSession = Depends(get_db)):
    spec = _load_spec(req.symbol)
    risk, acct, usettings = await _load_risk_context(user, db)
    market = _build_market(req.symbol, req.direction)
    ereq = ExecutionRequest(
        symbol=req.symbol, direction=req.direction, entry=req.entry,
        stop_loss=req.stop_loss, take_profit=req.take_profit, symbol_spec=spec,
        ai_confidence=req.ai_confidence, strategy_name=None, trade_mode="paper",
    )
    result = exec_validate(ereq, risk, acct, market)
    return OrderOut(
        success=result.allowed, trade_mode="validate", ticket=None,
        execution_price=None, volume_lots=result.lot, risk_amount=result.risk_amount,
        estimated_loss_at_sl=result.estimated_loss_at_sl,
        estimated_profit_at_tp=result.estimated_profit_at_tp,
        risk_reward=result.risk_reward,
        validation_passed=[k for k, v in result.passed_checks.items() if v],
        failures=result.failures, warnings=result.warnings,
        rejection_reason=None if result.allowed else "; ".join(result.failures),
    )


@router.post("/orders/execute", response_model=OrderOut)
async def execute(req: OrderExecuteRequest, user: User = Depends(get_current_user),
                  db: AsyncSession = Depends(get_db)):
    if req.trade_mode == "live" and not req.confirm_live:
        raise HTTPException(status_code=400, detail="Live trades require confirm_live=true")
    spec = _load_spec(req.symbol)
    risk, acct, settings = await _load_risk_context(user, db)

    # Safety: if settings says PAPER, refuse live
    if req.trade_mode == "live" and settings.trading_mode != TradeMode.LIVE:
        raise HTTPException(status_code=400, detail="Account is not in LIVE mode. Enable explicitly in Settings.")
    if STATE.kill_switch_active:
        raise HTTPException(status_code=400, detail="Kill switch is active — trading halted")

    market = _build_market(req.symbol, req.direction)
    ereq = ExecutionRequest(
        symbol=req.symbol, direction=req.direction, entry=req.entry,
        stop_loss=req.stop_loss, take_profit=req.take_profit, symbol_spec=spec,
        ai_confidence=req.ai_confidence, strategy_name=req.strategy_name,
        trade_mode=req.trade_mode,
    )

    if req.trade_mode == "paper":
        result = execute_order(ereq, risk, acct, market, None, is_paper=True)
        if result.success:
            # Record paper position in STATE
            p = PaperPosition(
                ticket=result.ticket, symbol=req.symbol, direction=req.direction,
                volume=result.volume_lots, entry_price=result.execution_price,
                sl=req.stop_loss, tp=req.take_profit,
                open_time=datetime.now(timezone.utc), strategy=req.strategy_name,
                ai_confidence=req.ai_confidence,
            )
            STATE.paper_positions.setdefault(str(user.id), []).append(p)
            log_trade_event("order_fill", f"PAPER {req.direction} {req.symbol} filled",
                            symbol=req.symbol, ticket=result.ticket, user_id=str(user.id),
                            metadata={"lot": result.volume_lots, "price": result.execution_price})
        return OrderOut(
            success=result.success, trade_mode=result.trade_mode, ticket=result.ticket,
            execution_price=result.execution_price, volume_lots=result.volume_lots,
            risk_amount=result.risk_amount, estimated_loss_at_sl=result.estimated_loss_at_sl,
            estimated_profit_at_tp=result.estimated_profit_at_tp, risk_reward=result.risk_reward,
            validation_passed=[k for k, v in (result.validation.passed_checks if result.validation else {}).items() if v],
            failures=result.validation.failures if result.validation else [],
            warnings=result.warnings, rejection_reason=result.rejection_reason,
        )

    # Live path
    conn = STATE.mt5_connectors.get(f"user:{user.id}")
    if conn is None or not conn.connected:
        raise HTTPException(status_code=400, detail="MT5 not connected")
    result = execute_order(ereq, risk, acct, market, mt5=conn, is_paper=False)
    if result.success:
        log_trade_event("order_fill", f"LIVE {req.direction} {req.symbol} filled",
                        symbol=req.symbol, ticket=result.ticket, user_id=str(user.id),
                        metadata={"lot": result.volume_lots, "price": result.execution_price})
    return OrderOut(
        success=result.success, trade_mode=result.trade_mode, ticket=result.ticket,
        execution_price=result.execution_price, volume_lots=result.volume_lots,
        risk_amount=result.risk_amount, estimated_loss_at_sl=result.estimated_loss_at_sl,
        estimated_profit_at_tp=result.estimated_profit_at_tp, risk_reward=result.risk_reward,
        validation_passed=[k for k, v in (result.validation.passed_checks if result.validation else {}).items() if v],
        failures=result.validation.failures if result.validation else [],
        warnings=result.warnings, rejection_reason=result.rejection_reason,
    )


@router.post("/orders/close/{ticket}")
async def close_position(ticket: int, user: User = Depends(get_current_user),
                         db: AsyncSession = Depends(get_db)):
    # Paper close
    positions = STATE.paper_positions.get(str(user.id), [])
    for p in positions:
        if p.ticket == ticket:
            tick = STATE.latest_ticks.get(p.symbol)
            if not tick:
                raise HTTPException(status_code=400, detail=f"No tick for {p.symbol}")
            price = tick["bid"] if p.direction == "BUY" else tick["ask"]
            spec = _load_spec(p.symbol)
            diff = (price - p.entry_price) if p.direction == "BUY" else (p.entry_price - price)
            pips = diff / spec.pip_size if spec.pip_size else 0
            pip_val = spec.tick_value * (spec.pip_size / spec.tick_size) if spec.tick_size else 10
            pnl = pips * pip_val * p.volume
            STATE.paper_trades.setdefault(str(user.id), []).append({
                "ticket": p.ticket, "symbol": p.symbol, "direction": p.direction,
                "volume": p.volume, "entry": p.entry_price, "exit": price,
                "pnl": pnl, "closed_at": datetime.now(timezone.utc).isoformat(),
                "exit_reason": "manual",
            })
            STATE.paper_balance[str(user.id)] = STATE.paper_balance.get(str(user.id), 10_000.0) + pnl
            STATE.paper_positions[str(user.id)] = [x for x in positions if x.ticket != ticket]
            return {"success": True, "ticket": ticket, "exit_price": price, "pnl": pnl}
    conn = STATE.mt5_connectors.get(f"user:{user.id}")
    if conn:
        try:
            mt_positions = await asyncio.to_thread(conn.get_positions)
            target = next((m for m in mt_positions if m.ticket == ticket), None)
            if target is None:
                raise HTTPException(status_code=404, detail="Position ticket not found")
            from trading_engine.execution.executor import close_position as close_pos_fn
            res = await asyncio.to_thread(lambda: close_pos_fn(
                target.ticket, symbol=target.symbol, direction=target.direction,
                volume=target.volume, mt5=conn,
            ))
            return {"success": res["success"], "mt5": res}
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    raise HTTPException(status_code=404, detail="Position not found")


@router.get("/positions", response_model=List[PositionOut])
async def positions(user: User = Depends(get_current_user)):
    out = []
    for p in STATE.paper_positions.get(str(user.id), []):
        tick = STATE.latest_ticks.get(p.symbol)
        cur = None; pnl = 0.0; pips = 0.0; r = None
        if tick:
            cur = tick["bid"] if p.direction == "BUY" else tick["ask"]
            spec = _load_spec(p.symbol)
            diff = (cur - p.entry_price) if p.direction == "BUY" else (p.entry_price - cur)
            pips = diff / spec.pip_size if spec.pip_size else 0
            pip_val = spec.tick_value * (spec.pip_size / spec.tick_size) if spec.tick_size else 10
            pnl = pips * pip_val * p.volume
            risk_dist = abs(p.entry_price - p.sl) / spec.pip_size if p.sl and spec.pip_size else 0
            if risk_dist > 0:
                r = round(pips / risk_dist, 2)
        out.append(PositionOut(
            ticket=p.ticket, symbol=p.symbol, direction=p.direction, volume_lots=p.volume,
            entry_price=p.entry_price, current_price=cur, stop_loss=p.sl, take_profit=p.tp,
            pnl=round(pnl, 2), pnl_pips=round(pips, 1), r_multiple=r, commission=0, swap=0,
            state="OPEN", exit_reason=None, ai_confidence=p.ai_confidence,
            strategy_name=p.strategy, opened_at=p.open_time,
        ))
    conn = STATE.mt5_connectors.get(f"user:{user.id}")
    if conn and conn.connected:
        try:
            mt_positions = await asyncio.to_thread(conn.get_positions)
            for m in mt_positions:
                out.append(PositionOut(
                    ticket=m.ticket, symbol=m.symbol, direction=m.direction, volume_lots=m.volume,
                    entry_price=m.open_price, current_price=m.current_price, stop_loss=m.sl,
                    take_profit=m.tp, pnl=m.profit, pnl_pips=0, r_multiple=None,
                    commission=m.commission, swap=m.swap, state="OPEN", exit_reason=None,
                    ai_confidence=None, strategy_name=m.comment or None,
                    opened_at=m.open_time,
                ))
        except Exception:
            pass
    return out
