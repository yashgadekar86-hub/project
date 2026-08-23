# AI Forex Command Center

> **Production-grade AI-powered Forex trading platform for MetaTrader 5**

[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://python.org)
[![Next.js](https://img.shields.io/badge/Next.js-14-black.svg)](https://nextjs.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110+-green.svg)](https://fastapi.tiangolo.com)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-15-blue.svg)](https://postgresql.org)
[![Redis](https://img.shields.io/badge/Redis-7-red.svg)](https://redis.io)
[![License](https://img.shields.io/badge/License-Proprietary-yellow.svg)]()

## ⚠️ CRITICAL DISCLAIMER

Trading foreign exchange carries a high level of risk and may not be suitable for all investors. Past performance (including backtested, AI-generated, or paper-traded performance) **does not guarantee future results**. This software is provided for educational and research purposes. You are solely responsible for any trading decisions and resulting financial outcomes. **Never trade with money you cannot afford to lose.**

- Live trading requires **explicit user activation** — it is never enabled by default.
- The AI **never** has unrestricted authority to place trades. All signals pass through a non-bypassable Risk Engine and Execution Validator.
- Default configuration is **conservative** (0.5% risk per trade, max 2% daily loss, etc.).

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                    Next.js Frontend (UI)                    │
│   Dashboard · Markets · AI Signals · Trading · Journal      │
└───────────────────────┬─────────────────────────────────────┘
                        │ REST + WebSockets (JWT)
┌───────────────────────▼─────────────────────────────────────┐
│                   FastAPI Backend (API)                     │
│   Auth · Signals · Orders · Backtest · Settings · Alerts    │
└─────────┬────────────────────┬───────────────────────────────┘
          │                    │
┌─────────▼────────┐   ┌──────▼──────┐   ┌───────────────────┐
│  Trading Engine  │   │ AI Engine   │   │ PostgreSQL + Redis │
│  MT5 · Risk ·    │   │ Features ·  │   │ Users · Trades ·   │
│  Execution ·     │   │ ML Models · │   │ Journal · Signals  │
│  Strategies      │   │ Regime      │   │ Cache · Pub/Sub    │
└────────┬─────────┘   └─────────────┘   └───────────────────┘
         │
┌────────▼─────────┐
│  MetaTrader 5    │
│  (FortressFX or  │
│   any MT5 broker)│
└──────────────────┘
```

## Modes of Operation

| Mode | Description | Real Orders? |
|------|-------------|--------------|
| 🔵 **BACKTEST** | Historical strategy validation | No |
| 🟡 **PAPER** | Simulated live trading | No |
| 🟢 **LIVE** | Real execution via MT5 | Yes — explicit activation required |

The UI displays the current mode prominently at all times.

## Project Structure

```
ai-forex-trading-platform/
├── frontend/              # Next.js 14 (App Router) + TypeScript + Tailwind + shadcn/ui
├── backend/               # FastAPI API (auth, settings, signals, backtest, alerts)
├── trading-engine/        # MT5 connector, risk engine, execution, strategies, indicators
├── ai-engine/             # Feature engineering, ML training, inference, regime detection
├── backtesting/           # Vectorized event-driven backtester + walk-forward
├── mql5/                  # MQL5 Expert Advisor bridge (optional advanced integration)
├── tests/                 # Unit + integration tests (pytest)
├── docker/                # Docker Compose + service configs
├── docs/                  # Architecture, MT5 setup, broker config, deployment guides
└── scripts/               # Dev/ops helper scripts
```

## Quick Start (Development)

### Prerequisites
- Python 3.11+
- Node.js 18+ / pnpm (or npm)
- Docker & Docker Compose
- MetaTrader 5 terminal installed (for live/paper trading)
- A funded/demo MT5 account (e.g., FortressFX, if MT5-compatible)

### 1. Clone & Install Backend
```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Install Frontend
```bash
cd frontend
npm install
```

### 3. Start Infrastructure
```bash
docker compose -f docker/docker-compose.yml up -d postgres redis
```

### 4. Run Database Migrations
```bash
cd backend
alembic upgrade head
```

### 5. Start Services
```bash
# Backend (API + WebSocket)
cd backend && uvicorn api.main:app --reload --host 0.0.0.0 --port 8000

# Frontend
cd frontend && npm run dev
```

### 6. Configure MT5 Connection
Open the UI at `http://localhost:3000/mt5-connection` and enter your MT5 account, password, server, and broker terminal path. Start in **PAPER** mode first.

## Safety Features (Non-Bypassable)

- **Kill Switch**: One-button emergency stop that halts all new orders
- **Risk Engine**: Enforces max % per trade, daily loss, drawdown, exposure, spread, R:R
- **Position Sizer**: Calculates lot size from equity/risk/SL distance — never hard-coded pip values
- **Mode separation**: BACKTEST / PAPER / LIVE are strictly separated
- **Live trading gating**: Requires explicit activation plus passing pre-trade checklist (14 checks)
- **Safe failure**: MT5 disconnect / stale data / risk engine failure → stop new trading
- **Audit log**: Every decision, signal, order, and result is persisted

## Default Safety Settings

| Setting | Default |
|---------|---------|
| Risk per trade | 0.5% |
| Max daily loss | 2% → auto-disable for the day |
| Max drawdown | 5% → emergency halt |
| Max simultaneous trades | 3 |
| Min AI confidence | 70% |
| Min risk/reward | 1:2 |
| Max spread (for major pairs) | 3 pips (configurable) |
| Max consecutive losses | 3 → cooldown |

All settings are configurable via the Settings UI.

## Documentation

- [Architecture Document](docs/architecture/ARCHITECTURE.md)
- [MT5 Setup Guide](docs/mt5-setup/MT5_SETUP.md)
- [Broker Configuration (incl. FortressFX)](docs/broker-configuration/BROKER_CONFIG.md)
- [Deployment Guide](docs/deployment/DEPLOYMENT.md)
- [API Docs](http://localhost:8000/docs) (when backend is running)

## Testing

```bash
cd backend && pytest ../tests -v
```

## License & Liability

This software is provided **AS IS** without warranties. Trading involves substantial risk of loss. The authors and contributors accept no liability for any financial loss incurred through use of this software. See full disclaimer in [LICENSE](LICENSE).
