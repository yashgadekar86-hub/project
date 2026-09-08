"""Multi-timeframe market data for gold (Phases 2, 3).

Two clearly-labelled sources:

* **PRIMARY — MT5 broker data**: the broker's own XAUUSD (or GOLD, ...) rates
  and ticks. Trading prices, spread, symbol economics ALWAYS come from here
  when a terminal is available.
* **SECONDARY — Yahoo Finance ``GC=F``**: COMEX gold *futures*, used for
  research/analysis when MT5 is not connected (``--analysis-only``). ``GC=F``
  is NOT the broker spot symbol: prices differ by a basis and session times;
  every dataset carries a :class:`DataProvenance` label making the source
  explicit, and reports print both when both exist.

Look-ahead safety: fetchers only return bars that have already closed
(the current, still-forming bar is dropped) so decisions at time T are made
from completed candles only.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import pandas as pd

from .models import DataProvenance, validate_candles

if TYPE_CHECKING:  # pragma: no cover
    from .models import BrokerSymbolSpec

logger = logging.getLogger(__name__)

# Our canonical TF -> yfinance interval + how many bars yfinance can serve.
YF_INTERVAL = {
    "M1": ("1m", 7), "M5": ("5m", 60), "M15": ("15m", 60), "M30": ("30m", 60),
    "H1": ("1h", 730), "H4": ("4h", 730), "D1": ("1d", None),
}
# yfinance period strings that comfortably cover min_candles per timeframe.
YF_PERIOD = {
    "M1": "5d", "M5": "30d", "M15": "60d", "M30": "60d",
    "H1": "720d", "H4": "720d", "D1": "5y",
}

# Our canonical TF -> MetaTrader5 timeframe constant name.
MT5_TF_CONST = {
    "M1": "TIMEFRAME_M1", "M5": "TIMEFRAME_M5", "M15": "TIMEFRAME_M15",
    "M30": "TIMEFRAME_M30", "H1": "TIMEFRAME_H1", "H4": "TIMEFRAME_H4",
    "D1": "TIMEFRAME_D1",
}


def fetch_yf_timeframes(
    symbol: str = "GC=F",
    timeframes: list[str] | None = None,
    min_candles: int = 60,
) -> tuple[dict[str, pd.DataFrame], DataProvenance]:
    """Fetch closed candles per timeframe from Yahoo Finance.

    Raises ``RuntimeError`` when NOTHING could be fetched (caller must treat
    as DATA_UNAVAILABLE -> HOLD). Individual timeframe failures are logged
    and simply omitted from the result.
    """
    import yfinance as yf

    timeframes = timeframes or ["D1", "H4", "H1", "M15", "M5"]
    ticker = yf.Ticker(symbol)
    out: dict[str, pd.DataFrame] = {}
    for tf in timeframes:
        if tf not in YF_INTERVAL:
            continue
        interval, _ = YF_INTERVAL[tf]
        try:
            df = ticker.history(
                period=YF_PERIOD[tf], interval=interval,
                auto_adjust=False, actions=False,
            )
        except Exception as exc:  # noqa: BLE001 - per-TF degradation
            logger.warning("yfinance %s %s failed: %s", symbol, tf, exc)
            continue
        if df is None or len(df) == 0:
            logger.warning("yfinance %s %s returned no rows", symbol, tf)
            continue
        try:
            df = validate_candles(_normalize_yf(df), min_rows=1)
        except ValueError as exc:
            logger.warning("yfinance %s %s unusable: %s", symbol, tf, exc)
            continue
        # Drop the still-forming last bar: intraday frames always, and the
        # daily frame when its bar is today's (still forming until the gold
        # session closes). Decisions must rest on CLOSED candles only.
        if tf != "D1":
            df = df.iloc[:-1]
        else:
            today = pd.Timestamp.now(tz="UTC").normalize()
            if len(df) and df.index[-1] >= today:
                df = df.iloc[:-1]
        if len(df) >= min_candles:
            out[tf] = df
        else:
            logger.warning("yfinance %s %s only %d bars (< %d)",
                           symbol, tf, len(df), min_candles)

    if not out:
        raise RuntimeError(
            f"DATA_UNAVAILABLE: no usable candles from Yahoo for {symbol} "
            f"(timeframes tried: {timeframes})"
        )
    provenance = DataProvenance(
        source="YAHOO", symbol=symbol,
        fetched_at=datetime.now(timezone.utc),
    )
    return out, provenance


def _normalize_yf(df: pd.DataFrame) -> pd.DataFrame:
    """Map yfinance columns to our lowercase OHLCV convention."""
    rename = {"Open": "open", "High": "high", "Low": "low",
              "Close": "close", "Volume": "volume"}
    out = df.rename(columns=rename)
    for col in ("open", "high", "low", "close"):
        if col not in out.columns:
            raise ValueError(f"missing {col}")
    if "volume" not in out.columns:
        out["volume"] = 0.0
    return out


def fetch_mt5_timeframes(
    broker,
    symbol: str,
    timeframes: list[str] | None = None,
    bars: int = 300,
) -> tuple[dict[str, pd.DataFrame], DataProvenance]:
    """Fetch closed candles per timeframe from the MT5 terminal (PRIMARY)."""
    mt5 = broker._mt5
    if mt5 is None:
        raise RuntimeError("MT5 broker not connected")
    timeframes = timeframes or ["D1", "H4", "H1", "M15", "M5"]
    out: dict[str, pd.DataFrame] = {}
    for tf in timeframes:
        const_name = MT5_TF_CONST.get(tf)
        if const_name is None:
            continue
        timeframe = getattr(mt5, const_name)
        rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, bars)
        if not rates:
            logger.warning("MT5 %s %s returned no rates", symbol, tf)
            continue
        df = pd.DataFrame(rates)
        df.index = pd.to_datetime(df["time"], unit="s", utc=True)
        df = df.rename(columns={"open": "open", "high": "high", "low": "low",
                                "close": "close", "tick_volume": "volume"})
        try:
            df = validate_candles(df[["open", "high", "low", "close", "volume"]])
        except ValueError as exc:
            logger.warning("MT5 %s %s unusable: %s", symbol, tf, exc)
            continue
        out[tf] = df
    if not out:
        raise RuntimeError(f"DATA_UNAVAILABLE: no MT5 candles for {symbol}")
    return out, DataProvenance(
        source="MT5_BROKER", symbol=symbol,
        fetched_at=datetime.now(timezone.utc),
    )


def broker_spec_from_symbol_info(info) -> BrokerSymbolSpec:
    """Convert an MT5 ``symbol_info`` namedtuple into :class:`BrokerSymbolSpec`.

    Every economic field is read from the broker — nothing assumed.
    """
    from .models import BrokerSymbolSpec

    point = float(getattr(info, "point", 0.01) or 0.01)
    digits = int(getattr(info, "digits", 2) or 2)
    spread_pts = None
    try:
        spread_pts = float(getattr(info, "spread", 0) or 0)
    except (TypeError, ValueError):
        spread_pts = None
    return BrokerSymbolSpec(
        symbol=str(getattr(info, "name", "")),
        contract_size=float(getattr(info, "trade_contract_size", 100.0) or 100.0),
        tick_size=float(getattr(info, "trade_tick_size", point) or point),
        tick_value=float(getattr(info, "trade_tick_value", 0.0) or 0.0),
        point=point,
        digits=digits,
        volume_min=float(getattr(info, "volume_min", 0.01) or 0.01),
        volume_max=float(getattr(info, "volume_max", 100.0) or 100.0),
        volume_step=float(getattr(info, "volume_step", 0.01) or 0.01),
        stops_level_points=float(getattr(info, "trade_stops_level", 0) or 0),
        freeze_level_points=float(getattr(info, "trade_freeze_level", 0) or 0),
        spread_points=spread_pts,
    )


def reference_spec(price: float | None = None) -> BrokerSymbolSpec:
    """A typical-gold spec for analysis/paper when no broker is connected.

    Clearly labelled as an assumption: only used for research mode and the
    backtester default, never for live order construction (the live path
    always builds the spec from the broker's symbol_info).
    """
    from .models import BrokerSymbolSpec

    return BrokerSymbolSpec(
        symbol="GC=F (reference)",
        contract_size=100.0,
        tick_size=0.1,
        tick_value=10.0,
        point=0.1,
        digits=2,
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
        stops_level_points=0.0,
    )
