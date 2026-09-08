"""Deterministic Gold/XAUUSD trading layer for TradingAgents.

Package layout (each module is independently testable and LLM-free):

* :mod:`config`      — every knob + env loading + live-trade latch
* :mod:`models`      — shared dataclasses (plans, decisions, provenance)
* :mod:`marketdata`  — MT5-primary / Yahoo-reference multi-timeframe candles
* :mod:`indicators`  — deterministic TA (EMA/SMA/RSI/MACD/Stoch/ATR/BB/VWMA)
* :mod:`structure`   — swings, S/R clusters, BOS/CHOCH, trend
* :mod:`timeframes`  — the D1>H4>H1>M15>M5 decision hierarchy
* :mod:`fundamentals`— gold macro drivers with provenance/UNKNOWN semantics
* :mod:`news`        — structured news, freshness filter, deterministic score
* :mod:`news_blackout`— CPI/NFP/FOMC blackout calendar (LLM cannot override)
* :mod:`sessions`    — Asian/London/NY/Overlap session gate (UTC-defined)
* :mod:`levels`      — structural SL / honest TP construction (min RR)
* :mod:`sizing`      — risk-based volume from REAL broker symbol economics
* :mod:`risk_engine` — the FINAL deterministic authority (BUY/SELL/HOLD)
* :mod:`paper`       — paper trading (SL-first conservative fills)
* :mod:`backtest`    — look-ahead-safe backtesting + walk-forward split
* :mod:`state`       — persistent daily counters / idempotency state
* :mod:`report`      — auditable markdown reports

The golden rule enforced here: **LLMs reason; deterministic code decides.**
No LLM output can enable live trading, override a gate, or inflate sizing.
"""

from .config import GoldConfig
from .models import (
    AccountContext,
    Bias,
    BrokerSymbolSpec,
    FinalDecision,
    MarketContext,
    MultiTimeframeView,
    TradePlan,
)
from .risk_engine import (
    RATING_TO_CANDIDATE,
    DeterministicRiskEngine,
    candidate_from_rating,
    signal_id,
)

__all__ = [
    "GoldConfig",
    "AccountContext",
    "Bias",
    "BrokerSymbolSpec",
    "FinalDecision",
    "MarketContext",
    "MultiTimeframeView",
    "TradePlan",
    "DeterministicRiskEngine",
    "RATING_TO_CANDIDATE",
    "candidate_from_rating",
    "signal_id",
]
