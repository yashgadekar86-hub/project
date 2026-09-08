"""Phase 2 tests: data validation, provenance labelling, symbol specs."""

from __future__ import annotations

import pandas as pd
import pytest

from tests.gold_helpers import candles_from_path, trend_path
from tradingagents.gold.marketdata import (
    broker_spec_from_symbol_info,
    reference_spec,
)
from tradingagents.gold.models import (
    BrokerSymbolSpec,
    DataProvenance,
    validate_candles,
)


# ------------------------------------------------------------ validate_candles
def test_validate_candles_normalizes_columns():
    df = candles_from_path(trend_path(30, seed=1))
    df.columns = ["Open", "High", "Low", "Close", "Volume"]
    out = validate_candles(df)
    assert list(out.columns) == ["open", "high", "low", "close", "volume"]


def test_validate_candles_rejects_empty():
    with pytest.raises(ValueError, match="empty"):
        validate_candles(pd.DataFrame())


def test_validate_candles_rejects_missing_columns():
    df = candles_from_path(trend_path(10, seed=1)).drop(columns=["volume"])
    with pytest.raises(ValueError, match="missing columns"):
        validate_candles(df)


def test_validate_candles_sorts_and_dedupes():
    df = candles_from_path(trend_path(30, seed=2))
    shuffled = df.iloc[::-1]  # reverse order
    out = validate_candles(shuffled)
    assert out.index.is_monotonic_increasing
    dup = pd.concat([df, df])
    out2 = validate_candles(dup)
    assert len(out2) == len(df)


def test_validate_candles_localizes_utc():
    df = candles_from_path(trend_path(10, seed=3))
    df.index = df.index.tz_localize(None)
    out = validate_candles(df)
    assert str(out.index.tz) == "UTC"


def test_validate_candles_min_rows_enforced():
    df = candles_from_path(trend_path(10, seed=4))
    with pytest.raises(ValueError, match="need"):
        validate_candles(df, min_rows=50)


# ------------------------------------------------------------------ provenance
def test_provenance_label_format():
    p = DataProvenance(source="MT5_BROKER", symbol="XAUUSD", timeframe="H1")
    assert p.label() == "[MT5_BROKER:XAUUSD H1]"
    p2 = DataProvenance(source="YAHOO", symbol="GC=F")
    assert p2.label() == "[YAHOO:GC=F]"


def test_broker_data_is_primary_reference_is_labelled():
    """The spec's Phase 2 rule: broker XAUUSD is primary; GC=F is reference."""
    broker_prov = DataProvenance(source="MT5_BROKER", symbol="XAUUSD", timeframe="H1")
    ref_prov = DataProvenance(source="YAHOO", symbol="GC=F", timeframe="H1")
    assert "MT5_BROKER" in broker_prov.label()
    assert "GC=F" in ref_prov.label()
    assert broker_prov.label() != ref_prov.label()


# ------------------------------------------------------------------ spec build
def test_broker_spec_from_symbol_info_reads_every_field():
    info = type("Info", (), {
        "name": "XAUUSD.pro", "trade_contract_size": 100.0,
        "trade_tick_size": 0.01, "trade_tick_value": 1.0, "point": 0.01,
        "digits": 2, "volume_min": 0.1, "volume_max": 20.0,
        "volume_step": 0.1, "trade_stops_level": 30,
        "trade_freeze_level": 10, "spread": 35,
    })()
    spec = broker_spec_from_symbol_info(info)
    assert spec.symbol == "XAUUSD.pro"
    assert spec.contract_size == 100.0
    assert spec.tick_size == 0.01 and spec.tick_value == 1.0
    assert spec.volume_min == 0.1 and spec.volume_step == 0.1
    assert spec.volume_max == 20.0
    assert spec.stops_level_points == 30.0
    assert spec.spread_points == 35.0
    assert spec.stops_level_price() == pytest.approx(0.30)
    assert spec.spread_price() == pytest.approx(0.35)


def test_broker_spec_handles_none_defaults():
    info = type("Info", (), {
        "name": "GOLD", "trade_contract_size": None, "trade_tick_size": None,
        "trade_tick_value": None, "point": None, "digits": None,
        "volume_min": None, "volume_max": None, "volume_step": None,
        "trade_stops_level": None, "trade_freeze_level": None,
        "spread": None,
    })()
    spec = broker_spec_from_symbol_info(info)
    # safe fallbacks, never zero (which would break sizing math)
    assert spec.contract_size > 0
    assert spec.point > 0
    assert spec.volume_step > 0


def test_reference_spec_is_labelled_and_typical():
    spec = reference_spec()
    assert "reference" in spec.symbol
    assert spec.contract_size == 100.0


def test_spec_spread_price_none_when_unknown():
    spec = BrokerSymbolSpec(symbol="X", spread_points=None)
    assert spec.spread_price() is None
