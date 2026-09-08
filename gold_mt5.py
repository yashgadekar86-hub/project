#!/usr/bin/env python
"""TradingAgents -> gold (XAUUSD) -> MetaTrader 5 runner.

Runs the full multi-agent pipeline (analysts -> researchers -> trader -> risk
debate -> portfolio manager) for GOLD, then maps the Portfolio Manager's
rating onto an MT5 order on your Windows MetaTrader 5 terminal.

Examples (PowerShell on Windows):

    # 1) Analysis only -- no MT5 connection needed
    python gold_mt5.py --analysis-only

    # 2) Full pipeline + DRY-RUN order (default): connects to MT5, prints the
    #    exact order it WOULD send, sends nothing
    python gold_mt5.py

    # 3) Actually trade (real money!). Requires MT5_DRY_RUN=false in .env
    #    AND the --live flag below.
    python gold_mt5.py --live

    # Analyse a past date (backtest mode)
    python gold_mt5.py --date 2025-06-12 --analysis-only

Safety model: a real order requires BOTH ``--live`` on the command line and
``MT5_DRY_RUN=false`` in the environment. Anything less simulates.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from tradingagents.default_config import DEFAULT_CONFIG  # noqa: E402
from tradingagents.gold_config import build_gold_config  # noqa: E402

logger = logging.getLogger("gold_mt5")

# Analysts used for gold by default. The sentiment analyst is skipped because
# its sources (StockTwits cashtags, r/wallstreetbets) are equity-centric and
# add noise for gold; keep it with --with-sentiment if you want it.
GOLD_ANALYSTS = ("market", "news", "fundamentals")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="TradingAgents gold analysis + MetaTrader 5 execution",
    )
    p.add_argument("--symbol", default="XAUUSD",
                   help="Gold symbol passed through the pipeline (default: XAUUSD).")
    p.add_argument("--mt5-symbol", default=None,
                   help="Broker-side gold contract name (default: auto-detect "
                        "XAUUSD/GOLD/XAUUSD+... on the MT5 server).")
    p.add_argument("--date", default=None,
                   help="Analysis date YYYY-MM-DD (default: today). Use a past "
                        "date to backtest the agents' decision.")
    p.add_argument("--analysts", default="market,news,fundamentals",
                   help="Comma list from: market,news,fundamentals,social")
    p.add_argument("--with-sentiment", action="store_true",
                   help="Include the sentiment (social) analyst.")
    p.add_argument("--analysis-only", action="store_true",
                   help="Run the agents and print/save reports; skip MT5 entirely.")
    p.add_argument("--dry-run", action="store_true", default=None,
                   help="Force dry-run (default unless MT5_DRY_RUN=false).")
    p.add_argument("--live", action="store_true",
                   help="Allow real orders. Still requires MT5_DRY_RUN=false.")
    p.add_argument("--lots", type=float, default=None,
                   help="Fixed volume override (e.g. 0.01). Disables risk sizing.")
    p.add_argument("--risk-percent", type=float, default=None,
                   help="Percentage of equity risked per trade (default: MT5_RISK_PERCENT or 1.0).")
    p.add_argument("--sl-distance", type=float, default=None,
                   help="Stop-loss distance in price (e.g. 20 = $20 on gold).")
    p.add_argument("--tp-distance", type=float, default=None,
                   help="Take-profit distance in price (defaults to SL distance).")
    p.add_argument("--allow-short", action="store_true",
                   help="Open short positions on bearish signals (default: flatten only).")
    p.add_argument("--close-on-hold", action="store_true",
                   help="Flatten the open gold position when the agents say Hold.")
    p.add_argument("--quiet", action="store_true", help="Less logging output.")
    return p.parse_args(argv)


def run_analysis(args) -> tuple[dict, str]:
    """Run the agent graph for gold; return (final_state, signal)."""
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    config = build_gold_config(DEFAULT_CONFIG.copy())
    analysts = [a.strip() for a in args.analysts.split(",") if a.strip()]
    if args.with_sentiment and "social" not in analysts:
        analysts.insert(1, "social")

    trade_date = args.date or dt.date.today().strftime("%Y-%m-%d")

    logger.info("Starting gold analysis: symbol=%s date=%s analysts=%s",
                args.symbol, trade_date, ",".join(analysts))

    ta = TradingAgentsGraph(
        selected_analysts=tuple(analysts),
        debug=not args.quiet,
        config=config,
    )
    final_state, signal = ta.propagate(args.symbol, trade_date, asset_type="commodity")

    report_path = ta.save_reports(final_state, args.symbol)
    logger.info("Reports saved to %s", report_path)
    return final_state, signal


def run_execution(args, final_state: dict, signal: str) -> dict:
    """Map the decision onto an MT5 order (dry-run unless explicitly live)."""
    import os

    from tradingagents.brokers import (
        GoldExecutor,
        GoldExecutorConfig,
        MT5Broker,
        MT5Config,
    )

    env_dry = os.getenv("MT5_DRY_RUN", "true").strip().lower() in ("1", "true", "yes", "on")
    dry_run = env_dry or not args.live  # BOTH gates must open for real orders
    if args.dry_run:
        dry_run = True

    broker_cfg = MT5Config.from_env(
        symbol=args.mt5_symbol,
        fixed_lots=args.lots,
        risk_percent=args.risk_percent,
        stop_loss_distance=args.sl_distance,
        take_profit_distance=args.tp_distance,
        dry_run=dry_run,
    )
    exec_cfg = GoldExecutorConfig(
        allow_short=args.allow_short,
        close_on_hold=args.close_on_hold,
    )

    summary: dict = {
        "signal": signal,
        "dry_run": dry_run,
        "orders": [],
        "account": None,
    }

    with MT5Broker(broker_cfg) as broker:
        summary["account"] = broker.snapshot()
        executor = GoldExecutor(broker, exec_cfg)
        results = executor.execute(
            final_state["final_trade_decision"],
            trader_plan=final_state.get("trader_investment_plan", ""),
        )
        summary["orders"] = [r.to_dict() for r in results]

    return summary


def print_summary(final_state: dict, signal: str, exec_summary: dict | None) -> None:
    line = "=" * 70
    print(f"\n{line}\nGOLD DECISION SUMMARY\n{line}")
    print(f"Signal (Portfolio Manager rating): {signal}")
    print(f"\n--- Final decision ---\n{final_state.get('final_trade_decision', '').strip()}")
    trader_plan = final_state.get("trader_investment_plan", "").strip()
    if trader_plan:
        print(f"\n--- Trader plan ---\n{trader_plan}")
    if exec_summary is not None:
        print(f"\n--- MT5 execution ({'DRY-RUN' if exec_summary['dry_run'] else 'LIVE'}) ---")
        acct = exec_summary.get("account") or {}
        if acct:
            print(f"Account: login={acct.get('login')} server={acct.get('server')} "
                  f"balance={acct.get('balance')} {acct.get('currency')} "
                  f"| {acct.get('symbol')} bid={acct.get('bid')} ask={acct.get('ask')}")
        if exec_summary["orders"]:
            for order in exec_summary["orders"]:
                print(f"  {order['action']:>10} {order['symbol']} vol={order['volume']} "
                      f"price={order['price']} sl={order['sl']} tp={order['tp']} "
                      f"retcode={order['retcode']} {order['comment']}")
        else:
            print("  No order generated for this signal.")
    print(line)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        final_state, signal = run_analysis(args)
    except Exception as exc:
        logger.error("Analysis failed: %s", exc, exc_info=not args.quiet)
        return 1

    print_summary(final_state, signal, None)

    if args.analysis_only:
        logger.info("--analysis-only: skipping MT5 execution.")
        return 0

    try:
        exec_summary = run_execution(args, final_state, signal)
    except Exception as exc:
        logger.error("MT5 execution step failed: %s", exc, exc_info=not args.quiet)
        print(f"\nMT5 step failed: {exc}\n(The analysis above is still valid; "
              "fix MT5 connectivity or re-run with --analysis-only.)")
        return 2

    print_summary(final_state, signal, exec_summary)

    # Persist an execution record next to the reports.
    out = Path(DEFAULT_CONFIG["results_dir"]) / "gold_mt5_executions.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "ts": dt.datetime.now().isoformat(timespec="seconds"),
            "symbol": args.symbol,
            "date": args.date or dt.date.today().strftime("%Y-%m-%d"),
            **exec_summary,
        }, default=str) + "\n")
    logger.info("Execution record appended to %s", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
