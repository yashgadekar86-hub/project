# Gold Trading Agents on Windows MT5 — Setup Guide

This guide walks you from a fresh Windows machine to:

1. the AI agent team analysing **gold (XAUUSD)** and saving full reports, and
2. that decision being sent to your **MetaTrader 5** terminal (dry-run first,
   demo next, live only when *you* unlock it).

---

## 1. Prerequisites

| Requirement | Notes |
|---|---|
| Windows 10/11 | The `MetaTrader5` Python package is **Windows-only**. |
| Python 3.10+ | 64-bit. <https://www.python.org/downloads/> — tick *"Add python.exe to PATH"* during install. |
| MetaTrader 5 terminal | Installed from your broker, opened, and logged into the account you want to trade (demo is strongly recommended first). |
| An LLM API key | OpenAI, Anthropic, Google, DeepSeek, or any supported provider. |
| FRED API key (free) | Powers the macro data (rates, dollar, CPI, gold fix). Get one at <https://fred.stlouisfed.org/docs/api/api_key.html>. Without it the macro tools degrade gracefully, but the gold fundamentals report is much weaker. |

> **Algo-trading switch**: in the MT5 terminal open *Tools → Options → Expert Advisors* and make sure **"Allow algorithmic trading"** is ticked. Without it, `orders_send` is rejected by the terminal.

## 2. Install

```powershell
git clone <this-repo>
cd project

# core framework + the MetaTrader5 bridge (the [mt5] extra)
pip install -e ".[mt5]"
```

`MetaTrader5` only publishes Windows wheels, so run this on the same machine where the terminal is installed.

## 3. Configure `.env`

Copy the example and edit:

```powershell
copy .env.example .env
notepad .env
```

Fill in at least:

```ini
# --- LLM (set the one you use) ---
OPENAI_API_KEY=sk-...
# Optional model overrides
#TRADINGAGENTS_LLM_PROVIDER=openai
#TRADINGAGENTS_DEEP_THINK_LLM=gpt-5.6
#TRADINGAGENTS_QUICK_THINK_LLM=gpt-5.6-luna

# --- macro data for the gold fundamentals analyst ---
FRED_API_KEY=your-fred-key

# --- MT5 (all optional if the terminal is already logged in) ---
#MT5_LOGIN=12345678
#MT5_PASSWORD=your-mt5-password
#MT5_SERVER=YourBroker-Demo
#MT5_SYMBOL=XAUUSD        ; your broker's gold contract; auto-detected if unset
#MT5_DRY_RUN=true         ; SAFETY SWITCH - keep true until you mean it

# --- deterministic risk engine (see .env.example for the full list) ---
#RISK_PER_TRADE=0.5       ; % of equity risked per trade
#MAX_DAILY_LOSS=2         ; stop trading for the day after -2%
#MIN_RR=2.0               ; minimum reward:risk (below -> HOLD)
#MIN_CONFIDENCE=75        ; deterministic confidence floor (0-100)
#MAX_SPREAD=45            ; max spread in points (gold spreads explode on news)
#NEWS_BLACKOUT_BEFORE=30  ; minutes before CPI/NFP/FOMC -> HOLD
#NEWS_BLACKOUT_AFTER=30   ; minutes after
#ALLOW_SHORT=false        ; long-only by default
#ALLOWED_SESSIONS=London,NewYork,Overlap
#GOLD_TIMEZONE=UTC        ; display timezone (IANA), never hard-coded
```

Notes:

- If your MT5 terminal is already open and logged in, you can leave `MT5_LOGIN` / `MT5_PASSWORD` / `MT5_SERVER` unset — the bridge attaches to the running session.
- Brokers name gold inconsistently (`XAUUSD`, `GOLD`, `XAUUSD+`, `GOLDm`, …). Leave `MT5_SYMBOL` unset to auto-detect, or check the symbol in MT5's Market Watch and pin it.

## 4. Run it

All commands from the repo root.

### 4.1 Analysis only (no MT5 connection at all)

```powershell
python gold_mt5.py --analysis-only
```

The agents run, the decision is printed, and full markdown reports are saved under `~/.tradingagents/logs/reports/`.

### 4.2 Dry-run (connects to MT5, simulates the order)

```powershell
python gold_mt5.py
```

You'll see the exact order the system **would** send — symbol, volume, price, SL, TP — but nothing is sent. Use this to verify symbol detection and sizing.

### 4.3 Demo trading

Point `MT5_LOGIN`/`MT5_SERVER` at a **demo** account, then:

```powershell
# open the safety gate in the environment (new shells afterwards)
setx MT5_DRY_RUN false

python gold_mt5.py --live
```

Both gates must be open for a real order: `MT5_DRY_RUN=false` **and** `--live`. Either one alone stays simulated.

### 4.3b Paper trading & backtesting (no MT5 needed)
```powershell
# simulated fills with SL-first pessimism, persistent paper account
python gold_mt5.py --paper

# deterministic backtest + 70/30 walk-forward split (no LLM keys needed)
python gold_mt5.py --backtest
```
Paper positions live in `results_dir/gold_state.json`; every run replays fresh
candles against open positions (a candle touching both SL and TP closes at the
SL — the pessimistic assumption, documented).

The backtester runs the DETERMINISTIC engine only (no LLM): honest,
reproducible statistics without API cost. Use it before believing anything.
In-sample and out-of-sample results are reported separately so you cannot
optimize and evaluate on the same data.

### 4.4 Useful flags

```powershell
python gold_mt5.py --lots 0.01                  # fixed volume override (gates still apply)
python gold_mt5.py --risk-percent 0.5           # risk half a percent per trade
python gold_mt5.py --min-rr 2.5                 # demand better reward:risk
python gold_mt5.py --min-confidence 80          # raise the confidence floor
python gold_mt5.py --allow-short                # bearish signals open shorts (default: flatten only)
python gold_mt5.py --close-on-hold              # flatten the position on a HOLD decision
python gold_mt5.py --fast                       # cheaper/faster LLM mode (gates unchanged)
python gold_mt5.py --no-llm                     # deterministic candidate only, zero LLM calls
python gold_mt5.py --timeframe H4               # structure/SL/TP working timeframe
python gold_mt5.py --date 2026-08-28 --analysis-only   # agents on a past (point-in-time) date
python gold_mt5.py --reflect 2.0                # feed a realized +2R result into agent memory
python gold_mt5.py --debug                      # verbose traces
python gold_mt5.py --quiet                      # less logging
```

## 5. Scheduling (run it every morning)

Windows Task Scheduler can run the pipeline daily. Example — weekdays 07:00, dry-run:

```powershell
schtasks /Create /SC WEEKLY /D MON,TUE,WED,THU,FRI /TN "GoldTradingAgents" ^
  /TR "C:\Python312\python.exe C:\path\to\project\gold_mt5.py --quiet" /ST 07:00
```

Gold trades ~23h/day Sunday evening to Friday (server time), so weekday mornings are the natural cadence. Every run appends a JSON line to `~/.tradingagents/logs/gold_mt5_executions.jsonl` so you have an audit trail of what was signalled and sent.

## 6. Troubleshooting

| Symptom | Fix |
|---|---|
| `MT5NotAvailableError` | You're not on Windows or didn't `pip install MetaTrader5`. |
| `mt5.initialize() failed` | MT5 terminal not installed at the default location — set `MT5_TERMINAL_PATH` to the full `terminal64.exe` path. Only one process can own a terminal data folder at a time: close other Python scripts using MT5. |
| `mt5.login() failed` | Wrong login/password/server, or the broker requires the terminal login (leave the three vars unset and log in via the terminal UI instead). |
| `None of the gold symbols ... exist` | Open Market Watch in MT5, find your gold contract's exact name, set `MT5_SYMBOL`. |
| Order retcode `10019` (no money) | Volume too big for the account — lower `RISK_PER_TRADE` (engine sizing) or `MT5_RISK_PERCENT` (legacy executor). |
| `REFUSED: live order without a stop-loss` | Working as intended — unprotected orders are never sent. This means the plan failed level validation mid-flight. |
| Decision is always HOLD | Read `results_dir/gold_reports/*.md`: every veto names its gate (blackout, spread, session, RR, confidence...). HOLD is the intended outcome when conditions are bad. |
| Order retcode `10027` (autotrading disabled) | Enable *Allow algorithmic trading* (Tools → Options → Expert Advisors) and check the "Algo Trading" toolbar button is green. |
| Order retcode `10030` (unsupported filling) | Rare; the bridge auto-selects IOC/FOK from the symbol's filling flags. Report the symbol's filling mode if you see this. |
| No macro numbers in the fundamentals report | Set `FRED_API_KEY`. |
| Yahoo rate errors on prices | Transient; the framework retries and caches. Re-run after a minute. |

## 7. Risk disclaimer

- LLM output is **non-deterministic**: the same date can produce different reasoning on different runs — but the deterministic gates (data, levels, sizing, risk limits, blackout) are fully reproducible, and the final BUY/SELL/HOLD can only pass them, never bypass them.
- Live trading requires THREE gates: `MT5_DRY_RUN=false` **and** `--live` **and** the final confirmation (`LIVE` at the prompt, or `GOLD_LIVE_CONFIRM=YES` when scheduled). Never set all three casually.
- News and macro data are point-in-time best-effort; a run with today's date uses live data that can lag.
- Gold CFD/futures leverage can lose more than the margin posted. Use stops (this executor always attaches one), size small, and never trade money you can't afford to lose.
- This software is provided "as is" under Apache 2.0, without warranty of any kind. It is not financial advice.
