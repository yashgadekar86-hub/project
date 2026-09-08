#!/usr/bin/env python
"""TradingAgents -> Gold (XAUUSD) -> MetaTrader 5 runner.

Thin command-line facade over :mod:`tradingagents.gold.runner` — all logic
lives there (and is fully tested); this file only parses arguments, routes to
the requested mode, and prints the result.

Modes (mutually exclusive; default = dry-run):

    --analysis-only   research + deterministic engine + report. NEVER touches MT5.
    --dry-run         + connects MT5 read-only (account/price/positions) and
                      prints the PROPOSED ORDER. Sends nothing.
    --paper           paper trading: simulated fills, persistent paper state.
    --backtest        deterministic backtest + walk-forward split (no LLM).
    --live            REAL ORDERS. Requires ALL of:
                      (1) MT5_DRY_RUN=false in the environment,
                      (2) the --live flag,
                      (3) a final confirmation (interactive "LIVE" prompt, or
                          GOLD_LIVE_CONFIRM=YES for unattended runs).

Examples (PowerShell on Windows):

    python gold_mt5.py --analysis-only
    python gold_mt5.py --paper
    python gold_mt5.py --backtest
    python gold_mt5.py --dry-run
    python gold_mt5.py --live          # all three gates must open

The deterministic risk engine (news blackout, min RR, spread, session,
daily loss, position limits, confidence floor, structural SL) vets every
decision. An LLM can never bypass it. HOLD is a first-class outcome.
"""

from __future__ import annotations

import argparse
import logging
import sys

from tradingagents.gold.runner import (
    RunOptions,
    run_backtest_flow,
    run_gold,
    run_paper,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="TradingAgents Gold/XAUUSD analysis + MetaTrader 5 "
                    "execution (deterministic risk engine gated)",
    )
    p.add_argument("--symbol", default="XAUUSD",
                   help="Gold symbol for the analysis pipeline (default: XAUUSD).")
    p.add_argument("--mt5-symbol", default=None,
                   help="Broker-side gold contract name (default: auto-detect "
                        "XAUUSD/GOLD/XAUUSD+... on the MT5 server).")
    p.add_argument("--date", default=None,
                   help="Analysis date YYYY-MM-DD (default: today). A past "
                        "date runs the agents on historical (point-in-time) data.")
    p.add_argument("--analysts", default="market,news,fundamentals",
                   help="Comma list from: market,news,fundamentals,social")
    p.add_argument("--with-sentiment", action="store_true",
                   help="Include the sentiment (social) analyst.")

    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--analysis-only", action="store_true",
                      help="Analyse + report. NEVER connects to MT5.")
    mode.add_argument("--paper", action="store_true",
                      help="Paper trading (simulated fills, persistent state).")
    mode.add_argument("--backtest", action="store_true",
                      help="Deterministic backtest + walk-forward (no LLM).")
    mode.add_argument("--dry-run", action="store_true",
                      help="Force dry-run even when MT5_DRY_RUN=false.")
    mode.add_argument("--live", action="store_true",
                      help="Allow REAL orders (still needs MT5_DRY_RUN=false "
                           "AND a final confirmation).")

    p.add_argument("--allow-short", action="store_true",
                   help="Permit short positions (default: long-only; bearish "
                        "signals only flatten an existing long).")
    p.add_argument("--close-on-hold", action="store_true",
                   help="Flatten the open position when the final decision is HOLD.")
    p.add_argument("--lots", type=float, default=None,
                   help="Fixed volume override (disables risk-based sizing; "
                        "still gated by every deterministic check).")
    p.add_argument("--risk-percent", type=float, default=None,
                   help="Percent of equity risked per trade (default: RISK_PER_TRADE=0.5).")
    p.add_argument("--min-rr", type=float, default=None,
                   help="Minimum reward:risk (default: MIN_RR=2.0).")
    p.add_argument("--min-confidence", type=float, default=None,
                   help="Minimum deterministic confidence 0-100 (default: 75).")
    p.add_argument("--account-balance", type=float, default=None,
                   help="Equity used when no broker account is connected "
                        "(analysis/paper modes).")
    p.add_argument("--timeframe", default=None,
                   help="Working timeframe for structure/SL/TP (default: H1).")
    p.add_argument("--backtest-days", type=int, default=400,
                   help="Backtest window hint in days (default: 400).")
    p.add_argument("--fast", action="store_true",
                   help="Lightweight LLM mode (fewer/cheaper calls); all "
                        "deterministic risk checks stay on.")
    p.add_argument("--no-llm", action="store_true",
                   help="Skip the LLM pipeline entirely; use the deterministic "
                        "multi-timeframe candidate (still fully gated).")
    p.add_argument("--reflect", type=float, default=None, metavar="R",
                   help="Feed a realized trade result (in R) into agent memory.")
    p.add_argument("--debug", action="store_true", help="Verbose error traces.")
    p.add_argument("--quiet", action="store_true", help="Less logging output.")
    return p.parse_args(argv)


def _opts_from_args(args) -> RunOptions:
    return RunOptions(
        symbol=args.symbol,
        mt5_symbol=args.mt5_symbol,
        date=args.date,
        analysts=args.analysts,
        with_sentiment=args.with_sentiment,
        analysis_only=args.analysis_only,
        force_dry_run=args.dry_run,
        live=args.live,
        paper=args.paper,
        backtest=args.backtest,
        backtest_days=args.backtest_days,
        allow_short=args.allow_short,
        close_on_hold=args.close_on_hold,
        lots=args.lots,
        risk_percent=args.risk_percent,
        min_rr=args.min_rr,
        min_confidence=args.min_confidence,
        account_balance=args.account_balance,
        timeframe=args.timeframe,
        fast=args.fast,
        no_llm=args.no_llm,
        debug=args.debug,
        quiet=args.quiet,
        reflect=args.reflect,
    )


def print_result(result) -> None:
    line = "=" * 70
    print(f"\n{line}\nGOLD DECISION SUMMARY\n{line}")
    if result.decision is not None:
        print(result.decision.summary())
        if result.decision.confidence is not None:
            print(result.decision.confidence.display())
        plan = result.decision.plan
        if plan is not None and plan.valid:
            print(f"plan: {plan.side} entry={plan.entry} sl={plan.stop_loss} "
                  f"tp={plan.take_profits} rr={plan.rr} vol={plan.volume} "
                  f"risk={plan.risk_amount}")
        for c in result.decision.checks:
            mark = "PASS" if c.passed else "FAIL"
            print(f"  [{mark}] {c.name}: {c.reason}")
    if result.rating:
        print(f"portfolio rating: {result.rating}")
    if result.report_path:
        print(f"report: {result.report_path}")
    exec_ = result.execution
    if exec_:
        mode = exec_.get("mode", "?")
        print(f"\n--- execution ({mode}) ---")
        for k in ("note", "reason", "opened"):
            if exec_.get(k):
                print(f"  {k}: {exec_[k]}")
        for order in exec_.get("orders", []):
            print(f"  {order}")
        if exec_.get("proposed"):
            print(f"  PROPOSED: {exec_['proposed']}")
    for note in result.notes:
        print(f"note: {note}")
    print(line)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.debug else
        (logging.WARNING if args.quiet else logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    opts = _opts_from_args(args)

    try:
        if opts.backtest:
            result = run_backtest_flow(opts)
        elif opts.paper:
            result = run_paper(opts)
        else:
            result = run_gold(opts)
    except Exception as exc:  # noqa: BLE001 - top-level guard: never fail open
        logging.getLogger("gold_mt5").error(
            "run failed: %s", exc, exc_info=args.debug)
        print(f"\nRUN FAILED (treated as NO TRADE / HOLD): {exc}", file=sys.stderr)
        return 1

    print_result(result)
    return 0 if result.ok else 2


if __name__ == "__main__":
    sys.exit(main())
