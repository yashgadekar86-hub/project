# AI Forex Command Center — Architecture

## High-level

```
[Browser UI (Next.js 14)]  ──── HTTPS / WSS ────►  [FastAPI Backend]
                                                        │
                                ┌───────────────────────┼────────────────────────┐
                                ▼                       ▼                        ▼
                         [Trading Engine]         [AI Engine]            [PostgreSQL + Redis]
                         • MT5 connector         • Feature eng.         • Users / Accounts / Symbols
                         • Indicators            • Regime classifier   • Signals / Orders / Positions
                         • Strategies            • ML models (XGB/RF)  • Trades / Journal / Backtests
                         • Risk engine           • LLM explainer       • Cache / Pub-Sub (Redis)
                         • Position sizer        • Walk-forward
                         • Execution validator
                                │
                                ▼
                          [MetaTrader 5]  ──►  [Broker (e.g. FortressFX)]
```

## Modules

### Frontend (`frontend/`)
- Next.js 14 App Router + TypeScript
- Tailwind CSS + shadcn/ui components (Radix primitives)
- Recharts (analytics) + Lightweight Charts for candlestick views (plug-in)
- WebSocket client for live ticks, signals, positions
- Zustand for auth state

### Backend (`backend/`)
- FastAPI + Pydantic v2
- Async SQLAlchemy + Alembic migrations
- JWT auth + bcrypt password hashing + Fernet encryption for broker credentials
- Redis for cache, pub/sub, and kill-switch state
- Celery for backtests/ML training (background tasks)

### Trading Engine (`trading_engine/`)
- `mt5/connector.py` — typed wrapper around MetaTrader5 (Windows native)
- `indicators/technical.py` — EMA/SMA/ADX/RSI/MACD/Stoch/CCI/ATR/Bollinger/swings/BOS/CHoCH
- `strategies/` — 5 plugin strategies (trend, breakout, pullback, mean-reversion, structure)
- `risk/engine.py` — non-bypassable pre-trade checks (kill switch, MT5, data, spread, news, session, daily loss, drawdown, max trades, consecutive-loss cooldown, SL/TP, RR, duplicate, margin, confidence, etc.)
- `risk/position_sizer.py` — broker-contract-aware lot sizing (NEVER hard-coded pip values)
- `execution/executor.py` — 14-check validator → paper or MT5 live execution with fill verification
- `utils/sessions.py` — Sydney / Tokyo / London / New York detection

### AI Engine (`ai_engine/`)
- `features/feature_engine.py` — multi-timeframe feature generation, no look-ahead
- `models/baseline.py` — LogReg / RF / GBM / XGBoost wrappers
- `regime/regime.py` — 11-regime quantitative classifier
- `inference/pipeline.py` — full end-to-end signal generation
- `inference/explainer.py` — deterministic + optional-LLM explanation (LLM never sets prices)

### Backtesting (`backtesting/`)
- Event-driven backtester reusing production strategies + Risk Engine
- Equity/drawdown curves, Sharpe/Sortino, win rate, PF, expectancy, consecutive wins/losses
- Walk-forward testing with stability detection

## Safety Architecture

```
AI Signal → Risk Engine → Execution Validator → MT5
                ↑
        (kill switch, daily loss, drawdown, spread,
         news, confidence, RR, max trades, exposure,
         cooldown, data freshness, margin, SL/TP,
         duplicate, session, MT5 connection)
```

The Risk Engine is independent from the AI layer. A 99% confidence BUY from AI is
rejected if a single hard rule fails. Live trading requires:

1. User explicitly enables PAPER→LIVE in Settings
2. User explicitly activates live trading per MT5 account
3. Every order passes all pre-trade checks at execution time
4. The prominent STOP ALL TRADING kill switch works even when all else fails

## Failure-safe behavior

| Failure mode            | System response                                     |
|-------------------------|-----------------------------------------------------|
| MT5 disconnects         | New trading halted; positions unchanged              |
| Stale market data       | New trading halted                                  |
| Risk engine throws      | New trading halted (fail-closed)                    |
| AI service fails        | AI signals stop; manual trading still protected     |
| DB fails                | Live execution halted                               |
| Spread exceeds limit    | Individual trade rejected                           |
| Kill switch active      | All new orders blocked; optional position flatten   |
| Unexpected MT5 response | No blind retries; error logged; order not duplicated |
