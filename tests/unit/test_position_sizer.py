"""Unit tests for the position size calculator."""
from __future__ import annotations

import pytest
from trading_engine.risk.position_sizer import SymbolSpec, calculate_position_size


def _eurusd_spec() -> SymbolSpec:
    # EURUSD standard FX contract: 100,000 units, 5-digit pricing, pip = 0.00010.
    return SymbolSpec(
        name="EURUSD",
        digits=5,
        point=0.00001,
        contract_size=100_000,
        lot_min=0.01,
        lot_max=100.0,
        lot_step=0.01,
        tick_size=0.00001,
        # ~$10 per pip per 1.0 lot (EURUSD≈$10/pip)
        tick_value=1.0,
        currency_base="EUR",
        currency_profit="USD",
        currency_margin="EUR",
        spread=0.0,
        spread_float=True,
        stops_level=20,
        freeze_level=0,
        trade_mode=0,
        trade_exemode=0,
        trade_calc_mode=0,
        swap_mode=0,
        swap_long=0.0,
        swap_short=0.0,
        visible=True,
    )


def _usdjpy_spec() -> SymbolSpec:
    # USDJPY: 3-digit pip (0.010), contract 100,000, tick value ~$10 / 100 pips/lot.
    return SymbolSpec(
        name="USDJPY",
        digits=3,
        point=0.001,
        contract_size=100_000,
        lot_min=0.01,
        lot_max=100.0,
        lot_step=0.01,
        tick_size=0.001,
        tick_value=0.92,  # ~$9.20 per pip per lot approx
        currency_base="USD",
        currency_profit="JPY",
        currency_margin="USD",
        spread=0.0,
        spread_float=True,
        stops_level=20,
        freeze_level=0,
        trade_mode=0,
        trade_exemode=0,
        trade_calc_mode=0,
        swap_mode=0,
        swap_long=0.0,
        swap_short=0.0,
        visible=True,
    )


def test_position_size_basic_lot_calculation():
    """1% risk on a $10,000 account with a 50-pip SL on EURUSD = ~$10 risk."""
    spec = _eurusd_spec()
    # entry=1.08000, SL=1.07500 (50 pips stop)
    res = calculate_position_size(
        equity=10_000.0,
        risk_pct=1.0,
        entry=1.08000,
        stop_loss=1.07500,
        take_profit=1.09250,
        spec=spec,
    )
    assert res.allowed, res.reason
    # 50 pips * $10/pip/lot = $500/lot risk. $100 risk → 0.20 lot.
    assert res.calculated_lot == pytest.approx(0.20, rel=0.05), res
    # Rounding is DOWN to nearest lot_step to guarantee we NEVER exceed risk.
    assert res.rounded_lot <= res.calculated_lot + 1e-9
    assert res.rounded_lot >= 0.19
    assert res.estimated_loss_at_sl <= res.risk_amount + 0.01
    assert res.risk_reward == pytest.approx(2.5, abs=0.05)


def test_position_size_rounds_down_never_exceeds_risk():
    """The rounded lot must NEVER exceed the risk budget."""
    spec = _eurusd_spec()
    res = calculate_position_size(
        equity=1_000.0,
        risk_pct=1.0,         # risk $10
        entry=1.10000,
        stop_loss=1.09950,    # 5 pips SL → $50/lot → raw lot 0.20
        take_profit=1.10150,
        spec=spec,
    )
    assert res.allowed, res.reason
    assert res.estimated_loss_at_sl <= 10.0 + 1e-6, res


def test_position_size_below_minimum_lot_rejected():
    """If even the minimum lot exceeds risk budget, the trade is rejected."""
    spec = _eurusd_spec()
    res = calculate_position_size(
        equity=100.0,
        risk_pct=0.5,        # risk $0.50
        entry=1.10000,
        stop_loss=1.09900,   # 10 pip SL → $100/lot → 0.005 lot needed
        take_profit=1.10200,
        spec=spec,
    )
    assert not res.allowed
    assert (
        ("below broker minimum" in (res.reason or "").lower())
        or ("exceeds risk" in (res.reason or "").lower())
    )


def test_position_size_clamps_to_max_lot():
    spec = _eurusd_spec()
    res = calculate_position_size(
        equity=10_000_000,   # huge account
        risk_pct=1.0,
        entry=1.10000,
        stop_loss=1.09000,   # 100 pip SL → $1000/lot → $100k risk → 100 lots
        take_profit=1.13000,
        spec=spec,
        max_lot_override=5.0,
    )
    assert res.allowed, res.reason
    assert res.rounded_lot <= 5.0


def test_position_size_invalid_inputs():
    spec = _eurusd_spec()
    # SL == entry → zero distance → must reject
    res = calculate_position_size(
        equity=10_000, risk_pct=1.0, entry=1.10000, stop_loss=1.10000,
        take_profit=1.10100, spec=spec,
    )
    assert not res.allowed


def test_jpy_position_size():
    """Smoke-test for 3-digit JPY pair — pips are 0.01, not 0.0001."""
    spec = _usdjpy_spec()
    res = calculate_position_size(
        equity=10_000,
        risk_pct=1.0,
        entry=150.00,
        stop_loss=149.50,   # 50 pips
        take_profit=151.00,
        spec=spec,
    )
    assert res.allowed, res.reason
    assert res.rounded_lot > 0
    assert res.sl_distance_pips == pytest.approx(50.0, rel=0.01)
    assert res.estimated_loss_at_sl <= 100.0 + 0.01
