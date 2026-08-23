"""
Plugin-based strategy architecture.

Each strategy accepts a dict of OHLC+indicator DataFrames (one per timeframe),
symbol spec, and account context, and returns zero or more `SignalOpinion`
objects. The scoring layer then ranks strategies across timeframes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import pandas as pd


@dataclass
class SignalOpinion:
    strategy_name: str
    direction: Optional[str]              # "BUY", "SELL", or None for NO TRADE
    confidence: int                       # 0-100
    entry: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    risk_reward: Optional[float] = None
    reasons: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    market_regime: Optional[str] = None
    timeframe: str = "H1"
    metadata: Dict[str, Any] = field(default_factory=dict)


class BaseStrategy:
    """Abstract base for strategies."""

    name: str = "base"
    display_name: str = "Base Strategy"
    description: str = ""
    default_params: Dict[str, Any] = {}

    def __init__(self, params: Optional[Dict[str, Any]] = None) -> None:
        self.params = {**self.default_params, **(params or {})}

    def evaluate(
        self,
        symbol: str,
        mtf_data: Dict[str, pd.DataFrame],  # timeframe -> OHLC+indicator DF
        symbol_spec: Any,
        market: Dict[str, Any],
    ) -> SignalOpinion:
        raise NotImplementedError


# Strategy registry
STRATEGY_REGISTRY: Dict[str, type] = {}


def register_strategy(cls: type) -> type:
    STRATEGY_REGISTRY[cls.name] = cls
    return cls


def get_strategy(name: str) -> type:
    if name not in STRATEGY_REGISTRY:
        raise KeyError(f"Unknown strategy: {name}. Available: {list(STRATEGY_REGISTRY)}")
    return STRATEGY_REGISTRY[name]


def list_strategies() -> List[Dict[str, Any]]:
    return [
        {
            "name": cls.name,
            "display_name": cls.display_name,
            "description": cls.description,
            "default_params": cls.default_params,
        }
        for cls in STRATEGY_REGISTRY.values()
    ]
