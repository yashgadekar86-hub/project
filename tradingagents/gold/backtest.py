"""Deterministic backtesting + walk-forward split (Phases 28, 29).

**Look-ahead protection** is structural, not conventional: at decision time
``T`` the strategy receives candle frames sliced to bars whose CLOSE time is
``<= T`` (:func:`slice_to`), so a future bar can never enter a decision —
tested explicitly. Fundamentals use the existing FRED point-in-time pinning.

The backtest runs the DETERMINISTIC engine only (no LLM calls): the default
strategy proposes a candidate from multi-timeframe alignment and the engine
applies every gate (RR, spread, volatility, session, sizing). LLM-in-the-loop
historical evaluation remains possible via the upstream ``--date`` flow with
its point-in-time data layer, at API cost; this module exists to produce
honest, reproducible statistics without keys.

Metrics cover the full Phase 28 list, plus BUY/SELL, session, monthly and
news-day breakdowns.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import pandas as pd

from .config import GoldConfig
from .models import (
    AccountContext,
    Bias,
    BrokerSymbolSpec,
    DataProvenance,
    MarketContext,
    MultiTimeframeView,
)
from .news_blackout import NewsBlackoutCalendar
from .paper import PaperBroker
from .risk_engine import DeterministicRiskEngine
from .sessions import active_sessions
from .timeframes import build_multi_timeframe_view

logger = logging.getLogger(__name__)

# Decision cadence: one decision per closed H1 candle by default.
DECISION_TF = "H1"


def slice_to(df: pd.DataFrame, when: datetime) -> pd.DataFrame:
    """Candles whose bar close time is at or before ``when``.

    A bar labelled ``t`` (open time) closes at ``t + timeframe``; the caller
    passes the bar-close boundary so the last bar is complete. This is the
    look-ahead boundary — nothing after it is visible.
    """
    if df is None or len(df) == 0:
        return df
    idx = pd.DatetimeIndex(df.index)
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    return df[(idx <= pd.Timestamp(when))]


@dataclass
class BacktestContext:
    """Everything the strategy callback may see at time T (and nothing more)."""
    when: datetime
    mtf: MultiTimeframeView
    market: MarketContext
    spec: BrokerSymbolSpec
    account: AccountContext


StrategyFn = Callable[[BacktestContext], str]


def default_strategy(ctx: BacktestContext) -> str:
    """Deterministic strategy: trade with the multi-timeframe alignment."""
    if not ctx.mtf.usable:
        return "HOLD"
    if ctx.mtf.aligned_direction == Bias.BULLISH:
        return "BUY"
    if ctx.mtf.aligned_direction == Bias.BEARISH:
        return "SELL"
    return "HOLD"


@dataclass
class BacktestMetrics:
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    expectancy: float = 0.0
    average_r: float = 0.0
    total_return_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    sharpe: float | None = None
    largest_win: float = 0.0
    largest_loss: float = 0.0
    consecutive_wins: int = 0
    consecutive_losses: int = 0
    average_holding_hours: float = 0.0
    final_equity: float = 0.0
    by_side: dict[str, dict] = field(default_factory=dict)
    by_session: dict[str, dict] = field(default_factory=dict)
    by_month: dict[str, dict] = field(default_factory=dict)
    by_news_day: dict[str, dict] = field(default_factory=dict)

    def summary(self) -> str:
        lines = [
            f"trades={self.total_trades} win_rate={self.win_rate:.1f}% "
            f"profit_factor={self.profit_factor:.2f} expectancy={self.expectancy:.2f} "
            f"avg_R={self.average_r:.2f}",
            f"return={self.total_return_pct:.2f}% max_dd={self.max_drawdown_pct:.2f}% "
            f"sharpe={'n/a' if self.sharpe is None else f'{self.sharpe:.2f}'}",
            f"streaks: {self.consecutive_wins}W / {self.consecutive_losses}L | "
            f"avg hold {self.average_holding_hours:.1f}h",
        ]
        return "\n".join(lines)


def _streak(values: list[bool]) -> tuple[int, int]:
    max_w = max_l = cur_w = cur_l = 0
    for v in values:
        if v:
            cur_w += 1
            cur_l = 0
        else:
            cur_l += 1
            cur_w = 0
        max_w = max(max_w, cur_w)
        max_l = max(max_l, cur_l)
    return max_w, max_l


def compute_metrics(trades, equity_curve: list[tuple[datetime, float]],
                    starting_balance: float,
                    calendar: NewsBlackoutCalendar | None = None) -> BacktestMetrics:
    m = BacktestMetrics(final_equity=starting_balance)

    # Equity-curve metrics are computable even with zero trades.
    final_eq = equity_curve[-1][1] if equity_curve else starting_balance
    m.final_equity = round(final_eq, 2)
    m.total_return_pct = round((final_eq / starting_balance - 1.0) * 100.0, 3)
    peak = starting_balance
    max_dd = 0.0
    for _, eq in equity_curve:
        peak = max(peak, eq)
        if peak > 0:
            max_dd = max(max_dd, (peak - eq) / peak * 100.0)
    m.max_drawdown_pct = round(max_dd, 3)

    m.total_trades = len(trades)
    if not trades:
        return m

    pnls = [t.pnl for t in trades]
    rs = [t.r_multiple for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    m.wins, m.losses = len(wins), len(losses)
    m.win_rate = round(100.0 * m.wins / m.total_trades, 2)
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    m.profit_factor = round(gross_win / gross_loss, 3) if gross_loss > 0 else (
        float("inf") if gross_win > 0 else 0.0
    )
    m.expectancy = round(sum(pnls) / m.total_trades, 2)
    m.average_r = round(sum(rs) / len(rs), 3)
    m.largest_win = round(max(pnls), 2)
    m.largest_loss = round(min(pnls), 2)
    m.consecutive_wins, m.consecutive_losses = _streak([p > 0 for p in pnls])
    m.average_holding_hours = round(
        sum(t.holding_hours for t in trades) / m.total_trades, 2
    )

    # Per-trade Sharpe (annualisation-free; documented as per-trade).
    if len(pnls) >= 2:
        mean = sum(pnls) / len(pnls)
        var = sum((p - mean) ** 2 for p in pnls) / (len(pnls) - 1)
        std = var ** 0.5
        if std > 0:
            m.sharpe = round(mean / std, 3)

    def bucket(trades_subset) -> dict:
        if not trades_subset:
            return {"n": 0}
        p = [t.pnl for t in trades_subset]
        w = [x for x in p if x > 0]
        return {
            "n": len(p),
            "win_rate": round(100.0 * len(w) / len(p), 1),
            "pnl": round(sum(p), 2),
        }

    m.by_side = {
        side: bucket([t for t in trades if t.side == side])
        for side in ("BUY", "SELL")
    }
    m.by_session = {}
    for t in trades:
        for s in active_sessions(t.opened_at) or ["OffHours"]:
            m.by_session.setdefault(s, {"n": 0, "pnl": 0.0})
            m.by_session[s]["n"] += 1
            m.by_session[s]["pnl"] = round(m.by_session[s]["pnl"] + t.pnl, 2)
    m.by_month = {}
    for t in trades:
        key = t.opened_at.strftime("%Y-%m")
        m.by_month.setdefault(key, {"n": 0, "pnl": 0.0})
        m.by_month[key]["n"] += 1
        m.by_month[key]["pnl"] = round(m.by_month[key]["pnl"] + t.pnl, 2)
    if calendar is not None:
        m.by_news_day = {}
        for t in trades:
            is_news = any(
                abs((e.timestamp - t.opened_at).total_seconds()) <= 24 * 3600
                for e in calendar.events
            )
            key = "news_day" if is_news else "normal_day"
            m.by_news_day.setdefault(key, {"n": 0, "pnl": 0.0})
            m.by_news_day[key]["n"] += 1
            m.by_news_day[key]["pnl"] = round(m.by_news_day[key]["pnl"] + t.pnl, 2)
    return m


class Backtester:
    """Event-driven backtest of the deterministic gold strategy."""

    def __init__(self, config: GoldConfig, spec: BrokerSymbolSpec,
                 strategy: StrategyFn | None = None):
        self.config = config
        self.spec = spec
        self.strategy = strategy or default_strategy

    def run(
        self,
        candles_by_tf: dict[str, pd.DataFrame],
        starting_balance: float | None = None,
        warmup_bars: int = 220,
    ) -> tuple[PaperBroker, BacktestMetrics]:
        cfg = self.config
        broker = PaperBroker(
            spec=self.spec,
            starting_balance=starting_balance or cfg.paper_starting_balance,
            commission_per_lot=cfg.paper_commission_per_lot,
            slippage_points=cfg.paper_slippage_points,
        )
        base = candles_by_tf.get(DECISION_TF)
        if base is None or len(base) < warmup_bars + 2:
            raise ValueError(
                f"backtest needs >= {warmup_bars + 2} {DECISION_TF} candles, "
                f"got {0 if base is None else len(base)}"
            )
        calendar = NewsBlackoutCalendar.build(
            datetime.now(timezone.utc),
            cfg.news_blackout_before_min, cfg.news_blackout_after_min,
        )

        frames = {tf: df for tf, df in candles_by_tf.items() if df is not None}
        provenance = DataProvenance(source="BACKTEST", symbol=cfg.symbol)

        bar_minutes = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60,
                       "H4": 240, "D1": 1440}

        for i in range(warmup_bars, len(base)):
            bar_time = base.index[i]
            if not isinstance(bar_time, pd.Timestamp):
                continue
            ts = bar_time.to_pydatetime()
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            close_time = ts + timedelta(minutes=bar_minutes[DECISION_TF])

            # 1) manage the open position on THIS closed candle first
            broker.on_candle(base.iloc[i], now=close_time)
            close_px = float(base.iloc[i]["close"])
            broker.mark(close_time, close_px)
            if broker.position is not None:
                continue  # one position at a time

            # 2) assemble the T-visible world (LOOK-AHEAD BOUNDARY)
            visible = {tf: slice_to(df, close_time) for tf, df in frames.items()}
            mtf = build_multi_timeframe_view(visible, cfg.min_candles, provenance)
            h1_close = close_px
            spread_price = (cfg.max_spread_points * 0.5) * self.spec.point
            market = MarketContext(
                bid=round(h1_close - spread_price / 2, self.spec.digits),
                ask=round(h1_close + spread_price / 2, self.spec.digits),
                spread_points=cfg.max_spread_points * 0.5,
            )
            account = AccountContext(
                equity=broker.equity(close_px),
                balance=broker.balance,
                open_positions=1 if broker.position else 0,
                trades_today=sum(
                    1 for t in broker.trades
                    if t.closed_at.date() == close_time.date()
                ),
                consecutive_losses=0,
            )
            ctx = BacktestContext(
                when=close_time, mtf=mtf, market=market,
                spec=self.spec, account=account,
            )

            # 3) deterministic strategy + engine
            candidate = self.strategy(ctx)
            if candidate not in ("BUY", "SELL"):
                continue
            engine = DeterministicRiskEngine(cfg, now=close_time)
            decision = engine.evaluate(
                rating=("Buy" if candidate == "BUY" else "Sell"),
                mtf=mtf, market=market, spec=self.spec, account=account,
                calendar=calendar,
            )
            if not decision.tradeable or decision.plan is None:
                continue
            plan = decision.plan
            broker.open(
                side=plan.side, entry=plan.entry, stop_loss=plan.stop_loss,
                take_profits=plan.take_profits, volume=plan.volume,
                now=close_time, risk_amount=plan.risk_amount or 0.0,
                signal_id=plan.signal_id,
                ask=market.ask, bid=market.bid,
            )

        # Close any position still open at the end, at the last close.
        if broker.position is not None and len(base):
            last_time = base.index[-1]
            ts = last_time.to_pydatetime() if isinstance(last_time, pd.Timestamp) else None
            close_time = (ts + timedelta(minutes=bar_minutes[DECISION_TF])) if ts else None
            broker.close_at(float(base.iloc[-1]["close"]),
                            close_time or datetime.now(timezone.utc), "EOD")

        metrics = compute_metrics(
            broker.trades, broker.equity_curve,
            starting_balance or cfg.paper_starting_balance, calendar,
        )
        return broker, metrics


def walk_forward_split(n: int, in_sample_frac: float = 0.7) -> tuple[range, range]:
    """Chronological in-sample / out-of-sample index split (Phase 29)."""
    if not 0.1 < in_sample_frac < 0.9:
        raise ValueError("in_sample_frac must be between 0.1 and 0.9")
    cut = int(n * in_sample_frac)
    if cut < 1 or n - cut < 1:
        raise ValueError("not enough data to split")
    return range(0, cut), range(cut, n)
