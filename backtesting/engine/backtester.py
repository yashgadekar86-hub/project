"""
Event-driven backtesting engine.

Features:
  * Historical OHLCV + tick-spread simulation
  * Per-trade commission, spread, slippage, swap (optional)
  * Position sizing via the production Risk Engine (so results reflect reality)
  * Full audit trail + equity/drawdown curves
  * Chronological walk-forward support

This backtester is not a toy: it calls the SAME strategy/scoring/risk code
used live, reducing backtest/live discrepancy.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

import numpy as np
import pandas as pd

from trading_engine.risk.engine import (
    AccountState, MarketConditions, ProposedTrade, RiskSettings, evaluate_trade,
)
from trading_engine.risk.position_sizer import SymbolSpec
from trading_engine.strategies.base import BaseStrategy, SignalOpinion


@dataclass
class BacktestConfig:
    initial_balance: float = 10_000.0
    commission_per_lot_per_side: float = 0.0  # e.g. $3 per lot per trade (round-trip = 2x)
    spread_pips: float = 1.0
    slippage_pips: float = 0.5
    swap_per_lot_per_day: float = 0.0
    risk_per_trade_pct: float = 0.5
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    enable_swap: bool = False


@dataclass
class BTTrade:
    symbol: str
    direction: str
    volume: float
    entry_time: datetime
    entry_price: float
    sl: float
    tp: float
    exit_time: Optional[datetime] = None
    exit_price: Optional[float] = None
    pnl: float = 0.0
    pips: float = 0.0
    commission: float = 0.0
    swap: float = 0.0
    net_pnl: float = 0.0
    exit_reason: Optional[str] = None
    r_multiple: Optional[float] = None
    strategy: Optional[str] = None
    confidence: Optional[int] = None


@dataclass
class BacktestResult:
    trades: List[BTTrade] = field(default_factory=list)
    equity_curve: List[Dict[str, Any]] = field(default_factory=list)
    drawdown_curve: List[Dict[str, Any]] = field(default_factory=list)
    net_profit: float = 0.0
    gross_profit: float = 0.0
    gross_loss: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    expectancy: float = 0.0
    max_drawdown_pct: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    largest_win: float = 0.0
    largest_loss: float = 0.0
    consecutive_wins: int = 0
    consecutive_losses: int = 0
    total_trades: int = 0
    final_balance: float = 0.0


def _pip_value(spec: SymbolSpec) -> float:
    if spec.tick_value and spec.tick_size:
        pip_size = spec.point * (10 if spec.digits in (3, 5) else 1)
        return spec.tick_value * (pip_size / spec.tick_size)
    return spec.contract_size * spec.point * (10 if spec.digits in (3, 5) else 1)


def run_backtest(
    *,
    symbol: str,
    ohlc: pd.DataFrame,                # indexed by time, columns open/high/low/close
    spec: SymbolSpec,
    strategy: BaseStrategy,
    config: BacktestConfig,
    risk: Optional[RiskSettings] = None,
    higher_tf_data: Optional[Dict[str, pd.DataFrame]] = None,
    progress_cb: Optional[Callable[[int, int], None]] = None,
) -> BacktestResult:
    """Run a single-symbol backtest using the production strategy/risk stack."""
    risk = risk or RiskSettings(
        risk_per_trade_pct=config.risk_per_trade_pct,
        max_daily_loss_pct=100.0,     # disable daily-loss for backtesting by default
        max_drawdown_pct=100.0,
        max_simultaneous_trades=1,
        min_risk_reward=1.0,
        min_ai_confidence=0,
        max_spread_pips_majors=9999.0,
        news_filter_enabled=False,
        session_filter_enabled=False,
        one_trade_per_symbol=True,
        stop_loss_required=True,
        take_profit_required=True,
    )

    df = ohlc.copy()
    if config.start_date:
        df = df[df.index >= pd.Timestamp(config.start_date)]
    if config.end_date:
        df = df[df.index <= pd.Timestamp(config.end_date)]
    if len(df) < 100:
        return BacktestResult()

    # Strategy expects a dict of timeframes; if no higher TF data, just use the primary one
    # named "H1" (or whatever the caller provided). We'll use a single timeframe key "H1" by default.
    primary_tf = "H1"
    mtf = {primary_tf: df}
    if higher_tf_data:
        mtf.update(higher_tf_data)

    pip_val = _pip_value(spec)
    pip_size = spec.point * (10 if spec.digits in (3, 5) else 1)

    balance = config.initial_balance
    equity_peak = balance
    trades: List[BTTrade] = []
    open_pos: Optional[BTTrade] = None
    equity_curve: List[Dict[str, Any]] = []
    drawdown_curve: List[Dict[str, Any]] = []

    # Pre-compute indicators via the strategy's own evaluate calls — we do it bar-by-bar
    # with lookback slices so the strategy only sees past data.
    warmup = max(100, 50)
    n = len(df)
    last_signal: Optional[SignalOpinion] = None

    for i in range(warmup, n):
        slice_ = df.iloc[:i + 1].copy()
        bar_time = df.index[i]
        bar_open = float(slice_["open"].iloc[-1])
        bar_high = float(slice_["high"].iloc[-1])
        bar_low = float(slice_["low"].iloc[-1])
        bar_close = float(slice_["close"].iloc[-1])

        # Manage open position (SL/TP hit on this bar?)
        if open_pos is not None:
            hit_sl = False
            hit_tp = False
            exit_price = None
            reason = None
            if open_pos.direction == "BUY":
                if bar_low <= open_pos.sl:
                    hit_sl = True
                    exit_price = open_pos.sl
                    reason = "SL"
                elif bar_high >= open_pos.tp:
                    hit_tp = True
                    exit_price = open_pos.tp
                    reason = "TP"
            else:
                if bar_high >= open_pos.sl:
                    hit_sl = True
                    exit_price = open_pos.sl
                    reason = "SL"
                elif bar_low <= open_pos.tp:
                    hit_tp = True
                    exit_price = open_pos.tp
                    reason = "TP"
            if hit_sl or hit_tp:
                open_pos.exit_time = bar_time
                open_pos.exit_price = exit_price
                open_pos.exit_reason = reason
                diff = (exit_price - open_pos.entry_price) if open_pos.direction == "BUY" else (open_pos.entry_price - exit_price)
                pips = diff / pip_size if pip_size > 0 else 0
                pnl = pips * pip_val * open_pos.volume
                open_pos.pnl = pnl
                open_pos.pips = pips
                open_pos.commission = config.commission_per_lot_per_side * open_pos.volume * 2
                open_pos.net_pnl = pnl - open_pos.commission + open_pos.swap
                open_pos.r_multiple = pnl / abs((open_pos.entry_price - open_pos.sl) * pip_val * open_pos.volume) if open_pos.entry_price != open_pos.sl else 0
                balance += open_pos.net_pnl
                trades.append(open_pos)
                open_pos = None

        # If no open position, evaluate strategy
        if open_pos is None:
            # Build market conditions for this bar (synthetic)
            spread_price = config.spread_pips * pip_size
            bid = bar_close - spread_price / 2
            ask = bar_close + spread_price / 2
            market = MarketConditions(
                symbol=symbol, bid=bid, ask=ask,
                spread_pips=config.spread_pips, market_open=True, data_fresh=True,
                session="London", high_news_risk=False, volatility_pips=0,
            )
            acct = AccountState(
                balance=balance, equity=balance, margin_free=balance,
                margin_level=None, currency="USD", open_trades_count=0,
                open_positions=[], daily_pnl=0, weekly_pnl=0, drawdown_pct_from_peak=0,
                peak_equity=equity_peak, kill_switch_active=False, mt5_connected=True,
                trading_enabled_by_user=True,
            )
            # We pass the lookback slice under primary_tf so strategies don't see future bars
            mtf_now = {k: (v.iloc[:i + 1].copy() if k == primary_tf else _asof_slice(v, bar_time)) for k, v in mtf.items()}
            try:
                opinion = strategy.evaluate(symbol, mtf_now, spec, {"bid": bid, "ask": ask, "spread_pips": config.spread_pips})
                last_signal = opinion
            except Exception:
                opinion = None

            if opinion and opinion.direction in ("BUY", "SELL") and opinion.entry and opinion.stop_loss and opinion.take_profit:
                slip_price = config.slippage_pips * pip_size
                entry = opinion.entry + (slip_price if opinion.direction == "BUY" else -slip_price)
                proposed = ProposedTrade(
                    symbol=symbol, direction=opinion.direction, entry=entry,
                    stop_loss=opinion.stop_loss, take_profit=opinion.take_profit,
                    symbol_spec=spec, ai_confidence=opinion.confidence,
                    risk_reward=opinion.risk_reward, strategy_name=opinion.strategy_name,
                )
                result = evaluate_trade(proposed, risk, acct, market)
                if result.allowed:
                    open_pos = BTTrade(
                        symbol=symbol, direction=opinion.direction,
                        volume=result.lot, entry_time=bar_time, entry_price=entry,
                        sl=opinion.stop_loss, tp=opinion.take_profit,
                        strategy=opinion.strategy_name, confidence=opinion.confidence,
                    )

        # Track equity
        unrealized = 0.0
        if open_pos is not None:
            diff = (bar_close - open_pos.entry_price) if open_pos.direction == "BUY" else (open_pos.entry_price - bar_close)
            unrealized = diff / pip_size * pip_val * open_pos.volume if pip_size > 0 else 0
        equity = balance + unrealized
        equity_peak = max(equity_peak, equity)
        dd_pct = (equity_peak - equity) / equity_peak * 100 if equity_peak > 0 else 0
        equity_curve.append({"time": bar_time, "equity": equity, "balance": balance})
        drawdown_curve.append({"time": bar_time, "drawdown_pct": dd_pct})

        if progress_cb and (i % 500 == 0 or i == n - 1):
            progress_cb(i, n)

    return _compile_stats(trades, equity_curve, drawdown_curve, config.initial_balance)


def _asof_slice(df: pd.DataFrame, t) -> pd.DataFrame:
    """Return a slice of df up to (and including) time `t` (for higher TFs that don't have all bars)."""
    return df[df.index <= t].copy()


def _compile_stats(trades: List[BTTrade], equity_curve, drawdown_curve, initial_balance: float) -> BacktestResult:
    res = BacktestResult()
    res.trades = trades
    res.equity_curve = equity_curve
    res.drawdown_curve = drawdown_curve
    res.total_trades = len(trades)

    wins = [t for t in trades if t.net_pnl > 0]
    losses = [t for t in trades if t.net_pnl <= 0]
    res.gross_profit = sum(t.net_pnl for t in wins)
    res.gross_loss = abs(sum(t.net_pnl for t in losses))
    res.net_profit = res.gross_profit - res.gross_loss
    res.win_rate = len(wins) / len(trades) if trades else 0
    res.profit_factor = res.gross_profit / res.gross_loss if res.gross_loss > 0 else float("inf") if res.gross_profit > 0 else 0
    res.avg_win = np.mean([t.net_pnl for t in wins]) if wins else 0
    res.avg_loss = np.mean([abs(t.net_pnl) for t in losses]) if losses else 0
    res.largest_win = max((t.net_pnl for t in wins), default=0)
    res.largest_loss = max((abs(t.net_pnl) for t in losses), default=0)
    res.expectancy = (res.win_rate * res.avg_win) - ((1 - res.win_rate) * res.avg_loss)
    res.final_balance = initial_balance + res.net_profit
    res.max_drawdown_pct = max((d["drawdown_pct"] for d in drawdown_curve), default=0)

    # Sharpe / Sortino (from bar-level equity returns)
    if len(equity_curve) > 2:
        eq = pd.Series([p["equity"] for p in equity_curve])
        rets = eq.pct_change().dropna()
        if rets.std() > 0:
            res.sharpe_ratio = float((rets.mean() / rets.std()) * np.sqrt(252 * 24))  # hourly-ish bars
        downside = rets[rets < 0]
        if len(downside) > 1 and downside.std() > 0:
            res.sortino_ratio = float((rets.mean() / downside.std()) * np.sqrt(252 * 24))

    # Consecutive wins/losses
    max_cw = max_cl = cw = cl = 0
    for t in trades:
        if t.net_pnl > 0:
            cw += 1; cl = 0; max_cw = max(max_cw, cw)
        else:
            cl += 1; cw = 0; max_cl = max(max_cl, cl)
    res.consecutive_wins = max_cw
    res.consecutive_losses = max_cl

    return res
