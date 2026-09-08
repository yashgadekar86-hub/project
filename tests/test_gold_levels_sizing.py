"""Phase 11-14 tests: SL/TP construction, RR gating, position sizing.

Covers: structural stops, trader-stop sanity window, honest TP (never a
manufactured RR target), broker validation, tick-value vs contract-size
economics, FLOOR rounding (risk can only shrink), minimum-volume-exceeds-
risk -> NO TRADE, volume_max clamp.
"""

from __future__ import annotations

import pytest

from tests.gold_helpers import controlled_mtf, gold_spec, market_at
from tradingagents.gold.levels import (
    build_trade_plan,
    validate_levels_against_broker,
)
from tradingagents.gold.sizing import calculate_volume, loss_per_lot


def plan_for(spec=None, market=None, mtf=None, min_rr=2.0, **kw):
    return build_trade_plan(
        side="BUY",
        mtf=mtf or controlled_mtf(),
        market=market or market_at(104.5),
        spec=spec or gold_spec(),
        min_rr=min_rr,
        atr_sl_multiplier=1.5,
        min_sl_atr_multiple=0.5,
        **kw,
    )


# ------------------------------------------------------------------ stop loss
def test_structural_sl_below_support_with_atr_buffer():
    plan = plan_for()
    assert plan.valid
    assert plan.stop_source == "STRUCTURE"
    # support 102, ATR 2 -> SL = 102 - 0.25*2 = 101.5
    assert plan.stop_loss == pytest.approx(101.5, abs=0.02)
    assert plan.entry == pytest.approx(104.65, abs=0.02)  # ask with 30pt spread


def test_trader_stop_used_only_when_sane():
    # a trader stop 1.5 below entry is on the correct side and outside noise
    plan = plan_for(trader_stop=103.0)
    assert plan.valid
    assert plan.stop_source == "TRADER"
    assert plan.stop_loss == pytest.approx(103.0, abs=0.01)


def test_trader_stop_ignored_when_inside_noise_band():
    # 0.2 away is inside the 0.5*ATR(=1.0) noise band -> structural stop wins
    plan = plan_for(trader_stop=104.4)
    assert plan.valid
    assert plan.stop_source == "STRUCTURE"


def test_trader_stop_ignored_when_on_wrong_side():
    plan = plan_for(trader_stop=110.0)  # above entry for a BUY
    assert plan.valid
    assert plan.stop_source == "STRUCTURE"


def test_atr_fallback_when_no_support():
    mtf = controlled_mtf(h1_support=())  # no support below
    plan = plan_for(mtf=mtf)
    assert plan.valid
    assert plan.stop_source == "ATR"
    # entry - 1.5*ATR(2.0) = entry - 3.0
    assert plan.stop_loss == pytest.approx(plan.entry - 3.0, abs=0.02)


def test_sl_rejected_when_too_close_for_broker():
    # stops level 50 points = 0.50 price; structural SL is 3.15 away -> OK.
    # Tighten the market so the ATR fallback (3.0) is fine but a 0.2 stop
    # would not be: the builder must skip candidates inside the stops level.
    spec = gold_spec(stops_level_points=400)  # 4.00 price distance required
    plan = plan_for(spec=spec)
    # structure SL is ~3.15 away < 4.00 -> rejected; ATR SL 3.0 < 4.0 -> also
    # rejected -> no valid stop -> plan invalid (never an unprotected trade)
    assert not plan.valid
    assert "no valid stop-loss" in plan.invalid_reason


def test_no_valid_stop_means_invalid_plan_not_unprotected():
    mtf = controlled_mtf(h1_support=(), h1_resistance=(), h1_atr=0.001)
    plan = plan_for(mtf=mtf)
    assert not plan.valid


# ----------------------------------------------------------------- take profit
def test_structural_tp_clears_min_rr():
    plan = plan_for()
    assert plan.valid
    # resistance 112: reward ~7.35 vs risk ~3.15 -> RR ~2.3 >= 2
    assert plan.tp_source == "STRUCTURE"
    assert plan.tp1 == pytest.approx(112.0, abs=0.02)
    assert plan.rr >= 2.0


def test_tp_not_manufactured_to_reach_rr():
    # resistance only 106 (reward ~1.35, risk ~3.15 -> RR 0.43); ATR
    # extensions max out at 4*2=8 below... none reach RR 2 with a 3.15 risk
    # (needs 6.3 reward). ATR 2.0 candidates give rewards 4,5,6,8 -> 8 gives
    # RR 2.5, so to force failure use min_rr=2.6.
    mtf = controlled_mtf(h1_resistance=(106.0,))
    plan = plan_for(mtf=mtf, min_rr=2.6)
    assert not plan.valid
    assert "min RR" in plan.invalid_reason


def test_pure_rr_fallback_disabled_by_default():
    # With allow_rr_fallback False there must be NO tp exactly at
    # entry + min_rr * risk unless a structure/ATR candidate lands there.
    mtf = controlled_mtf(h1_resistance=(106.0,))
    plan = build_trade_plan(
        side="BUY", mtf=mtf, market=market_at(104.5), spec=gold_spec(),
        min_rr=2.0, atr_sl_multiplier=1.5, min_sl_atr_multiple=0.5,
    )
    if plan.valid:
        risk = plan.risk_distance
        for tp in plan.take_profits:
            assert abs(tp - (plan.entry + 2.0 * risk)) > 0.05 or \
                plan.tp_source in ("STRUCTURE", "TRADER", "ATR_MULT")


def test_rr_fallback_only_when_explicitly_allowed():
    # ATR extensions top out at 4*ATR (8.0 reward, RR ~2.54); with min_rr 2.6
    # nothing honest qualifies, so ONLY the explicit RR fallback can rescue
    # the plan — proving it is opt-in.
    mtf = controlled_mtf(h1_resistance=(106.0,))
    plan = build_trade_plan(
        side="BUY", mtf=mtf, market=market_at(104.5), spec=gold_spec(),
        min_rr=2.6, atr_sl_multiplier=1.5, min_sl_atr_multiple=0.5,
        allow_rr_fallback=True,
    )
    assert plan.valid
    assert plan.tp_source == "RR_FALLBACK"
    assert plan.rr >= 2.6 - 1e-6


def test_multiple_tp_levels_are_progressively_further():
    plan = plan_for(tp_levels=3)
    assert plan.valid
    tps = plan.take_profits
    assert 1 <= len(tps) <= 3
    distances = [abs(t - plan.entry) for t in tps]
    assert distances == sorted(distances)


def test_tp_nearest_qualifying_target_chosen():
    # two resistances: 111 (RR ~2.0) and 115 (RR ~3.3); nearest that clears
    # min_rr must win, not the biggest.
    mtf = controlled_mtf(h1_resistance=(111.0, 115.0))
    plan = plan_for(mtf=mtf, min_rr=1.5)
    assert plan.valid
    assert plan.tp1 == pytest.approx(111.0, abs=0.02)


# ---------------------------------------------------------- SELL side symmetry
def test_sell_plan_mirrors_buy_logic():
    mtf = controlled_mtf(h1="BEARISH", h1_trend="BEARISH",
                         h1_support=(98.0,), h1_resistance=(),
                         close=104.5)
    # for a SELL: resistance above is gone; support below becomes the TP side
    plan = build_trade_plan(
        side="SELL", mtf=mtf, market=market_at(104.5), spec=gold_spec(),
        min_rr=1.5, atr_sl_multiplier=1.5, min_sl_atr_multiple=0.5,
    )
    assert plan.valid
    assert plan.stop_loss > plan.entry       # above entry for SELL
    assert plan.tp1 < plan.entry             # below entry for SELL
    assert plan.rr >= 1.5


# --------------------------------------------------------- broker validation
def test_broker_validation_flags_wrong_side_levels():
    from tradingagents.gold.models import TradePlan
    plan = TradePlan(side="BUY", entry=100.0, stop_loss=101.0,  # wrong side
                     take_profits=[99.0], valid=True, risk_distance=1.0,
                     rr=1.0)
    problems = validate_levels_against_broker(plan, gold_spec())
    assert any("SL not below entry" in p for p in problems)
    assert any("TP not above entry" in p for p in problems)


def test_broker_validation_flags_stops_level_violation():
    from tradingagents.gold.models import TradePlan
    plan = TradePlan(side="BUY", entry=100.0, stop_loss=99.9,
                     take_profits=[100.05], valid=True, risk_distance=0.1,
                     rr=0.5)
    spec = gold_spec(stops_level_points=30)  # 0.30 minimum distance
    problems = validate_levels_against_broker(plan, spec)
    assert any("stops level" in p for p in problems)


def test_broker_validation_ok_for_clean_plan():
    plan = plan_for()
    assert validate_levels_against_broker(plan, gold_spec()) == []


# -------------------------------------------------------------------- sizing
def test_loss_per_lot_tick_value_path():
    # distance 10.0, tick 0.01 -> 1000 ticks * $1 = $1000/lot
    assert loss_per_lot(10.0, gold_spec()) == pytest.approx(1000.0)


def test_loss_per_lot_contract_size_fallback():
    spec = gold_spec(tick_value=0.0)  # unreported -> contract fallback
    assert loss_per_lot(10.0, spec) == pytest.approx(1000.0)  # 10 * 100


def test_loss_per_lot_rejects_non_positive():
    with pytest.raises(ValueError):
        loss_per_lot(0.0, gold_spec())


def test_sizing_exact_volume():
    plan = plan_for()
    result = calculate_volume(plan, gold_spec(), 10_000.0, 1.0)
    assert result.ok
    # risk $100; per-lot = risk_distance(3.15) * 100 = $315 -> 0.317.. ->
    # floor to step 0.01 -> 0.31
    assert result.volume == pytest.approx(0.31, abs=0.005)
    assert plan.risk_amount <= 100.0  # realized risk never exceeds allowed


def test_sizing_floors_never_rounds_up():
    plan = plan_for()
    result = calculate_volume(plan, gold_spec(), 10_000.0, 1.0)
    per_lot = result.loss_per_lot
    assert plan.risk_amount <= per_lot * result.volume + 1e-9
    # one more step would exceed the risk budget
    assert plan.risk_amount + per_lot * 0.01 > 100.0 + 1e-9 or \
        result.volume == gold_spec().volume_max


def test_sizing_min_volume_exceeds_risk_is_no_trade():
    plan = plan_for()
    # equity 500 * 0.5% = $2.50 allowed; min lot 0.01 risks ~$3.15 -> HOLD
    result = calculate_volume(plan, gold_spec(), 500.0, 0.5)
    assert not result.ok
    assert "minimum broker volume" in result.reason


def test_sizing_respects_volume_max():
    plan = plan_for()
    spec = gold_spec(volume_max=0.05)
    result = calculate_volume(plan, spec, 10_000_000.0, 10.0)
    assert result.ok
    assert result.volume == 0.05


def test_sizing_snaps_to_volume_step():
    plan = plan_for()
    spec = gold_spec(volume_step=0.1, volume_min=0.1)
    result = calculate_volume(plan, spec, 10_000.0, 1.0)
    assert result.ok
    assert abs(result.volume / 0.1 - round(result.volume / 0.1)) < 1e-9


def test_sizing_requires_valid_plan():
    from tradingagents.gold.models import TradePlan
    result = calculate_volume(TradePlan(side="BUY"), gold_spec(), 10_000, 1.0)
    assert not result.ok


def test_sizing_uses_real_broker_economics_not_assumptions():
    """A broker with contract 10 (not 100) must size 10x larger."""
    plan = plan_for()
    small = gold_spec(contract_size=10.0, tick_value=0.10, tick_size=0.01)
    big = gold_spec(contract_size=100.0, tick_value=1.0, tick_size=0.01)
    r_small = calculate_volume(plan, small, 10_000.0, 1.0)
    r_big = calculate_volume(plan, big, 10_000.0, 1.0)
    assert r_small.loss_per_lot == pytest.approx(r_big.loss_per_lot / 10)
    # volumes differ by ~10x within one volume step (floor rounding)
    assert abs(r_small.volume - r_big.volume * 10) <= 0.1
