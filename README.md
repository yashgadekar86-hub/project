# Gold Trading Agents for MetaTrader 5

A fork of [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents)
(v0.4.0, Apache-2.0 — see `NOTICE` and `UPSTREAM_README.md`) re-pointed at
**gold (XAUUSD)** with a **MetaTrader 5** bridge for Windows, and hardened with
a **deterministic risk engine** that has final authority over every trade
decision.

> **Research / paper-trading system.** Not investment advice. No profitability
> is claimed — evaluate with the included backtester and out-of-sample split
> before risking anything.

## Architecture

```
                    XAUUSD
                       |
        Market Data (MT5 primary; GC=F reference, labelled)
                       |
       +---------------+----------------+
       |               |                |
  Technical       Fundamental         News
  (deterministic)  (FRED, provenance) (freshness-filtered)
       |               |                |
       +---------------+----------------+
                       |
          Multi-timeframe hierarchy (D1>H4>H1>M15>M5)
                       |
         [LLM layer: analysts -> Bull/Bear debate ->
          Research Manager -> Trader -> Portfolio rating]
                       |
             DETERMINISTIC RISK ENGINE  (final authority)
                       |
     RR / news blackout / spread / session / volatility /
     daily loss / positions / sizing / confidence gates
                       |
                Final decision: BUY / SELL / HOLD
                       |
              Paper / MT5 dry-run / MT5 live
                       |
              Audit log (JSONL) + markdown report + state
```

**The golden rule: LLMs reason; deterministic code decides.** No LLM output can
enable live trading, override a gate, inflate position size, or manufacture a
take-profit.

## What changed vs. upstream TradingAgents

| Area | Change |
|---|---|
| Asset type | New `commodity` type; XAUUSD/GOLD/GC=F classify as commodity |
| Fundamentals analyst | Macro drivers (real yields, DXY, CPI, Fed funds, gold fix) replace company financials |
| `tradingagents/gold/` | **New deterministic package** (17 modules): indicators, structure (BOS/CHOCH), multi-timeframe hierarchy, sessions, news blackout, levels (SL/TP), sizing, risk engine, paper broker, backtester, state, reporting |
| `tradingagents/brokers/` | MT5 bridge: symbol auto-detect, risk sizing from real symbol economics, SL/TP attached to every order |
| Runner | `gold_mt5.py` rewritten around the engine: `--analysis-only`, `--dry-run`, `--paper`, `--backtest`, `--live`, `--fast`, `--no-llm`, `--min-rr`, `--min-confidence`, `--allow-short`, ... |
| CLI | `tradingagents gold analyze --symbol XAUUSD` subcommand |
| Tests | 240+ new tests incl. 13 explicit safety invariants (959 total) |

## Quick start (Windows + MT5)

```powershell
# 1. Install Python 3.10+ and MetaTrader 5 (terminal open & logged in)
git clone <this repo>; cd project

# 2. Install the framework + MT5 bridge
pip install -e ".[mt5]"

# 3. Configure: copy .env.example to .env
#    - an LLM key (e.g. OPENAI_API_KEY)
#    - FRED_API_KEY (free, for macro data)
#    - MT5_* settings (only if the terminal isn't already logged in)

# 4. Analysis only — no MT5, no orders
python gold_mt5.py --analysis-only

# 5. Paper trading (simulated fills, persistent paper account)
python gold_mt5.py --paper

# 6. Deterministic backtest + walk-forward split (no LLM, no keys)
python gold_mt5.py --backtest

# 7. Analysis + MT5 read-only + PROPOSED ORDER (dry-run, the default)
python gold_mt5.py --dry-run

# 8. Real orders — requires ALL THREE gates:
#    MT5_DRY_RUN=false  AND  --live  AND  the final confirmation
#    (type LIVE at the prompt, or GOLD_LIVE_CONFIRM=YES when unattended)
python gold_mt5.py --live
```

Same flows via the installed CLI: `tradingagents gold analyze --symbol XAUUSD
--analysis-only`.

### How a rating becomes an order

| Portfolio rating | Deterministic translation |
|---|---|
| Buy / Overweight | BUY *candidate* — must still pass every gate |
| Hold | NO TRADE (optionally flatten with `--close-on-hold`) |
| Underweight / Sell | SELL candidate — only if `--allow-short`; otherwise flatten-long only |
| REVIEW / unparseable | NO TRADE, never |

Gates (any failure = HOLD, none overridable by the LLM): news blackout
(CPI/NFP/FOMC ±30 min), session filter, spread cap, volatility band,
higher-timeframe alignment, min RR (default 2.0 — the old 1:1 default is
gone), risk-based sizing from **actual broker symbol economics** (tick value,
volume step, min/max), daily loss / trade-count / loss-streak / position
limits, duplicate-signal suppression, deterministic confidence floor (75/100).

### Safety model

* **Dry-run by default.** Live needs `MT5_DRY_RUN=false` **and** `--live`
  **and** a final confirmation, then a last deterministic re-validation.
* **Long-only by default** (`ALLOW_SHORT=false`).
* **Every live order carries its SL** — the broker wrapper refuses to send an
  unprotected order.
* **HOLD is a first-class outcome.** The system is deliberately not optimized
  to trade more.

## Tests

```powershell
python -m pytest tests/ -q        # 959 passed (2 env-gated skips)
python -m pytest tests/test_gold_risk_engine.py -q   # the 13 safety invariants
```

No test requires MT5, network, LLM keys, or money.

## Repository map

| Path | Contents |
|---|---|
| `gold_mt5.py` | CLI runner (thin facade over `tradingagents.gold.runner`) |
| `tradingagents/gold/` | The deterministic engine (config, models, marketdata, indicators, structure, timeframes, fundamentals, news, news_blackout, sessions, levels, sizing, risk_engine, paper, backtest, state, report, runner) |
| `tradingagents/brokers/` | `MT5Broker` + legacy `GoldExecutor` (rating→order direct bridge) |
| `tradingagents/gold_config.py` | Gold overlays for the LLM research layer |
| `docs/GOLD_MT5_WINDOWS.md` | Step-by-step Windows setup, scheduling, troubleshooting |
| `tests/test_gold_*.py` | The gold/deterministic test suites |

## License & attribution

Apache-2.0 (upstream) — derivative work; see `NOTICE` for the modification
list and `UPSTREAM_README.md` for the original project documentation.
