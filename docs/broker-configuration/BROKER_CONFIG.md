# Broker Configuration

The AI Forex Command Center is broker-agnostic: any MT5-compatible broker works
(including FortressFX, IC Markets, Pepperstone, etc.).

## How detection works

On `/mt5-connection` the system:

1. Calls `mt5.initialize()` with the provided server/login/password/path.
2. Reads `account_info()` for broker, balance, equity, currency, leverage.
3. Calls `symbols_get()` to enumerate symbols.
4. For each symbol (up to a safe limit), calls `symbol_info(sym)` to pull:
   - `digits`, `point` → pip size
   - `trade_contract_size` (lot size, usually 100,000 for FX)
   - `volume_min`, `volume_max`, `volume_step`
   - `trade_tick_size`, `trade_tick_value` → per-tick P/L for correct position sizing
   - `currency_base`, `currency_profit`, `currency_margin`
   - `stops_level`, `freeze_level` → minimum SL/TP distance enforced by broker
   - `spread`, `spread_float`, `trade_mode`, `trade_exemode`
5. Stores these in the `symbols` table and in-memory cache for Risk Engine.

## Adding a new broker

No code changes are needed. Simply connect with the new broker's credentials.
If you want to tag a broker for reference, insert a row into the `brokers` table
(or use the UI after connecting):

```sql
INSERT INTO brokers (name, server_name, mt5_compatible)
VALUES ('MyBroker', 'MyBroker-Live', true)
ON CONFLICT (name) DO NOTHING;
```

## Symbol suffixes

Some brokers add suffixes (e.g. `EURUSDm`, `EURUSD.a`, `EURUSD.`). The system
matches by exact MT5 name. When using the UI watchlist or strategy config, use
the broker's exact symbol name as shown in the Detected Symbols table.

## Safety per broker

Brokers differ in:
- Minimum stop distance (`stops_level`)
- Lot step (0.01 for most, 0.1 for some micro brokers)
- Execution mode (instant, market, exchange)
- Margin currency

All of these are read at connect time and fed into the Position Size Calculator
and Risk Engine, so behavior is correct per broker without code changes.
