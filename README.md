# Gold Trading Agents for MetaTrader 5

A **gold-trading fork of [TradingAgents](https://github.com/TauricResearch/TradingAgents)** (TauricResearch): the multi-agent LLM financial trading framework, re-pointed at **gold (XAUUSD)** and wired to execute on **MetaTrader 5 on Windows**.

An AI "trading firm" — market, news and fundamentals analysts; bull/bear researchers; a trader; a risk-management debate; and a portfolio manager — analyses gold every run and produces a 5-tier rating (`Buy / Overweight / Hold / Underweight / Sell`). This fork then maps that decision onto a real MT5 order (or a simulated one — dry-run is the default).

> ⚠️ **Research/educational use.** Nothing here is financial advice. LLM decisions are non-deterministic and can be wrong. Start in dry-run, then on a **demo account**, and only consider live trading after you trust the behaviour. You are responsible for any order sent from your account.

---

## What changed vs. upstream TradingAgents

| Area | Change |
|---|---|
| **Asset type** | New `commodity` asset type. `XAUUSD`, `GOLD`, `GC=F`, … are detected as commodities (`cli/models.py`, `cli/utils.py`). |
| **Fundamentals analyst** | For commodities it becomes a **macro/supply-demand strategist** (FRED real yields, dollar index, CPI, Fed funds, gold fix + macro news) instead of hunting for company financials that don't exist for a metal (`tradingagents/agents/analysts/fundamentals_analyst.py`). |
| **Instrument context** | Agents are told the target is a commodity so they never assume earnings/balance sheets (`tradingagents/agents/utils/agent_utils.py`). |
| **FRED macro series** | Gold drivers added: `gold_price` (LBMA PM fix), `real_yield_5y/10y/30y`, `tips_10y`, `dxy` (`tradingagents/dataflows/fred.py`). |
| **News queries** | Default macro-news queries re-pointed at gold drivers (Fed/real yields, DXY, central-bank buying, safe-haven demand, ETF flows) (`tradingagents/gold_config.py`). |
| **MT5 broker** | New `tradingagents/brokers/` package: connection, gold-symbol auto-detect, risk-based lot sizing, SL/TP, position flattening, **dry-run safety** (`mt5_broker.py`, `gold_executor.py`). |
| **Runner** | New `gold_mt5.py`: runs the agent team for gold, prints/saves reports, and executes the decision on MT5. |

Everything upstream (CLI, backtesting-style dated runs, memory/reflection, checkpointing, all LLM providers) still works — see [UPSTREAM_README.md](UPSTREAM_README.md).

---

## Quick start (Windows + MT5)

Full step-by-step instructions, including screenshots-free terminal walkthroughs and Task Scheduler automation, are in **[docs/GOLD_MT5_WINDOWS.md](docs/GOLD_MT5_WINDOWS.md)**.

```powershell
# 1. Install Python 3.10+ and MetaTrader 5 (terminal open & logged in)

# 2. Install the framework + MT5 bridge
git clone <this-repo>
cd project
pip install -e ".[mt5]"

# 3. Configure keys: copy .env.example to .env and fill in
#    - an LLM key (e.g. OPENAI_API_KEY)
#    - FRED_API_KEY (free, for macro data)
#    - MT5_* settings (login/server only needed if the terminal isn't logged in)

# 4. Analysis only (no MT5 needed at all)
python gold_mt5.py --analysis-only

# 5. Analysis + simulated MT5 order (dry-run, the default)
python gold_mt5.py

# 6. Real orders — requires BOTH gates open:
setx MT5_DRY_RUN false        # env gate (new shell afterwards)
python gold_mt5.py --live     # CLI gate
```

### How a decision becomes an order

```
Rating            ->  MT5 action (default settings)
Buy / Overweight  ->  BUY  (opens a long, risk-sized)
Hold              ->  nothing (keep position; --close-on-hold flattens)
Underweight/Sell  ->  flatten any long; open a short only with --allow-short
REVIEW/unparsable ->  never traded
```

- **Sizing**: `risk_percent` of equity divided by (SL distance × contract size); or `--lots 0.01` to fix volume.
- **SL/TP**: from the Trader's stop level when it stated one, else `--sl-distance` (e.g. `20` = $20 on gold); TP defaults to the SL distance (1:1), or `--tp-distance`.
- **Symbol**: auto-detected among `XAUUSD / GOLD / XAUUSD+ / GOLDm / …`; pin it with `MT5_SYMBOL`.

## Tests

```bash
pip install -e ".[dev]"
pytest tests/ -q          # 780+ tests incl. the gold/MT5 suite
```

## Repository map

```
gold_mt5.py                        <- gold runner (analysis -> MT5)
tradingagents/brokers/             <- MT5 broker + gold executor (NEW)
tradingagents/gold_config.py       <- gold overlays: news queries, symbols (NEW)
tradingagents/agents/...           <- agent team (commodity-aware fundamentals)
tradingagents/dataflows/...        <- yfinance/FRED/news vendors (+ gold series)
docs/GOLD_MT5_WINDOWS.md           <- full Windows setup guide (NEW)
tests/test_gold_mt5.py             <- gold + MT5 test suite (NEW)
UPSTREAM_README.md                 <- original TradingAgents README
```

## License & attribution

This is a derivative work of [TradingAgents](https://github.com/TauricResearch/TradingAgents) by TauricResearch, used under the terms of its [license](LICENSE). Gold/MT5 additions in this fork inherit the same terms.
