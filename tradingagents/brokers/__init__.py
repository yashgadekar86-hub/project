"""Broker integrations for TradingAgents.

Currently ships a MetaTrader 5 integration focused on gold (XAUUSD) trading
on Windows:

    from tradingagents.brokers import MT5Broker, MT5Config, GoldExecutor

The ``MetaTrader5`` dependency is imported lazily so the package stays
importable on non-Windows platforms (tests, CI, analysis-only runs).
"""

from .gold_executor import (
    RATING_TO_DIRECTION,
    GoldExecutor,
    GoldExecutorConfig,
    extract_price_levels,
)
from .mt5_broker import (
    MT5Broker,
    MT5Config,
    MT5ConnectionError,
    MT5NotAvailableError,
    MT5OrderError,
    MT5SymbolError,
    OrderResult,
)

__all__ = [
    "GoldExecutor",
    "GoldExecutorConfig",
    "MT5Broker",
    "MT5Config",
    "MT5ConnectionError",
    "MT5NotAvailableError",
    "MT5OrderError",
    "MT5SymbolError",
    "OrderResult",
    "RATING_TO_DIRECTION",
    "extract_price_levels",
]
