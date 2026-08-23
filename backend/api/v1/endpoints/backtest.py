"""Backtesting + walk-forward endpoints."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import get_current_user
from backend.api.schemas import BacktestOut, BacktestRequest
from backend.core.state import STATE
from backend.database.session import get_db
from backend.models.user import User
from backend.services.market_data import fetch_candles
from backtesting.engine.backtester import BacktestConfig, run_backtest
from trading_engine.risk.position_sizer import SymbolSpec
from trading_engine.strategies.base import get_strategy

router = APIRouter(prefix="/backtest", tags=["Backtesting"])


def _spec(symbol: str) -> SymbolSpec:
    s = STATE.symbol_specs.get(symbol)
    if s is not None:
        return s
    is_jpy = symbol.endswith("JPY")
    digits = 3 if is_jpy else 5
    point = 10 ** (-digits)
    return SymbolSpec(
        name=symbol, digits=digits, point=point, pip_size=point*10,
        contract_size=100000, lot_min=0.01, lot_max=100, lot_step=0.01,
        tick_size=point, tick_value=1.0,
        currency_base=symbol[:3], currency_profit=symbol[3:6], currency_margin=symbol[:3],
        spread=0, spread_float=True, stops_level=20, freeze_level=0,
        trade_mode=0, trade_exemode=0, trade_calc_mode=0, swap_mode=0,
        swap_long=0, swap_short=0, visible=True,
    )


@router.post("", response_model=BacktestOut)
async def backtest(req: BacktestRequest, user: User = Depends(get_current_user),
                   db: AsyncSession = Depends(get_db)):
    conn = STATE.mt5_connectors.get(f"user:{user.id}")
    if conn is None or not getattr(conn, "connected", False):
        raise HTTPException(status_code=400, detail="Connect MT5 first to fetch historical data")
    # Fetch enough candles to span start→end (approx by count)
    count = 5000
    df = await fetch_candles(conn, req.symbol, timeframe=req.timeframe, count=count)
    if df is None or len(df) < 200:
        raise HTTPException(status_code=400, detail="Not enough historical data retrieved")
    # Filter by requested dates
    df = df[(df.index >= pd.Timestamp(req.start_date)) & (df.index <= pd.Timestamp(req.end_date))]
    if len(df) < 100:
        raise HTTPException(status_code=400, detail="Requested date range yields too few bars")

    try:
        strategy_cls = get_strategy(req.strategy)
    except KeyError:
        raise HTTPException(status_code=400, detail=f"Unknown strategy: {req.strategy}")
    strategy = strategy_cls(req.params or {})

    config = BacktestConfig(
        initial_balance=req.initial_balance,
        commission_per_lot_per_side=req.commission_per_lot,
        spread_pips=req.spread_pips,
        slippage_pips=req.slippage_pips,
        risk_per_trade_pct=req.risk_per_trade_pct,
    )

    def _run():
        return run_backtest(
            symbol=req.symbol, ohlc=df, spec=_spec(req.symbol),
            strategy=strategy, config=config,
        )
    import asyncio
    result = await asyncio.to_thread(_run)

    trades_out = []
    for t in result.trades[-500:]:  # cap payload
        trades_out.append({
            "symbol": t.symbol, "direction": t.direction, "volume": t.volume,
            "entry_time": t.entry_time, "entry_price": t.entry_price,
            "exit_time": t.exit_time, "exit_price": t.exit_price,
            "pnl": t.pnl, "pips": t.pips, "net_pnl": t.net_pnl,
            "commission": t.commission, "exit_reason": t.exit_reason,
            "r_multiple": t.r_multiple, "strategy": t.strategy,
            "confidence": t.confidence,
        })
    # Monthly returns
    eq = pd.DataFrame(result.equity_curve).set_index("time") if result.equity_curve else pd.DataFrame()
    monthly = {}
    if not eq.empty:
        eq.index = pd.to_datetime(eq.index)
        m = eq["equity"].resample("M").last().pct_change().dropna()
        monthly = {k.strftime("%Y-%m"): float(v) for k, v in m.items()}

    return BacktestOut(
        net_profit=round(result.net_profit, 2),
        gross_profit=round(result.gross_profit, 2),
        gross_loss=round(result.gross_loss, 2),
        win_rate=round(result.win_rate * 100, 2),
        profit_factor=round(result.profit_factor, 2),
        expectancy=round(result.expectancy, 2),
        max_drawdown_pct=round(result.max_drawdown_pct, 2),
        sharpe_ratio=round(result.sharpe_ratio, 2),
        sortino_ratio=round(result.sortino_ratio, 2),
        avg_win=round(result.avg_win, 2),
        avg_loss=round(result.avg_loss, 2),
        largest_win=round(result.largest_win, 2),
        largest_loss=round(result.largest_loss, 2),
        consecutive_wins=result.consecutive_wins,
        consecutive_losses=result.consecutive_losses,
        total_trades=result.total_trades,
        equity_curve=[{"time": str(p["time"]), "equity": round(p["equity"],2)} for p in result.equity_curve[::max(1,len(result.equity_curve)//500)]],
        drawdown_curve=[{"time": str(p["time"]), "drawdown_pct": round(p["drawdown_pct"],2)} for p in result.drawdown_curve[::max(1,len(result.drawdown_curve)//500)]],
        trades=trades_out,
        monthly_returns=monthly,
    )
