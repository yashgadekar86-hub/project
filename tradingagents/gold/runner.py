"""Orchestration for the Gold/XAUUSD system (Phases 34-38).

One implementation of the full flow, shared by the ``gold_mt5.py`` argparse
facade and the ``tradingagents gold ...`` Typer subcommand:

    market data -> [LLM research (optional) ] -> DETERMINISTIC ENGINE
        -> BUY/SELL/HOLD -> paper / dry-run / live -> audit log -> state

Safety invariants enforced here:

* ``--analysis-only`` never connects to MT5.
* ``--dry-run`` may read account/symbol/price data but never submits an order.
* ``--live`` additionally requires ``MT5_DRY_RUN=false`` AND a final
  confirmation (interactive prompt, or ``GOLD_LIVE_CONFIRM=YES`` when
  unattended) — then the plan is re-validated one last time before sending.
* Every failure path degrades to HOLD with a reason; nothing fails open.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

import tradingagents.default_config as _default_config

from .config import GoldConfig
from .fundamentals import collect_fundamentals
from .marketdata import (
    broker_spec_from_symbol_info,
    fetch_mt5_timeframes,
    fetch_yf_timeframes,
    reference_spec,
)
from .models import (
    AccountContext,
    Bias,
    BrokerSymbolSpec,
    FinalDecision,
    MarketContext,
    MultiTimeframeView,
)
from .news import collect_news
from .news_blackout import NewsBlackoutCalendar
from .report import render_report
from .risk_engine import DeterministicRiskEngine
from .sessions import check_session
from .state import GoldStateStore

# Load .env BEFORE reading any MT5_*/GOLD_* variables in GoldConfig.from_env.
load_dotenv()

logger = logging.getLogger("tradingagents.gold.runner")


@dataclass
class RunOptions:
    """Everything the runner needs; built by argparse (gold_mt5.py) or Typer."""

    symbol: str = "XAUUSD"
    mt5_symbol: str | None = None
    date: str | None = None
    analysts: str = "market,news,fundamentals"
    with_sentiment: bool = False
    analysis_only: bool = False
    force_dry_run: bool = False
    live: bool = False
    paper: bool = False
    backtest: bool = False
    backtest_days: int = 400
    allow_short: bool = False
    close_on_hold: bool = False
    lots: float | None = None
    risk_percent: float | None = None
    min_rr: float | None = None
    min_confidence: float | None = None
    account_balance: float | None = None
    timeframe: str | None = None
    fast: bool = False
    no_llm: bool = False
    debug: bool = False
    quiet: bool = False
    reflect: float | None = None


@dataclass
class RunResult:
    ok: bool
    decision: FinalDecision | None = None
    rating: str | None = None
    report_path: str | None = None
    execution: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


# --------------------------------------------------------------------- config
def build_config(opts: RunOptions) -> GoldConfig:
    """Environment + CLI overrides. The live latch is set ONLY by --live."""
    cfg = GoldConfig.from_env(
        symbol=opts.mt5_symbol or None,
        # --dry-run forces simulation even when MT5_DRY_RUN=false is set;
        # --live alone can never open the gates without the env var too.
        dry_run=True if opts.force_dry_run else None,
        risk_percent=opts.risk_percent,
        min_rr=opts.min_rr,
        min_confidence=opts.min_confidence,
        allow_short=opts.allow_short,
        working_timeframe=opts.timeframe,
        fast_mode=opts.fast,
        live_enabled=opts.live,  # dry_run comes from env (MT5_DRY_RUN)
    )
    return cfg


# ------------------------------------------------------------------ LLM layer
def run_llm_analysis(opts: RunOptions, cfg: GoldConfig) -> tuple[str | None, dict]:
    """Run the multi-agent research pipeline. Returns (rating, final_state)."""
    from tradingagents.gold_config import build_gold_config
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    config = build_gold_config(_default_config.DEFAULT_CONFIG.copy())
    if opts.fast:
        config["max_debate_rounds"] = 1
        config["max_risk_rounds"] = 1
        # fast mode: quick model everywhere, including the deep-think seats
        config["deep_think_llm"] = config.get("quick_think_llm")

    analysts = [a.strip() for a in opts.analysts.split(",") if a.strip()]
    if opts.with_sentiment and "social" not in analysts:
        analysts.insert(1, "social")

    trade_date = opts.date or dt.date.today().strftime("%Y-%m-%d")
    logger.info("LLM analysis: symbol=%s date=%s analysts=%s fast=%s",
                opts.symbol, trade_date, ",".join(analysts), opts.fast)

    ta = TradingAgentsGraph(
        selected_analysts=tuple(analysts),
        debug=not opts.quiet,
        config=config,
    )
    final_state, _signal = ta.propagate(opts.symbol, trade_date, asset_type="commodity")

    if opts.reflect is not None:
        try:
            ta.reflect_and_remember(float(opts.reflect))
        except Exception as exc:  # noqa: BLE001 - memory is best-effort
            logger.warning("reflect_and_remember failed: %s", exc)

    report_path = ta.save_reports(final_state, opts.symbol)
    logger.info("LLM reports saved under %s", report_path)

    from tradingagents.agents.utils.rating import extract_rating, is_review

    decision_text = final_state.get("final_trade_decision", "") or ""
    rating = extract_rating(decision_text)
    if is_review(rating or ""):
        rating = None
    return rating, final_state


def parse_trader_levels(final_state: dict) -> tuple[float | None, list[float]]:
    """(stop_loss, take_profits) from the Trader's plan, if it stated any."""
    from tradingagents.brokers.gold_executor import (
        extract_price_levels,
        extract_take_profits,
    )

    plan_text = final_state.get("trader_investment_plan", "") or ""
    _entry, stop = extract_price_levels(plan_text)
    tps = extract_take_profits(plan_text)
    return stop, tps


def deterministic_candidate(mtf: MultiTimeframeView) -> str | None:
    """No-LLM fallback candidate from the multi-timeframe alignment."""
    if not mtf.usable:
        return None
    if mtf.aligned_direction == Bias.BULLISH:
        return "Buy"
    if mtf.aligned_direction == Bias.BEARISH:
        return "Sell"
    return None


# --------------------------------------------------------------- data assembly
def _state_store(cfg: GoldConfig) -> GoldStateStore:
    return GoldStateStore(
        Path(_default_config.DEFAULT_CONFIG["results_dir"]) / "gold_state.json",
        cfg.timezone,
    )


def _account_from_state(cfg: GoldConfig, equity: float,
                        state: GoldStateStore,
                        open_positions: int = 0) -> AccountContext:
    state.rollover(datetime.now(timezone.utc))
    c = state.counters
    return AccountContext(
        equity=equity,
        balance=equity,
        currency="USD",
        open_positions=open_positions,
        trades_today=c.trades_today,
        realized_pnl_today=c.realized_pnl_today,
        consecutive_losses=c.consecutive_losses,
        last_signal_id=c.last_signal_id,
        last_signal_time=(
            datetime.fromisoformat(c.last_signal_time)
            if c.last_signal_time else None
        ),
    )


def _collect_deterministic_inputs(cfg: GoldConfig, now: datetime):
    """Fundamentals + news for the confidence engine (best-effort).

    Returns ``(fundamentals, fresh_news, stale_news)`` from a single fetch.
    """
    try:
        fundamentals = collect_fundamentals(
            now.strftime("%Y-%m-%d"), now=now
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("fundamentals unavailable: %s", exc)
        fundamentals = None
    try:
        fresh, stale = collect_news(now=now)
    except Exception as exc:  # noqa: BLE001
        logger.warning("news unavailable: %s", exc)
        fresh, stale = [], []
    return fundamentals, fresh, stale


def _engine_eval(
    cfg: GoldConfig, rating: str | None, mtf: MultiTimeframeView,
    market: MarketContext, spec: BrokerSymbolSpec, account: AccountContext,
    fundamentals, news_fresh, trader_stop, trader_tps, review: bool = False,
) -> FinalDecision:
    engine = DeterministicRiskEngine(cfg)
    calendar = NewsBlackoutCalendar.build(
        engine.now, cfg.news_blackout_before_min, cfg.news_blackout_after_min,
        calendar_file=cfg.event_calendar_path or None,
    )
    return engine.evaluate(
        rating=rating, mtf=mtf, market=market, spec=spec, account=account,
        fundamentals=fundamentals, news_fresh=news_fresh, calendar=calendar,
        trader_stop=trader_stop, trader_take_profits=trader_tps, review=review,
    )


# ------------------------------------------------------------------- reporting
def _save_report(cfg: GoldConfig, opts: RunOptions, decision: FinalDecision,
                 mtf: MultiTimeframeView, market: MarketContext,
                 spec: BrokerSymbolSpec, account: AccountContext,
                 fundamentals, news_fresh, news_stale, session, rating,
                 bull_case: str, bear_case: str, trader_proposal: str,
                 gc_reference_price: float | None) -> str:
    text = render_report(
        symbol=opts.symbol, decision=decision, mtf=mtf, market=market,
        spec=spec, account=account, fundamentals=fundamentals,
        news_fresh=news_fresh, news_stale=news_stale, session=session,
        rating=rating, bull_case=bull_case, bear_case=bear_case,
        trader_proposal=trader_proposal,
        gc_reference_price=gc_reference_price,
        gc_reference_note="Yahoo GC=F reference (analysis-only mode)",
    )
    out_dir = Path(_default_config.DEFAULT_CONFIG["results_dir"]) / "gold_reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = out_dir / f"{stamp}_{opts.symbol.replace('=', '')}_report.md"
    path.write_text(text, encoding="utf-8")
    logger.info("Report written to %s", path)
    return str(path)


def _append_jsonl(cfg: GoldConfig, record: dict) -> str:
    out = Path(
        _default_config.DEFAULT_CONFIG["results_dir"]
    ) / "gold_mt5_executions.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    # Defence in depth: never let a credential-looking key slip into the log.
    banned = {"password", "token", "api_key", "apikey", "secret"}
    safe = {
        k: v for k, v in record.items()
        if not any(b in k.lower() for b in banned)
    }
    with out.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(safe, default=str) + "\n")
    return str(out)


# ----------------------------------------------------------------- main flows
def run_gold(opts: RunOptions) -> RunResult:
    """Analysis-only / dry-run / live flow (MT5-backed when available)."""
    cfg = build_config(opts)
    notes: list[str] = []
    now = datetime.now(timezone.utc)

    # ---- 1. Research layer (LLM) ------------------------------------------
    final_state: dict = {}
    rating: str | None = None
    trader_stop: float | None = None
    trader_tps: list[float] = []
    if not opts.no_llm:
        try:
            rating, final_state = run_llm_analysis(opts, cfg)
            trader_stop, trader_tps = parse_trader_levels(final_state)
        except Exception as exc:  # noqa: BLE001 - LLM failure degrades safely
            logger.error("LLM analysis failed: %s", exc, exc_info=opts.debug)
            notes.append(f"LLM analysis failed ({type(exc).__name__}); "
                         "falling back to deterministic candidate.")

    state = _state_store(cfg)

    # ---- 2. Market data ----------------------------------------------------
    broker = None
    candles = None
    provenance = None
    gc_reference_price: float | None = None

    if opts.analysis_only:
        try:
            candles, provenance = fetch_yf_timeframes(cfg.reference_symbol)
        except Exception as exc:  # noqa: BLE001
            logger.error("reference data unavailable: %s", exc)
            notes.append(f"market data unavailable: {exc}")
            return RunResult(ok=False, notes=notes)
        gc_reference_price = (
            float(candles["D1"]["close"].iloc[-1]) if "D1" in candles else None
        )
    else:
        from tradingagents.brokers.mt5_broker import MT5Broker, MT5Config

        broker_cfg = MT5Config.from_env(symbol=opts.mt5_symbol or None)
        broker = MT5Broker(broker_cfg)
        try:
            broker.connect()
        except Exception as exc:  # noqa: BLE001
            logger.error("MT5 connect failed: %s", exc, exc_info=opts.debug)
            notes.append(f"MT5 unavailable ({exc}); aborting — analysis still "
                         "possible with --analysis-only.")
            return RunResult(ok=False, notes=notes)
        sym = broker.resolve_symbol(opts.mt5_symbol or cfg.symbol)
        try:
            candles, provenance = fetch_mt5_timeframes(broker, sym)
        except Exception as exc:  # noqa: BLE001
            logger.error("MT5 data unavailable: %s", exc)
            notes.append(f"MT5 candle data unavailable: {exc}")
            return RunResult(ok=False, notes=notes)

    from .timeframes import build_multi_timeframe_view

    mtf = build_multi_timeframe_view(candles, cfg.min_candles, provenance)

    # ---- 3. Market + account context ---------------------------------------
    session = check_session(now, cfg.allowed_sessions, cfg.timezone)
    fundamentals, news_fresh, news_stale = _collect_deterministic_inputs(
        cfg, now
    )

    if broker is not None:
        tick = broker.tick()
        info = broker.symbol_info()
        spec: BrokerSymbolSpec = broker_spec_from_symbol_info(info)
        market = MarketContext(
            bid=float(tick.bid), ask=float(tick.ask),
            spread_points=spec.spread_points,
        )
        acct_info = broker.account_info()
        open_positions = len(broker.positions())
        account = _account_from_state(
            cfg, float(getattr(acct_info, "equity", 0.0)), state, open_positions
        )
        account.currency = str(getattr(acct_info, "currency", "USD"))
        account.margin_free = float(getattr(acct_info, "margin_free", 0.0) or 0.0)
    else:
        spec = reference_spec()
        # freshest closed candle available (M5 first, then M15, H1, H4, D1)
        last_close = 0.0
        for tf in ("M5", "M15", "H1", "H4", "D1"):
            frame = candles.get(tf)
            if frame is not None and len(frame):
                last_close = float(frame["close"].iloc[-1])
                break
        spread_pts = cfg.max_spread_points * 0.5  # labelled reference estimate
        half = spread_pts * spec.point / 2.0
        market = MarketContext(
            bid=round(last_close - half, spec.digits),
            ask=round(last_close + half, spec.digits),
            spread_points=spread_pts,
        )
        equity = opts.account_balance or cfg.paper_starting_balance
        account = _account_from_state(
            cfg, equity, state, open_positions=len(state.counters.open_paper_positions)
        )
        notes.append("REFERENCE DATA (Yahoo GC=F): prices are indicative, not "
                     "the broker's; spread is an estimate.")

    # ---- 4. Deterministic candidate fallback -------------------------------
    if rating is None and not opts.no_llm and final_state:
        # LLM ran but produced no parseable rating -> REVIEW semantics.
        rating = None
    if rating is None:
        cand = deterministic_candidate(mtf)
        if cand is not None:
            rating = cand
            notes.append("LLM rating unavailable; using deterministic "
                         "multi-timeframe candidate (engine still gates it).")

    # ---- 5. THE ENGINE ------------------------------------------------------
    decision = _engine_eval(
        cfg, rating, mtf, market, spec, account,
        fundamentals, news_fresh, trader_stop, trader_tps,
        review=bool(final_state) and rating is None and not opts.no_llm
        and bool(final_state.get("final_trade_decision")),
    )

    report_path = _save_report(
        cfg, opts, decision, mtf, market, spec, account, fundamentals,
        news_fresh, news_stale, session, rating,
        bull_case=_first_report(final_state, "investment_plan"),
        bear_case=_first_report(final_state, "investment_plan"),
        trader_proposal=final_state.get("trader_investment_plan", "") or "",
        gc_reference_price=gc_reference_price,
    )

    result = RunResult(
        ok=True, decision=decision, rating=rating, report_path=report_path,
        notes=notes,
    )

    # ---- 6. Execution -------------------------------------------------------
    if opts.analysis_only:
        result.execution = {
            "mode": "analysis-only", "submitted": False,
            "note": "analysis-only never connects to MT5",
        }
        return result

    assert broker is not None
    exec_summary = _execute_decision(
        opts, cfg, broker, decision, market, spec, account, state
    )
    result.execution = exec_summary

    _append_jsonl(cfg, {
        "ts": now.isoformat(timespec="seconds"),
        "mode": "live" if exec_summary.get("submitted") else (
            "dry-run" if not opts.analysis_only else "analysis-only"),
        "symbol": exec_summary.get("symbol") or cfg.symbol,
        "signal": decision.action,
        "rating": rating,
        "confidence": decision.confidence.total if decision.confidence else None,
        "entry": decision.plan.entry if decision.plan else None,
        "sl": decision.plan.stop_loss if decision.plan else None,
        "tps": decision.plan.take_profits if decision.plan else None,
        "rr": decision.plan.rr if decision.plan else None,
        "risk_percent": cfg.risk_per_trade,
        "risk_amount": decision.plan.risk_amount if decision.plan else None,
        "volume": decision.plan.volume if decision.plan else None,
        "spread_points": market.spread_points,
        "account_equity": account.equity,
        "dry_run": not cfg.live_trading_unlocked,
        "live": cfg.live_trading_unlocked,
        "signal_id": decision.plan.signal_id if decision.plan else None,
        "vetoed_by": decision.vetoed_by,
        "mt5": exec_summary.get("order"),
        "session": session.current_sessions,
        "report": report_path,
        "notes": notes,
    })
    with contextlib.suppress(Exception):
        broker.shutdown()
    return result


def _first_report(final_state: dict, key: str) -> str:
    return str(final_state.get(key, "") or "")


# ------------------------------------------------------------- paper trading
def run_paper(opts: RunOptions) -> RunResult:
    """Paper-trading flow: reference data + PaperBroker + persistent state.

    No MT5 connection, no orders — fills are simulated with conservative
    assumptions (spread paid, optional commission/slippage, SL-first when a
    candle touches both SL and TP; see :mod:`.paper`).
    """
    cfg = build_config(opts)
    notes: list[str] = []
    now = datetime.now(timezone.utc)

    try:
        candles, provenance = fetch_yf_timeframes(cfg.reference_symbol)
    except Exception as exc:  # noqa: BLE001
        return RunResult(ok=False, notes=[f"market data unavailable: {exc}"])

    state = _state_store(cfg)
    state.rollover(now)

    # ---- manage existing paper positions on fresh candles -------------------
    from .paper import PaperBroker, PaperPosition

    spec = reference_spec()
    paper = PaperBroker(
        spec=spec,
        starting_balance=state.counters.paper_balance or cfg.paper_starting_balance,
        commission_per_lot=cfg.paper_commission_per_lot,
        slippage_points=cfg.paper_slippage_points,
    )
    # NOTE: DataFrames are truthy-ambiguous; use explicit None checks.
    m15 = candles.get("M15")
    if m15 is None:
        m15 = candles.get("H1")
    if m15 is None:
        m15 = candles.get("D1")
    still_open: list[dict] = []
    for posdict in list(state.counters.open_paper_positions):
        pos = PaperPosition.from_dict(posdict)
        paper.position = pos
        replay = (
            m15[m15.index > pd.Timestamp(pos.opened_at)]
            if m15 is not None else None
        )
        closed = None
        if replay is not None and len(replay):
            for ts, candle in replay.iterrows():
                closed = paper.on_candle(candle, now=ts.to_pydatetime())
                if closed is not None:
                    break
        if closed is not None:
            state.record_trade(closed.pnl, closed.closed_at)
            logger.info("paper position closed: %s pnl=%s (%s)",
                        closed.side, closed.pnl, closed.exit_reason)
        else:
            still_open.append(posdict)
            logger.info("paper position still open: %s entry=%s",
                        pos.side, pos.entry)
    paper.position = None  # positions live in the store, not the broker
    state.set_paper_positions(still_open)

    # ---- research + deterministic engine ------------------------------------
    rating, final_state, trader_stop, trader_tps = _research(opts, cfg, notes)
    from .timeframes import build_multi_timeframe_view

    mtf = build_multi_timeframe_view(candles, cfg.min_candles, provenance)
    session = check_session(now, cfg.allowed_sessions, cfg.timezone)
    fundamentals, news_fresh, _news_stale = _collect_deterministic_inputs(
        cfg, now
    )

    last_close = 2400.0
    for tf in ("M5", "M15", "H1", "H4", "D1"):
        frame = candles.get(tf)
        if frame is not None and len(frame):
            last_close = float(frame["close"].iloc[-1])
            break
    spread_pts = cfg.max_spread_points * 0.5
    half = spread_pts * spec.point / 2.0
    market = MarketContext(
        bid=round(last_close - half, spec.digits),
        ask=round(last_close + half, spec.digits),
        spread_points=spread_pts,
    )
    open_positions = len(state.counters.open_paper_positions)
    account = _account_from_state(
        cfg, paper.balance + _open_unrealized(state, last_close, spec), state,
        open_positions,
    )
    account.balance = paper.balance

    if rating is None:
        cand = deterministic_candidate(mtf)
        if cand is not None:
            rating = cand
            notes.append("deterministic multi-timeframe candidate (no LLM rating)")

    decision = _engine_eval(
        cfg, rating, mtf, market, spec, account,
        fundamentals, news_fresh, trader_stop, trader_tps,
    )
    report_path = _save_report(
        cfg, opts, decision, mtf, market, spec, account, fundamentals,
        news_fresh, [], session, rating,
        bull_case=_first_report(final_state, "investment_plan"),
        bear_case="",
        trader_proposal=final_state.get("trader_investment_plan", "") or "",
        gc_reference_price=last_close,
    )

    execution: dict = {"mode": "paper", "submitted": False}
    if decision.tradeable and decision.plan is not None and open_positions == 0:
        plan = decision.plan
        filled = paper.open(
            side=plan.side, entry=plan.entry, stop_loss=plan.stop_loss,
            take_profits=plan.take_profits, volume=plan.volume, now=now,
            risk_amount=plan.risk_amount or 0.0, signal_id=plan.signal_id,
            ask=market.ask, bid=market.bid,
        )
        if filled.filled and paper.position is not None:
            state.record_signal(plan.signal_id, now)
            state.record_trade(0.0, now)
            state.set_paper_positions([paper.position.to_dict()])
            execution["opened"] = paper.position.to_dict()
            execution["note"] = "paper position opened (simulated fill)"
            logger.info("PAPER OPEN: %s %s @%s sl=%s tp=%s vol=%s",
                        plan.side, spec.symbol, paper.position.entry,
                        plan.stop_loss, plan.take_profits, plan.volume)
        else:
            execution["note"] = f"paper open rejected: {filled.reason}"
    else:
        execution["note"] = "; ".join(decision.reasons) or "HOLD"

    state.counters.paper_balance = paper.balance
    state.save()

    _append_jsonl(cfg, {
        "ts": now.isoformat(timespec="seconds"),
        "mode": "paper",
        "symbol": opts.symbol,
        "signal": decision.action,
        "rating": rating,
        "confidence": decision.confidence.total if decision.confidence else None,
        "rr": decision.plan.rr if decision.plan else None,
        "paper_balance": round(paper.balance, 2),
        "open_positions": len(state.counters.open_paper_positions),
        "signal_id": decision.plan.signal_id if decision.plan else None,
        "vetoed_by": decision.vetoed_by,
        "report": report_path,
        "notes": notes,
    })
    return RunResult(
        ok=True, decision=decision, rating=rating, report_path=report_path,
        execution=execution, notes=notes,
    )


def _open_unrealized(state: GoldStateStore, price: float, spec) -> float:
    """Mark-to-market the stored paper positions (simple, quote-currency)."""
    total = 0.0
    for posdict in state.counters.open_paper_positions:
        direction = 1.0 if posdict.get("side") == "BUY" else -1.0
        total += direction * (price - float(posdict.get("entry", price))) * \
            spec.contract_size * float(posdict.get("volume", 0.0))
    return round(total, 2)


def _research(opts: RunOptions, cfg: GoldConfig, notes: list[str]):
    """LLM research with safe degradation. Returns (rating, state, sl, tps)."""
    if opts.no_llm:
        return None, {}, None, []
    try:
        rating, final_state = run_llm_analysis(opts, cfg)
        trader_stop, trader_tps = parse_trader_levels(final_state)
        return rating, final_state, trader_stop, trader_tps
    except Exception as exc:  # noqa: BLE001
        logger.error("LLM analysis failed: %s", exc, exc_info=opts.debug)
        notes.append(f"LLM analysis failed ({type(exc).__name__}); "
                     "falling back to deterministic candidate.")
        return None, {}, None, []


# ---------------------------------------------------------------- backtesting
def run_backtest_flow(opts: RunOptions) -> RunResult:
    """Deterministic backtest + walk-forward split (no LLM, no orders)."""
    cfg = build_config(opts)
    from .backtest import Backtester

    try:
        candles, provenance = fetch_yf_timeframes(
            cfg.reference_symbol, timeframes=["D1", "H4", "H1", "M15", "M5"]
        )
    except Exception as exc:  # noqa: BLE001
        return RunResult(ok=False, notes=[f"market data unavailable: {exc}"])

    spec = reference_spec()
    backtester = Backtester(cfg, spec)
    base = candles.get("H1")
    n_base = 0 if base is None else len(base)
    # Adaptive warm-up: never larger than a third of the history.
    warmup = max(60, min(220, n_base // 3))
    try:
        broker, metrics = backtester.run(candles, warmup_bars=warmup)
    except ValueError as exc:
        return RunResult(ok=False, notes=[f"backtest failed: {exc}"])

    print("\n=== BACKTEST (deterministic engine, reference data) ===")
    print(f"symbol={cfg.reference_symbol} balance={cfg.paper_starting_balance}")
    print(metrics.summary())
    print("\nby side:", json.dumps(metrics.by_side))
    print("by session:", json.dumps(metrics.by_session))
    print("by news day:", json.dumps(metrics.by_news_day))

    # ---- walk-forward (Phase 29): in-sample vs out-of-sample ---------------
    # The split is by TIME (one boundary timestamp applied to every timeframe)
    # so frames of different lengths stay aligned.
    wf_note = "not enough data for walk-forward split"
    if base is not None and len(base) > 3 * warmup:
        cut = int(len(base) * 0.7)
        boundary = base.index[cut]
        in_candles = {tf: df[df.index <= boundary]
                      for tf, df in candles.items() if df is not None}
        out_candles = {tf: df[df.index > boundary]
                       for tf, df in candles.items() if df is not None}
        try:
            in_warmup = max(40, min(warmup, cut // 3))
            _, in_metrics = backtester.run(in_candles, warmup_bars=in_warmup)
            _, out_metrics = backtester.run(out_candles, warmup_bars=in_warmup)
            print("\n=== WALK-FORWARD SPLIT (70/30 chronological) ===")
            print(f"IN-SAMPLE        : {in_metrics.summary()}")
            print(f"OUT-OF-SAMPLE    : {out_metrics.summary()}")
            wf_note = (
                f"in-sample {in_metrics.total_trades} trades "
                f"({in_metrics.win_rate}% wr) vs out-of-sample "
                f"{out_metrics.total_trades} trades ({out_metrics.win_rate}% wr)"
            )
        except ValueError as exc:
            wf_note = f"walk-forward leg failed: {exc}"

    out_dir = Path(
        _default_config.DEFAULT_CONFIG["results_dir"]
    ) / "gold_reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = out_dir / f"{stamp}_backtest_{cfg.reference_symbol.replace('=', '')}.json"
    path.write_text(json.dumps({
        "symbol": cfg.reference_symbol,
        "metrics": metrics.__dict__,
        "walk_forward": wf_note,
        "assumptions": [
            "deterministic engine only (no LLM) — honest, reproducible",
            "SL fills before TP when one candle touches both (pessimistic)",
            f"commission {cfg.paper_commission_per_lot}/lot/side, "
            f"slippage {cfg.paper_slippage_points} pts",
            "reference data (Yahoo GC=F), not broker spot",
        ],
    }, indent=2, default=str), encoding="utf-8")
    print(f"\nBacktest artifacts: {path}")
    return RunResult(
        ok=True, notes=[f"backtest complete: {metrics.summary()}",
                        f"walk-forward: {wf_note}"],
    )


# ----------------------------------------------------------------- execution
def _execute_decision(
    opts: RunOptions, cfg: GoldConfig, broker, decision: FinalDecision,
    market: MarketContext, spec: BrokerSymbolSpec, account: AccountContext,
    state: GoldStateStore,
) -> dict:
    """Turn the engine decision into broker actions. NEVER fails open."""
    now = datetime.now(timezone.utc)
    summary: dict = {
        "symbol": spec.symbol, "submitted": False, "orders": [],
    }
    sym = spec.symbol

    # HOLD: optionally flatten (operator opt-in), never open.
    if not decision.tradeable:
        if opts.close_on_hold:
            positions = broker.positions(sym)
            for pos in positions:
                action = "CLOSE_BUY" if int(pos.type) == 0 else "CLOSE_SELL"
                res = broker.market_order(action, symbol=sym, comment="TA-hold-flatten")
                summary["orders"].append(res.to_dict())
                summary["submitted"] = summary["submitted"] or (
                    res.retcode in (10009, 10010)
                )
        summary["reason"] = "; ".join(decision.reasons) or "HOLD"
        return summary

    plan = decision.plan
    assert plan is not None and plan.valid

    # Operator fixed-volume override (--lots): still bounds-checked below and
    # every other gate still applies. It only replaces the risk-based size.
    if opts.lots is not None:
        plan.volume = float(opts.lots)

    # Double-gate check (defence in depth; engine config already encodes it).
    if not cfg.live_trading_unlocked:
        print("\n=== PROPOSED ORDER (DRY-RUN — nothing will be sent) ===")
        _print_order_block(plan, market, spec, account, decision, cfg)
        summary["proposed"] = plan.__dict__.copy()
        summary["note"] = ("dry-run: set MT5_DRY_RUN=false AND pass --live "
                           "(plus confirmation) to trade for real")
        return summary

    # ---- LIVE path: final safety confirmation (Phase 22/37) -----------------
    print("\n=== LIVE ORDER READY — FINAL CONFIRMATION REQUIRED ===")
    _print_order_block(plan, market, spec, account, decision, cfg)
    confirmed = _live_confirmation()
    if not confirmed:
        summary["note"] = "live order ABORTED at final confirmation"
        print("Aborted: no confirmation. No order was sent.")
        return summary

    # Final deterministic re-validation immediately before submission.
    final_check = _final_validation(plan, market, spec, account, cfg)
    if not final_check[0]:
        summary["note"] = f"final validation failed: {final_check[1]}"
        print(f"ABORTED — final validation failed: {final_check[1]}")
        return summary

    # Margin check via the terminal's own calculator when available.
    margin_ok, margin_why = _margin_check(broker, plan, spec, account)
    if not margin_ok:
        summary["note"] = f"margin check failed: {margin_why}"
        print(f"ABORTED — {margin_why}")
        return summary
    print(f"  MARGIN : {margin_why}")

    res = broker.market_order(
        plan.side, volume=plan.volume, sl=plan.stop_loss,
        tp=plan.tp1, symbol=sym, comment=f"TA-gold {plan.signal_id}"[:31],
    )
    summary["orders"].append(res.to_dict())
    summary["order"] = res.to_dict()
    summary["submitted"] = res.retcode in (10009, 10010) if not res.dry_run else False
    if res.ok:
        state.record_signal(plan.signal_id, now)
        state.record_trade(0.0, now)  # entry counts toward trades-per-day
        state.save()
    return summary


def _print_order_block(plan, market, spec, account, decision,
                       cfg: GoldConfig) -> None:
    print(f"  SYMBOL : {spec.symbol}")
    print(f"  SIDE   : {plan.side}")
    print(f"  VOLUME : {plan.volume}  (min {spec.volume_min} step {spec.volume_step})")
    print(f"  ENTRY  : {plan.entry}  (bid {market.bid} / ask {market.ask})")
    print(f"  SL     : {plan.stop_loss}  [{plan.stop_source}]")
    print(f"  TP     : {plan.take_profits}  [{plan.tp_source}]")
    print(f"  RR     : {plan.rr:.2f}  (min {cfg.min_rr})")
    print(f"  RISK   : {plan.risk_amount} ({cfg.risk_per_trade}% of equity)")
    print(f"  SPREAD : {market.spread_points} pts")
    print(f"  EQUITY : {account.equity:.2f} {account.currency}")
    if decision.confidence is not None:
        print(f"  CONF   : {decision.confidence.total:.1f}/100")


def _live_confirmation() -> bool:
    """Interactive confirm on a TTY; GOLD_LIVE_CONFIRM=YES for unattended."""
    env_confirm = os.getenv("GOLD_LIVE_CONFIRM", "").strip().upper()
    if env_confirm == "YES":
        return True
    if not sys.stdin.isatty():
        print("Non-interactive session: set GOLD_LIVE_CONFIRM=YES to allow "
              "live orders.")
        return False
    try:
        answer = input("Type LIVE (uppercase) to send this order: ").strip()
    except EOFError:
        return False
    return answer == "LIVE"


def _margin_check(broker, plan, spec: BrokerSymbolSpec,
                  account: AccountContext) -> tuple[bool, str]:
    """Best-effort margin validation via MT5's order_calc_margin."""
    mt5 = getattr(broker, "_mt5", None)
    calc = getattr(mt5, "order_calc_margin", None) if mt5 is not None else None
    if calc is None:
        return True, "not verified (order_calc_margin unavailable)"
    order_type = getattr(mt5, "ORDER_TYPE_BUY", 0) if plan.side == "BUY" \
        else getattr(mt5, "ORDER_TYPE_SELL", 1)
    try:
        required = calc(order_type, spec.symbol, plan.volume, plan.entry)
    except Exception as exc:  # noqa: BLE001 - terminal-dependent
        return True, f"not verified ({type(exc).__name__})"
    if required is None:
        return True, "not verified (calculator returned None)"
    free = account.margin_free
    if free is not None and float(required) > free:
        return False, (f"insufficient margin: required {float(required):.2f} "
                       f"> free {free:.2f}")
    return True, f"required {float(required):.2f} <= free {free}"


def _final_validation(plan, market, spec, account, cfg: GoldConfig) -> tuple[bool, str]:
    """Last-line deterministic checks right before orders_send."""
    if plan.stop_loss is None:
        return False, "plan has no stop-loss — unprotected orders are forbidden"
    side = plan.side
    if side == "BUY" and not plan.stop_loss < (market.ask or plan.entry):
        return False, "SL not below entry for BUY"
    if side == "SELL" and not plan.stop_loss > (market.bid or plan.entry):
        return False, "SL not above entry for SELL"
    if plan.volume is None or not (spec.volume_min <= plan.volume <= spec.volume_max):
        return False, f"volume {plan.volume} outside broker bounds"
    if plan.tp1 is None or plan.rr is None or plan.rr < cfg.min_rr:
        return False, f"RR {plan.rr} below minimum {cfg.min_rr}"
    if account.margin_free is not None and account.margin_free <= 0:
        return False, "no free margin"
    return True, "ok"
