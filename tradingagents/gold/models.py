"""Core data models for the deterministic Gold/XAUUSD system.

These are plain dataclasses (no LLM, no I/O) shared by every module:

* indicators / structure / timeframes produce :class:`TechnicalSnapshot`
* fundamentals / news produce :class:`GoldFundamentals` and news lists
* the risk engine consumes everything and emits a :class:`FinalDecision`

Every market dataset carries a :class:`DataProvenance` so reports can always
state WHERE a number came from (Phase 2: broker data is primary for trading,
``GC=F`` is research/reference only and must be clearly labelled).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Literal

import pandas as pd

# ---------------------------------------------------------------------------
# Canonical vocabulary
# ---------------------------------------------------------------------------

Side = Literal["BUY", "SELL", "HOLD"]

# A valid candle frame: DatetimeIndex (tz-aware) + these lowercase columns.
REQUIRED_COLUMNS = ("open", "high", "low", "close", "volume")


class Bias(str, Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"

    def __str__(self) -> str:  # nicer printing
        return self.value


# Canonical timeframe labels (broker-style). Mapped to minutes elsewhere.
TIMEFRAME_MINUTES = {
    "M1": 1, "M5": 5, "M15": 15, "M30": 30,
    "H1": 60, "H4": 240, "D1": 1440, "W1": 10080,
}


def timeframe_minutes(tf: str) -> int:
    try:
        return TIMEFRAME_MINUTES[tf.upper()]
    except KeyError as exc:
        raise ValueError(f"Unknown timeframe {tf!r}") from exc


# ---------------------------------------------------------------------------
# Provenance / data labelling (Phase 2)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DataProvenance:
    """Where a dataset came from, so it can be labelled in every report."""

    source: str            # e.g. "MT5_BROKER", "YAHOO", "FRED", "STATIC"
    symbol: str            # e.g. "XAUUSD" or "GC=F"
    timeframe: str = ""    # e.g. "D1"; "" for non-OHLC data
    fetched_at: datetime | None = None

    def label(self) -> str:
        tf = f" {self.timeframe}" if self.timeframe else ""
        return f"[{self.source}:{self.symbol}{tf}]"


def validate_candles(df: pd.DataFrame, min_rows: int = 1) -> pd.DataFrame:
    """Validate and normalise an OHLCV frame.

    Returns a copy with lowercase column names and a sorted, unique,
    tz-aware DatetimeIndex. Raises ``ValueError`` when the frame is not
    usable — callers must convert that into DATA_UNAVAILABLE / HOLD, never a
    fabricated result (Phase 4).
    """
    if df is None or len(df) == 0:
        raise ValueError("empty candle set")
    out = df.copy()
    out.columns = [str(c).lower() for c in out.columns]
    missing = [c for c in REQUIRED_COLUMNS if c not in out.columns]
    if missing:
        raise ValueError(f"candles missing columns: {missing}")
    if not isinstance(out.index, pd.DatetimeIndex):
        try:
            out.index = pd.to_datetime(out.index)
        except Exception as exc:  # noqa: BLE001
            raise ValueError("candle index is not datetime") from exc
    if out.index.tz is None:
        out.index = out.index.tz_localize("UTC")
    out = out[~out.index.duplicated(keep="last")].sort_index()
    for col in ("open", "high", "low", "close"):
        if not pd.api.types.is_numeric_dtype(out[col]):
            raise ValueError(f"column {col} is not numeric")
    if len(out) < min_rows:
        raise ValueError(f"need >= {min_rows} candles, got {len(out)}")
    return out[list(REQUIRED_COLUMNS)]


# ---------------------------------------------------------------------------
# Market structure / technicals (Phases 3, 4)
# ---------------------------------------------------------------------------


@dataclass
class Swing:
    index: int
    time: datetime
    price: float
    kind: Literal["high", "low"]


@dataclass
class MarketStructure:
    """Deterministic structure read for ONE timeframe."""

    trend: Bias = Bias.NEUTRAL
    last_event: str = "UNKNOWN"          # "BOS" | "CHOCH" | "UNKNOWN"
    last_event_dir: Bias = Bias.NEUTRAL
    support: list[float] = field(default_factory=list)
    resistance: list[float] = field(default_factory=list)
    swing_highs: list[Swing] = field(default_factory=list)
    swing_lows: list[Swing] = field(default_factory=list)
    atr: float | None = None
    atr_pct: float | None = None         # ATR / close * 100
    ema_stack_bullish: bool | None = None
    note: str = ""


@dataclass
class TechnicalSnapshot:
    """Indicator + structure bundle for one timeframe."""

    timeframe: str
    candles: int
    indicators: dict[str, float | None] = field(default_factory=dict)
    structure: MarketStructure = field(default_factory=MarketStructure)
    bias: Bias = Bias.NEUTRAL
    usable: bool = True                  # False => DATA_UNAVAILABLE for this TF
    reason: str = ""
    provenance: DataProvenance | None = None


@dataclass
class MultiTimeframeView:
    """The hierarchical read across all timeframes (Phase 3)."""

    per_tf: dict[str, TechnicalSnapshot] = field(default_factory=dict)
    macro_trend: Bias = Bias.NEUTRAL     # D1
    major_structure: Bias = Bias.NEUTRAL # H4
    bias: Bias = Bias.NEUTRAL            # H1
    setup: Bias = Bias.NEUTRAL           # M15
    entry_confirmation: Bias = Bias.NEUTRAL  # M5
    aligned_direction: Bias = Bias.NEUTRAL
    alignment_score: float = 0.0         # 0..1, NOT a naive vote
    description: str = ""
    usable: bool = True


# ---------------------------------------------------------------------------
# Fundamentals / news (Phases 5, 6, 7)
# ---------------------------------------------------------------------------


@dataclass
class GoldFundamentalPoint:
    name: str                       # e.g. "real_yield_10y", "dollar_index"
    value: float | None = None      # None => UNKNOWN, never fabricated
    unit: str = ""
    timestamp: datetime | None = None
    source: str = "UNKNOWN"
    freshness_hours: float | None = None

    @property
    def available(self) -> bool:
        return self.value is not None

    def display(self) -> str:
        if not self.available:
            return f"{self.name}: UNKNOWN"
        ts = self.timestamp.isoformat() if self.timestamp else "no-ts"
        return f"{self.name}={self.value:.4f}{(' ' + self.unit) if self.unit else ''} ({self.source}, {ts})"


@dataclass
class GoldFundamentals:
    points: list[GoldFundamentalPoint] = field(default_factory=list)
    supportive: Bias = Bias.NEUTRAL
    note: str = ""

    def get(self, name: str) -> GoldFundamentalPoint | None:
        for p in self.points:
            if p.name == name:
                return p
        return None


@dataclass
class NewsItem:
    headline: str
    source: str = "UNKNOWN"
    timestamp: datetime | None = None
    summary: str = ""
    sentiment: float = 0.0          # -1..+1 (gold positive = +)
    gold_impact: Literal["HIGH", "MEDIUM", "LOW", "UNKNOWN"] = "UNKNOWN"
    confidence: float = 0.0         # 0..1
    age_hours: float | None = None


@dataclass
class HighImpactEvent:
    code: str                       # e.g. "CPI", "NFP", "FOMC"
    name: str
    timestamp: datetime           # tz-aware event time
    source: str = "CALENDAR"


@dataclass
class BlackoutStatus:
    active: bool
    reason: str = ""
    event: HighImpactEvent | None = None
    minutes_to_event: float | None = None


# ---------------------------------------------------------------------------
# Trade plan / decision (Phases 9-14, 21)
# ---------------------------------------------------------------------------


@dataclass
class TradePlan:
    """A fully-specified, deterministic trade proposal."""

    side: Side = "HOLD"
    entry: float | None = None
    stop_loss: float | None = None
    take_profits: list[float] = field(default_factory=list)  # TP1..TP3
    stop_source: str = "UNKNOWN"    # "STRUCTURE" | "ATR" | "TRADER" | "NONE"
    tp_source: str = "UNKNOWN"      # "STRUCTURE" | "ATR_MULT" | "NONE"
    atr: float | None = None
    rr: float | None = None         # reward:risk of TP1
    risk_distance: float | None = None
    volume: float | None = None
    risk_amount: float | None = None
    signal_id: str = ""
    valid: bool = False
    invalid_reason: str = ""

    @property
    def tp1(self) -> float | None:
        return self.take_profits[0] if self.take_profits else None


@dataclass
class CheckResult:
    name: str
    passed: bool
    reason: str = ""
    value: Any = None


@dataclass
class ConfidenceBreakdown:
    scores: dict[str, float] = field(default_factory=dict)   # weighted points
    weights: dict[str, float] = field(default_factory=dict)
    total: float = 0.0

    def display(self) -> str:
        lines = [
            f"{k.title():>12}: {self.scores.get(k, 0.0):4.1f}/{self.weights.get(k, 0.0):4.1f}"
            for k in self.weights
        ]
        lines.append(f"{'Total':>12}: {self.total:4.1f}/100")
        return "\n".join(lines)


@dataclass
class FinalDecision:
    """Output of the deterministic risk engine (Phases 10, 21)."""

    action: Side = "HOLD"
    checks: list[CheckResult] = field(default_factory=list)
    confidence: ConfidenceBreakdown | None = None
    plan: TradePlan | None = None
    reasons: list[str] = field(default_factory=list)
    vetoed_by: list[str] = field(default_factory=list)   # gates that blocked
    timestamp: datetime | None = None

    @property
    def tradeable(self) -> bool:
        return self.action in ("BUY", "SELL")

    def summary(self) -> str:
        head = f"FINAL DECISION: {self.action}"
        if self.vetoed_by:
            head += f"  (vetoed by: {', '.join(self.vetoed_by)})"
        return head


# ---------------------------------------------------------------------------
# Account / market context handed to the risk engine
# ---------------------------------------------------------------------------


@dataclass
class BrokerSymbolSpec:
    """Actual broker symbol economics — NEVER assume 1 lot = 100 oz (Phase 14)."""

    symbol: str
    contract_size: float = 100.0
    tick_size: float = 0.01
    tick_value: float = 0.0          # quote->account currency per tick per lot
    point: float = 0.01
    digits: int = 2
    volume_min: float = 0.01
    volume_max: float = 100.0
    volume_step: float = 0.01
    stops_level_points: float = 0.0  # min distance for SL/TP in points
    freeze_level_points: float = 0.0
    spread_points: float | None = None

    def stops_level_price(self) -> float:
        return self.stops_level_points * (self.point or 0.0)

    def spread_price(self) -> float | None:
        if self.spread_points is None:
            return None
        return self.spread_points * (self.point or 0.0)


@dataclass
class AccountContext:
    equity: float = 0.0
    balance: float = 0.0
    currency: str = "USD"
    open_positions: int = 0
    open_direction: Side = "HOLD"      # net current exposure
    realized_pnl_today: float = 0.0    # for daily-loss gate
    trades_today: int = 0
    consecutive_losses: int = 0
    margin_free: float | None = None   # for the margin gate (live/dry-run)
    last_signal_id: str = ""
    last_signal_time: datetime | None = None


@dataclass
class MarketContext:
    bid: float | None = None
    ask: float | None = None
    spread_points: float | None = None
    session: str = "UNKNOWN"
    session_open: bool = False

    @property
    def mid(self) -> float | None:
        if self.bid is None or self.ask is None:
            return None
        return (self.bid + self.ask) / 2.0

    @property
    def spread_price(self) -> float | None:
        if self.bid is None or self.ask is None:
            return None
        return self.ask - self.bid
