"""Strategies package — auto-imports strategies so they register themselves."""
from trading_engine.strategies.base import (
    BaseStrategy, SignalOpinion, STRATEGY_REGISTRY,
    register_strategy, get_strategy, list_strategies,
)

# Import all strategies to trigger @register_strategy
from trading_engine.strategies import (  # noqa: F401
    trend_following,
    breakout,
    pullback,
    mean_reversion,
    market_structure,
)

__all__ = [
    "BaseStrategy", "SignalOpinion", "STRATEGY_REGISTRY",
    "register_strategy", "get_strategy", "list_strategies",
]
