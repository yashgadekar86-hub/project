"""Deterministic Gold/XAUUSD system configuration.

Every knob the master spec calls out lives here with a safe default. Values are
loaded from the environment (``.env``) via :meth:`GoldConfig.from_env`, and any
of them can be overridden programmatically by passing keyword arguments.

Design rules honoured throughout:

* **Fail-safe defaults.** Anything that could enable risk (live trading,
  shorting, trading through a news blackout, oversized risk) defaults OFF.
* **Deterministic gates are NOT overridable by LLM output.** This dataclass is
  built from the environment / CLI only. The LLM never constructs it.
* **No hard-coded timezone.** Session times are stored in UTC offsets; the
  active IANA timezone is configurable (default ``UTC``) and never assumes IST.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

_TRUE = {"1", "true", "yes", "on", "y", "t"}
_FALSE = {"0", "false", "no", "off", "n", "f", ""}


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    v = raw.strip().lower()
    if v in _TRUE:
        return True
    if v in _FALSE:
        return False
    # An unrecognised value must not silently become the default — fail loud.
    raise ValueError(f"Env {name}={raw!r} is not a boolean.")


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"Env {name}={raw!r} is not a number.") from exc


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"Env {name}={raw!r} is not an integer.") from exc


def _env_str(name: str, default: str) -> str:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip()


def _env_csv(name: str, default: list[str]) -> list[str]:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return list(default)
    return [p.strip() for p in raw.split(",") if p.strip()]


@dataclass
class GoldConfig:
    """All deterministic parameters for the Gold/XAUUSD system.

    Grouped by concern; see the master spec phase numbers in comments.
    """

    # -- Symbol / data (Phase 2) -------------------------------------------
    symbol: str = "XAUUSD"                 # broker-side symbol (configurable)
    reference_symbol: str = "GC=F"         # research/reference feed (Yahoo)
    symbol_candidates: list[str] = field(
        default_factory=lambda: [
            "XAUUSD", "GOLD", "XAUUSD+", "GOLDm", "XAUUSDm", "XAUUSD.",
            "GOLD+", "XAUUSDc", "GOLDc",
        ]
    )

    # -- Timeframes (Phase 3) ----------------------------------------------
    # Ordered highest -> lowest. Used for the decision hierarchy.
    timeframes: list[str] = field(
        default_factory=lambda: ["D1", "H4", "H1", "M15", "M5"]
    )
    # How many candles of warm-up we require before computing indicators.
    min_candles: int = 60
    # The timeframe whose structure drives SL/TP construction (default H1).
    working_timeframe: str = "H1"

    # -- Risk limits (Phases 14, 15) ---------------------------------------
    risk_per_trade: float = 0.5            # % of equity risked per trade
    max_daily_loss: float = 2.0            # % of equity; no trades after hit
    max_open_positions: int = 1
    max_trades_per_day: int = 3
    max_consecutive_losses: int = 4

    # -- Reward / stops (Phases 11, 12, 13) --------------------------------
    min_rr: float = 2.0                    # minimum reward:risk
    preferred_rr_max: float = 3.0          # beyond this TP is suspect
    atr_sl_multiplier: float = 1.5         # fallback SL distance in ATRs
    min_sl_atr_multiple: float = 0.5       # SL must be >= this many ATRs away
    tp_levels: int = 1                     # 1..3 -> TP1[, TP2[, TP3]]

    # -- Gating filters (Phases 16, 17, 18, 19) ---------------------------
    max_spread_points: float = 45.0        # broker points; HOLD if exceeded
    max_spread_price: float | None = None  # optional absolute price distance
    atr_percentile_cap: float = 99.0       # reject extreme volatility above
    atr_percentile_floor: float = 1.0      # reject dead/illiquid below
    allowed_sessions: list[str] = field(
        default_factory=lambda: ["London", "NewYork", "Overlap"]
    )
    timezone: str = "UTC"                  # IANA name; NEVER hard-coded IST

    # -- Confidence (Phase 19) ---------------------------------------------
    min_confidence: float = 75.0           # below -> HOLD
    weights: dict[str, float] = field(
        default_factory=lambda: {
            "technical": 30.0,
            "fundamental": 20.0,
            "news": 15.0,
            "sentiment": 10.0,
            "structure": 15.0,
            "rr": 10.0,
        }
    )

    # -- News blackout (Phase 7) -------------------------------------------
    news_blackout_before_min: int = 30
    news_blackout_after_min: int = 30
    # An explicit, operator-controlled override. The LLM can NEVER set this.
    allow_trade_during_blackout: bool = False
    # Optional JSON file with exact event times (CPI/PPI/GDP/speeches).
    event_calendar_path: str = ""
    # Event codes treated as high impact for blackout purposes.
    blackout_events: list[str] = field(
        default_factory=lambda: [
            "CPI", "NFP", "FOMC", "FED_RATE", "POWELL", "PPI", "GDP",
            "NONFARM", "UNEMPLOYMENT",
        ]
    )

    # -- Direction / execution (Phases 22, 23, 24, 25) --------------------
    allow_short: bool = False
    dry_run: bool = True                   # hard default; live needs 2 gates
    require_sl: bool = True                # never send an unprotected order
    dedupe_window_min: int = 60            # same signal suppressed within this

    # -- AI cost control (Phase 30) ----------------------------------------
    fast_mode: bool = False                # reduce expensive LLM calls
    debate_rounds: int = 1
    risk_rounds: int = 1

    # -- Paper / backtest (Phases 27, 28) ----------------------------------
    paper_starting_balance: float = 10_000.0
    paper_commission_per_lot: float = 0.0
    paper_slippage_points: float = 0.0

    # -- Live safety latch (Phase 22/37) -----------------------------------
    # Set true ONLY when the operator passes --live AND MT5_DRY_RUN=false.
    # The executor re-checks this; it is never derived from LLM output.
    live_enabled: bool = False

    # ------------------------------------------------------------------ #
    @classmethod
    def from_env(cls, **overrides: Any) -> GoldConfig:
        """Build a config from GOLD_*/MT5_* env vars plus explicit overrides."""
        cfg = cls(
            symbol=_env_str("MT5_SYMBOL", "XAUUSD"),
            reference_symbol=_env_str("GOLD_REFERENCE_SYMBOL", "GC=F"),
            risk_per_trade=_env_float("RISK_PER_TRADE", 0.5),
            max_daily_loss=_env_float("MAX_DAILY_LOSS", 2.0),
            max_open_positions=_env_int("MAX_OPEN_POSITIONS", 1),
            max_trades_per_day=_env_int("MAX_TRADES_PER_DAY", 3),
            max_consecutive_losses=_env_int("MAX_CONSECUTIVE_LOSSES", 4),
            min_rr=_env_float("MIN_RR", 2.0),
            atr_sl_multiplier=_env_float("ATR_SL_MULTIPLIER", 1.5),
            max_spread_points=_env_float("MAX_SPREAD", 45.0),
            timezone=_env_str("GOLD_TIMEZONE", "UTC"),
            min_confidence=_env_float("MIN_CONFIDENCE", 75.0),
            news_blackout_before_min=_env_int("NEWS_BLACKOUT_BEFORE", 30),
            news_blackout_after_min=_env_int("NEWS_BLACKOUT_AFTER", 30),
            allow_trade_during_blackout=_env_bool("ALLOW_TRADE_DURING_BLACKOUT", False),
            allow_short=_env_bool("ALLOW_SHORT", False),
            dry_run=_env_bool("MT5_DRY_RUN", True),
            fast_mode=_env_bool("GOLD_FAST_MODE", False),
            debate_rounds=_env_int("TRADINGAGENTS_MAX_DEBATE_ROUNDS", 1),
            risk_rounds=_env_int("TRADINGAGENTS_MAX_RISK_ROUNDS", 1),
            paper_starting_balance=_env_float("GOLD_PAPER_BALANCE", 10_000.0),
            event_calendar_path=_env_str("GOLD_EVENT_CALENDAR", ""),
            working_timeframe=_env_str("GOLD_WORKING_TIMEFRAME", "H1"),
            allowed_sessions=_env_csv(
                "ALLOWED_SESSIONS", ["London", "NewYork", "Overlap"]
            ),
        )
        for key, value in overrides.items():
            if hasattr(cfg, key) and value is not None:
                setattr(cfg, key, value)
        cfg.validate()
        return cfg

    def validate(self) -> None:
        """Loudly reject configurations that would be silently dangerous."""
        if self.risk_per_trade <= 0 or self.risk_per_trade > 10:
            raise ValueError(f"RISK_PER_TRADE out of sane range: {self.risk_per_trade}")
        if self.min_rr < 1.0:
            raise ValueError(f"MIN_RR must be >= 1.0, got {self.min_rr}")
        if self.max_open_positions < 1:
            raise ValueError("MAX_OPEN_POSITIONS must be >= 1")
        if abs(sum(self.weights.values()) - 100.0) > 1e-6:
            raise ValueError("Confidence weights must sum to 100")
        if not self.timeframes:
            raise ValueError("timeframes must not be empty")
        if self.min_candles < 10:
            raise ValueError("min_candles too small for stable indicators")

    # ------------------------------------------------------------------ #
    @property
    def live_trading_unlocked(self) -> bool:
        """The single source of truth for whether real orders may go out.

        Requires BOTH gates: env ``MT5_DRY_RUN=false`` (=> ``dry_run is False``)
        AND the explicit ``--live`` latch (``live_enabled``). Either alone is
        insufficient, satisfying Phases 22 and 37.
        """
        return (not self.dry_run) and self.live_enabled
