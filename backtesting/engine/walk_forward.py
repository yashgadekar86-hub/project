"""
Walk-forward testing.

Repeatedly: train (or optimize) on in-sample window, then run on out-of-sample
window. Returns aggregated metrics + per-window breakdown so the user can see
whether performance degrades outside the training period.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Dict, List, Optional

import pandas as pd

from backtesting.engine.backtester import (
    BacktestConfig, BacktestResult, BTTrade, run_backtest,
)
from trading_engine.risk.position_sizer import SymbolSpec
from trading_engine.strategies.base import BaseStrategy


@dataclass
class WFWindow:
    train_start: pd.Timestamp
    train_end: pd.Timestamp
    test_start: pd.Timestamp
    test_end: pd.Timestamp
    result: Optional[BacktestResult] = None
    params: Dict[str, Any] = field(default_factory=dict)


@dataclass
class WFResult:
    windows: List[WFWindow]
    aggregate_net_pnl: float = 0.0
    aggregate_sharpe: float = 0.0
    aggregate_max_dd: float = 0.0
    out_of_sample_degradation: float = 0.0
    stable: bool = False
    total_trades: int = 0


def walk_forward(
    *,
    symbol: str,
    ohlc: pd.DataFrame,
    spec: SymbolSpec,
    strategy_factory,   # callable(params: dict) -> BaseStrategy
    base_params: Dict[str, Any],
    config: BacktestConfig,
    train_days: int = 180,
    test_days: int = 30,
    step_days: int = 30,
) -> WFResult:
    """
    Run walk-forward over `ohlc`.

    `strategy_factory(params)` must return a new strategy instance. This
    implementation uses a single default param set per window (i.e. no
    optimization), which is already a strong sanity check.
    """
    if not isinstance(ohlc.index, pd.DatetimeIndex):
        raise ValueError("ohlc must be indexed by DatetimeIndex")
    start = ohlc.index.min()
    end = ohlc.index.max()
    windows: List[WFWindow] = []

    train = timedelta(days=train_days)
    test = timedelta(days=test_days)
    step = timedelta(days=step_days)

    w_start = start
    while w_start + train + test <= end:
        windows.append(WFWindow(
            train_start=w_start,
            train_end=w_start + train,
            test_start=w_start + train,
            test_end=w_start + train + test,
            params=dict(base_params),
        ))
        w_start += step

    in_pnls: List[float] = []
    out_pnls: List[float] = []
    total_pnl = 0.0
    total_trades = 0
    max_dd = 0.0
    sharpes: List[float] = []

    for w in windows:
        # In-sample (train) — here we just run the strategy with base params.
        in_df = ohlc[(ohlc.index >= w.train_start) & (ohlc.index < w.train_end)]
        out_df = ohlc[(ohlc.index >= w.test_start) & (ohlc.index < w.test_end)]
        if len(in_df) < 200 or len(out_df) < 50:
            continue
        try:
            strat_in = strategy_factory(w.params)
            res_in = run_backtest(
                symbol=symbol, ohlc=in_df, spec=spec, strategy=strat_in,
                config=config,
            )
            in_pnls.append(res_in.net_profit)
        except Exception:
            in_pnls.append(0.0)

        try:
            strat_out = strategy_factory(w.params)
            res_out = run_backtest(
                symbol=symbol, ohlc=out_df, spec=spec, strategy=strat_out,
                config=config,
            )
            w.result = res_out
            out_pnls.append(res_out.net_profit)
            total_pnl += res_out.net_profit
            total_trades += res_out.total_trades
            max_dd = max(max_dd, res_out.max_drawdown_pct)
            if res_out.sharpe_ratio == res_out.sharpe_ratio:  # NaN-safe
                sharpes.append(res_out.sharpe_ratio)
        except Exception:
            out_pnls.append(0.0)

    # Degradation: if average out-of-sample P&L is materially worse than in-sample,
    # the strategy is likely overfit.
    avg_in = sum(in_pnls) / len(in_pnls) if in_pnls else 0.0
    avg_out = sum(out_pnls) / len(out_pnls) if out_pnls else 0.0
    degradation = 0.0
    if avg_in > 0:
        degradation = max(0.0, (avg_in - avg_out) / abs(avg_in))
    stable = avg_out > 0 and degradation < 0.5 and total_trades >= 30

    return WFResult(
        windows=windows,
        aggregate_net_pnl=total_pnl,
        aggregate_sharpe=sum(sharpes) / len(sharpes) if sharpes else 0.0,
        aggregate_max_dd=max_dd,
        out_of_sample_degradation=degradation,
        stable=stable,
        total_trades=total_trades,
    )
