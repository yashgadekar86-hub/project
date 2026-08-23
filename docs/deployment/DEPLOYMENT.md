# Deployment Guide

## Quick Start (Local Development)

```bash
# 1. Start Postgres + Redis
docker compose -f docker/docker-compose.yml up -d postgres redis

# 2. Backend
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
alembic upgrade head                          # create tables
uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload

# 3. Frontend
cd ../frontend
npm install
npm run dev
```

Open:
- Frontend: http://localhost:3000
- API docs: http://localhost:8000/docs

### First-run setup

1. Register an account at http://localhost:3000/register
2. The first user can be promoted to ADMIN manually via DB:
   ```sql
   UPDATE users SET role = 'ADMIN' WHERE username = 'yourname';
   ```
3. Navigate to **MT5 Connection** and connect your broker.
4. The system defaults to PAPER mode. **Live trading is never enabled by default.**

## Production Deployment (Docker)

1. Copy `.env.example` → `.env` and set strong secrets (`APP_SECRET_KEY`,
   `JWT_SECRET_KEY`, `CREDENTIAL_ENCRYPTION_KEY` — generate one with
   `GET /internal/generate-fernet-key`).
2. Bring up all services:
   ```bash
   docker compose -f docker/docker-compose.yml up -d --build
   ```
3. Run migrations:
   ```bash
   docker compose -f docker/docker-compose.yml exec backend \
     python -m alembic upgrade head
   ```
4. The frontend is available on port 3000, backend on 8000.
   Put nginx/Caddy in front with TLS for production.

### MT5 in production

MetaTrader5 is Windows-native. Options:

1. **Recommended**: Run the backend on a Windows Server VM/PC with MT5 installed
   and logged in 24/7. Point Postgres/Redis to shared instances.
2. Use the optional MQL5 Expert Advisor bridge (`mql5/experts/AIFXBridge.mq5`) —
   attach it to a chart in MT5; it listens on a TCP socket for JSON orders.
   (The EA ships as a starting point; full EA code is in `mql5/`.)
3. Run the backend on Linux and let it talk to MT5 on a separate Windows
   machine via the bridge.

### Environment variables (critical)

- `APP_SECRET_KEY`, `JWT_SECRET_KEY`: set to long random strings
- `CREDENTIAL_ENCRYPTION_KEY`: Fernet key for broker password encryption
- `POSTGRES_*`, `REDIS_URL`: connection info
- `DEFAULT_TRADING_MODE=paper` is hard-coded to refuse `live` at boot; never change this to default to live
- `LIVE_TRADING_REQUIRES_EXPLICIT_ACTIVATION=true` must remain true

### Before going live checklist

- [ ] All unit tests pass (`pytest tests/`)
- [ ] Integration tests pass
- [ ] Backtested on ≥ 1 year of historical data across multiple pairs
- [ ] Walk-forward validation shows stable out-of-sample performance
- [ ] Paper-traded profitably for ≥ 4 weeks
- [ ] Conservative risk settings (≤ 0.5%/trade, ≤ 2% daily loss, ≤ 5% drawdown)
- [ ] Kill switch reachable and tested
- [ ] MT5 terminal is hosted on a reliable machine (VPS, no sleep/hibernate)
- [ ] Monitoring/alerts configured (Telegram/email)
- [ ] Disclaimer accepted; you understand AI/backtested performance does not guarantee future results
