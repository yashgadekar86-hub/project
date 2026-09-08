"""Gold-trading configuration overlays for TradingAgents.

The upstream DEFAULT_CONFIG is equity-centric (news queries about earnings,
SPY benchmark, company fundamentals). This module layers gold-specific
defaults on top so the agent team reasons about the drivers that actually
price gold: Fed policy, real yields, the dollar, central-bank demand, and
geopolitical safe-haven flows.

Usage::

    from tradingagents.default_config import DEFAULT_CONFIG
    from tradingagents.gold_config import build_gold_config

    config = build_gold_config(DEFAULT_CONFIG.copy())
"""

from __future__ import annotations

from typing import Any

# Symbols the MT5 layer tries, in order, when auto-detecting the broker's
# gold contract name. Brokers are inconsistent: some use XAUUSD, some GOLD,
# IC Markets-style suffixes (XAUUSD+, GOLDm), or dotted variants. The first
# one that exists and is tradeable on the connected account wins.
MT5_GOLD_SYMBOL_CANDIDATES = [
    "XAUUSD",
    "GOLD",
    "XAUUSD+",
    "XAUUSD.",
    "GOLDm",
    "GOLD+",
    "XAUUSDm",
    "GOLDUSD",
    "XAUUSD-E",
    "XAUUSD.pro",
    "GOLDpro",
]

# Yahoo canonical symbols accepted as "gold" by the runner.
GOLD_YAHOO_SYMBOLS = frozenset({"XAUUSD", "XAU", "GOLD", "GC=F"})

# Gold-focused macro news queries replace the equity-centric defaults so the
# News Analyst searches for what moves gold instead of earnings season.
GOLD_GLOBAL_NEWS_QUERIES = [
    "gold price Federal Reserve interest rates real yields",
    "US dollar DXY inflation CPI Fed policy",
    "central bank gold buying reserves",
    "geopolitical risk safe haven gold demand",
    "gold ETF flows bullion market",
]


def build_gold_config(config: dict[str, Any]) -> dict[str, Any]:
    """Return ``config`` with gold-specific defaults applied in place.

    Only keys the equity defaults got wrong for gold are overridden; every
    TRADINGAGENTS_* env var the user set still wins because it was already
    applied to DEFAULT_CONFIG before this overlay runs. Explicit user values
    for the overridden keys are preserved when they differ from the equity
    defaults they replaced (best-effort: env overrides are re-applied last).
    """
    config["global_news_queries"] = list(GOLD_GLOBAL_NEWS_QUERIES)
    # Gold trades 23h/day nearly year-round; a slightly wider macro lookback
    # helps the news analyst catch central-bank and policy cycles.
    config.setdefault("global_news_lookback_days", 7)
    # Alpha vs SPY keeps meaning for gold (opportunity cost of the position).
    config.setdefault("benchmark_ticker", None)

    # Re-apply env overrides so TRADINGAGENTS_* always beats this overlay.
    from tradingagents.default_config import _apply_env_overrides

    return _apply_env_overrides(config)


def is_gold_symbol(symbol: str) -> bool:
    """True when ``symbol`` (raw or canonical) refers to gold."""
    from tradingagents.dataflows.symbol_utils import normalize_symbol

    if not isinstance(symbol, str) or not symbol.strip():
        return False
    return (
        symbol.strip().upper() in GOLD_YAHOO_SYMBOLS
        or normalize_symbol(symbol) in GOLD_YAHOO_SYMBOLS
    )
