"""Phases 10-21 + Phase 41 tests: the deterministic risk engine.

Every gate gets its own test, plus the 13 explicit safety invariants from
Phase 41 proving the LLM cannot override deterministic rules.
"""

from __future__ import annotations

import pytest

from tests.gold_helpers import (
    FOMC_NOW,
    NFP_NOW,
    SAFE_NOW,
    account,
    cfg,
    controlled_mtf,
    gold_spec,
    market_at,
)
from tradingagents.gold.models import (
    GoldFundamentalPoint,
    GoldFundamentals,
    NewsItem,
)
from tradingagents.gold.risk_engine import (
    DeterministicRiskEngine,
    candidate_from_rating,
)


# ---------------------------------------------------------------- test scaffold
def engine_with(config=None, now=SAFE_NOW, **engine_kwargs):
    cfg_ = config or cfg()
    return DeterministicRiskEngine(cfg_, now=now)


def eval_buy(engine_cfg=None, now=SAFE_NOW, rating="Buy",
             mtf=None, market=None, spec=None, acct=None, **kw):
    """A default-clean BUY candidate through the engine."""
    engine = engine_with(engine_cfg, now=now)
    return engine.evaluate(
        rating=rating,
        mtf=mtf or controlled_mtf(),
        market=market or market_at(104.5),
        spec=spec or gold_spec(),
        account=acct or account(10_000.0),
        **kw,
    )


def strong_news():
    """Fresh, sourced, high-impact bullish news for confidence tests."""
    from datetime import timedelta

    now = SAFE_NOW
    items = []
    for i, head in enumerate([
        "Fed signals rate cut as inflation cools",
        "Real yields fall to multi-month lows",
        "Central banks continue gold buying spree",
        "Dollar weakens after dovish Fed minutes",
        "Gold ETF inflows hit yearly high",
        "Safe-haven demand rises on geopolitical tension",
    ]):
        items.append(NewsItem(
            headline=head, source="Reuters",
            timestamp=now - timedelta(hours=2 + i),
            summary=head, sentiment=0.6, gold_impact="HIGH",
            confidence=1.0, age_hours=2 + i,
        ))
    return items


def strong_fundamentals():
    return GoldFundamentals(points=[
        GoldFundamentalPoint(
            name=n, value=v, unit=u, source="FRED",
            timestamp=SAFE_NOW, freshness_hours=24.0,
        )
        for n, v, u in [
            ("real_yield_10y", 0.3, "%"), ("real_yield_5y", 0.2, "%"),
            ("nominal_yield_10y", 2.0, "%"), ("nominal_yield_2y", 1.8, "%"),
            ("fed_funds_rate", 2.0, "%"), ("dollar_index", 100.0, "index"),
            ("cpi_yoy", 300.0, "index"), ("core_cpi", 300.0, "index"),
            ("inflation_expectations", 2.2, "%"), ("gold_fix", 1050.0, "USD"),
        ]
    ])


# ------------------------------------------------------- rating translation
@pytest.mark.parametrize("rating,expected", [
    ("Buy", "BUY"), ("Overweight", "BUY"), ("Hold", "HOLD"),
    ("Underweight", "SELL"), ("Sell", "SELL"),
    (None, "HOLD"), ("REVIEW", "HOLD"), ("garbage", "HOLD"), ("", "HOLD"),
])
def test_candidate_from_rating_table(rating, expected):
    assert candidate_from_rating(rating) == expected


def test_review_rating_never_trades():
    decision = eval_buy(rating="REVIEW")
    assert decision.action == "HOLD"
    assert not decision.tradeable


# ------------------------------------------------------------ happy path
def test_full_pass_buy_with_all_inputs():
    decision = eval_buy(engine_cfg=cfg(min_confidence=75.0, min_rr=2.0),
                        fundamentals=strong_fundamentals(),
                        news_fresh=strong_news())
    assert decision.action == "BUY", [c.reason for c in decision.checks]
    assert decision.tradeable
    plan = decision.plan
    assert plan is not None and plan.valid
    assert plan.stop_loss is not None and plan.tp1 is not None
    assert plan.rr >= 2.0
    assert plan.volume is not None and plan.volume > 0
    assert plan.signal_id  # idempotency id attached
    assert decision.confidence.total >= 75.0
    # every recorded check passed
    assert all(c.passed for c in decision.checks), \
        [(c.name, c.reason) for c in decision.checks if not c.passed]


def test_hold_rating_is_no_trade():
    decision = eval_buy(rating="Hold")
    assert decision.action == "HOLD"
    assert decision.plan is None


# ============================================ PHASE 41 SAFETY INVARIANTS ====
# 1. LLM cannot override news blackout
def test_invariant_1_llm_cannot_override_news_blackout():
    # Strong BUY rating + bullish everything, but FOMC in 10 minutes.
    decision = eval_buy(now=FOMC_NOW, rating="Buy",
                        fundamentals=strong_fundamentals(),
                        news_fresh=strong_news())
    assert decision.action == "HOLD"
    assert "news_blackout" in decision.vetoed_by


def test_blackout_also_blocks_nfp():
    decision = eval_buy(now=NFP_NOW)
    assert decision.action == "HOLD"
    assert "news_blackout" in decision.vetoed_by


def test_blackout_operator_override_is_config_not_llm():
    # Only the operator flag can lift the blackout.
    decision = eval_buy(now=FOMC_NOW,
                        engine_cfg=cfg(allow_trade_during_blackout=True))
    assert "news_blackout" not in decision.vetoed_by


# 2. LLM cannot override minimum RR
def test_invariant_2_llm_cannot_override_min_rr():
    # Resistance only 106 away and ATR small (1.0): the widest honest target
    # (4x ATR = 4.0 reward vs 3.15 risk) reaches only RR ~1.3. A bullish
    # rating must NOT manufacture a 2R target -> HOLD.
    mtf = controlled_mtf(h1_resistance=(106.0,), h1_atr=1.0)
    decision = eval_buy(mtf=mtf, rating="Buy")
    assert decision.action == "HOLD"
    assert "min_rr" in decision.vetoed_by or "plan_invalid" in decision.vetoed_by


def test_rr_gate_blocks_low_rr_trade():
    # min_rr raised above what the structure can offer
    decision = eval_buy(engine_cfg=cfg(min_rr=99.0))
    assert decision.action == "HOLD"
    assert "min_rr" in decision.vetoed_by or "plan_invalid" in decision.vetoed_by


def test_rr_gate_unit_logic():
    """The spec's canonical example: LLM=BUY, RR=1.3, min RR=2.0 -> HOLD."""
    from tradingagents.gold.models import TradePlan

    engine = engine_with(cfg(min_rr=2.0))
    low = TradePlan(side="BUY", entry=100.0, stop_loss=98.5,
                    take_profits=[101.95], valid=True, rr=1.3,
                    risk_distance=1.5)
    high = TradePlan(side="BUY", entry=100.0, stop_loss=98.5,
                     take_profits=[103.0], valid=True, rr=3.0,
                     risk_distance=1.5)
    assert engine.rr_ok(low) is False
    assert engine.rr_ok(high) is True


# 3. LLM cannot override daily loss
def test_invariant_3_llm_cannot_override_daily_loss():
    acct = account(10_000.0, realized_pnl_today=-250.0)  # -2.5% today
    decision = eval_buy(acct=acct)
    assert decision.action == "HOLD"
    assert "daily_loss" in decision.vetoed_by


def test_daily_loss_boundary_exactly_at_limit_blocks():
    acct = account(10_000.0, realized_pnl_today=-200.0)  # exactly -2%
    decision = eval_buy(acct=acct)
    assert decision.action == "HOLD"
    assert "daily_loss" in decision.vetoed_by


# 4. LLM cannot override maximum position count
def test_invariant_4_llm_cannot_override_max_positions():
    acct = account(10_000.0, open_positions=1, open_direction="BUY")
    decision = eval_buy(acct=acct)
    assert decision.action == "HOLD"
    assert "position_limit" in decision.vetoed_by


def test_max_trades_per_day_blocks():
    acct = account(10_000.0, trades_today=3)
    decision = eval_buy(acct=acct)
    assert decision.action == "HOLD"
    assert "max_trades_per_day" in decision.vetoed_by


def test_consecutive_losses_block():
    acct = account(10_000.0, consecutive_losses=4)
    decision = eval_buy(acct=acct)
    assert decision.action == "HOLD"
    assert "consecutive_losses" in decision.vetoed_by


# 5. LLM cannot create a position without SL
def test_invariant_5_no_position_without_sl():
    # Every path that produces a tradeable plan carries a stop-loss; and any
    # plan whose SL fails sanity checks is rejected before sizing.
    mtf = controlled_mtf(h1_support=())  # no support -> ATR fallback stop
    decision = eval_buy(mtf=mtf)
    if decision.tradeable:
        assert decision.plan.stop_loss is not None
        assert decision.plan.stop_loss < decision.plan.entry
    else:
        assert decision.action == "HOLD"


def test_unstoppable_sl_rejection_blocks_trade():
    # No support and a tiny (but live) ATR: the ATR stop (1.5*ATR = 0.075)
    # sits inside the spread (0.30), so no valid stop exists -> NO TRADE.
    mtf = controlled_mtf(h1_support=(), h1_atr=0.05)
    decision = eval_buy(mtf=mtf)
    assert decision.action == "HOLD"
    assert "plan_invalid" in decision.vetoed_by


# 6. dry-run never submits a real order  (see test_gold_runner.py)
# 7. analysis-only never submits an order (see test_gold_runner.py)
# 8. live mode requires --live               (see test_gold_runner.py)
# 9. live mode requires MT5_DRY_RUN=false    (see test_gold_runner.py)

# 10. long-only mode cannot open shorts
def test_invariant_10_long_only_cannot_open_shorts():
    decision = eval_buy(rating="Sell",
                        mtf=controlled_mtf(d1="BEARISH", h4="BEARISH",
                                           h1="BEARISH", m15="BEARISH",
                                           m5="BEARISH",
                                           h1_support=(98.0,), h1_resistance=()),
                        market=market_at(104.5))
    assert decision.action == "HOLD"
    assert "long_only" in decision.vetoed_by


def test_short_allowed_when_enabled():
    decision = eval_buy(rating="Sell",
                        engine_cfg=cfg(allow_short=True),
                        mtf=controlled_mtf(d1="BEARISH", h4="BEARISH",
                                           h1="BEARISH", m15="BEARISH",
                                           m5="BEARISH",
                                           h1_support=(98.0,), h1_resistance=()))
    assert decision.action == "SELL", [c.reason for c in decision.checks]
    assert decision.plan.stop_loss > decision.plan.entry  # SL above for short


# 11. missing data results in HOLD
def test_invariant_11_missing_data_is_hold():
    mtf = controlled_mtf(h1_usable=False)
    decision = eval_buy(mtf=mtf)
    assert decision.action == "HOLD"
    assert "data_unavailable" in decision.vetoed_by


def test_missing_market_price_is_hold():
    from tradingagents.gold.models import MarketContext
    decision = eval_buy(market=MarketContext(bid=None, ask=None))
    assert decision.action == "HOLD"
    assert "data_unavailable" in decision.vetoed_by


def test_zero_equity_is_hold():
    decision = eval_buy(acct=account(0.0))
    assert decision.action == "HOLD"
    assert "account" in decision.vetoed_by


# 12. malformed AI response results in HOLD
def test_invariant_12_malformed_ai_response_is_hold():
    for rating in (None, "REVIEW", "!!!", "maybe??", "Buyy"):
        decision = eval_buy(rating=rating)
        assert decision.action == "HOLD", rating
        assert not decision.tradeable


def test_unparseable_trader_levels_are_ignored_not_fatal():
    # garbage trader stop must not corrupt the plan; engine falls back to
    # its own structural stop.
    decision = eval_buy(trader_stop=None, trader_take_profits=None)
    if decision.tradeable:
        assert decision.plan.stop_source in ("STRUCTURE", "ATR")


# 13. future candles cannot enter historical decisions (see backtest tests)


# ----------------------------------------------------------------- gates
def test_spread_filter_blocks_wide_spread():
    decision = eval_buy(market=market_at(104.5, spread_points=80.0))
    assert decision.action == "HOLD"
    assert "spread" in decision.vetoed_by


def test_spread_filter_passes_normal_spread():
    decision = eval_buy(market=market_at(104.5, spread_points=30.0))
    assert "spread" not in decision.vetoed_by


def test_session_filter_blocks_off_hours():
    from datetime import datetime, timezone

    sunday = datetime(2026, 9, 13, 22, 0, tzinfo=timezone.utc)  # dead hours
    decision = eval_buy(now=sunday)
    assert decision.action == "HOLD"
    assert "session" in decision.vetoed_by


def test_session_filter_allows_configured_sessions():
    from datetime import datetime, timezone

    asian_night = datetime(2026, 9, 9, 2, 0, tzinfo=timezone.utc)  # Asian only
    decision = eval_buy(now=asian_night,
                        engine_cfg=cfg(allowed_sessions=["Asian"]))
    assert "session" not in decision.vetoed_by


def test_volatility_filter_blocks_dead_market():
    mtf = controlled_mtf(h1_atr=0.0001)  # ATR% ~ 0.0001% -> below floor
    decision = eval_buy(mtf=mtf)
    assert decision.action == "HOLD"
    assert "volatility" in decision.vetoed_by


def test_volatility_filter_blocks_exploding_market():
    mtf = controlled_mtf(h1_atr=50.0, close=104.5)  # ~48% of price
    decision = eval_buy(mtf=mtf)
    assert "volatility" in decision.vetoed_by


def test_htf_alignment_blocks_counter_trend_buy():
    decision = eval_buy(mtf=controlled_mtf(d1="BEARISH", h4="BEARISH",
                                           h1="BEARISH", m15="BEARISH",
                                           m5="BEARISH"))
    assert decision.action == "HOLD"
    assert "htf_alignment" in decision.vetoed_by


def test_duplicate_signal_is_suppressed():
    # First evaluation succeeds; replaying the SAME signal within the window
    # must be suppressed (idempotency).
    first = eval_buy()
    assert first.tradeable
    plan = first.plan
    # second run: account now knows the last signal id
    from tradingagents.gold.risk_engine import signal_id

    same_id = signal_id("XAUUSD", "BUY", plan.entry, plan.stop_loss,
                        plan.tp1, SAFE_NOW)
    acct = account(10_000.0, last_signal_id=same_id, last_signal_time=SAFE_NOW)
    second = eval_buy(acct=acct)
    assert second.action == "HOLD"
    assert "duplicate_signal" in second.vetoed_by


def test_sizing_veto_when_min_volume_exceeds_risk():
    decision = eval_buy(acct=account(300.0))  # tiny account, 0.5% = $1.50
    assert decision.action == "HOLD"
    assert "sizing" in decision.vetoed_by


def test_broker_stops_level_cannot_be_bypassed():
    # stops level 400 points = 4.00 price distance: the structural (3.15) and
    # ATR (3.0) stops are both inside it -> no valid stop -> NO TRADE.
    decision = eval_buy(spec=gold_spec(stops_level_points=400))
    assert decision.action == "HOLD"
    assert "plan_invalid" in decision.vetoed_by


def test_broker_constraints_validator_is_defence_in_depth():
    from tradingagents.gold.levels import validate_levels_against_broker
    from tradingagents.gold.models import TradePlan

    bad = TradePlan(side="BUY", entry=100.0, stop_loss=99.9,
                    take_profits=[100.1], valid=True, risk_distance=0.1, rr=2.0)
    problems = validate_levels_against_broker(bad, gold_spec(stops_level_points=50))
    assert problems  # the engine's step 9 would veto this plan


# ------------------------------------------------------------- confidence
def test_confidence_floor_blocks_weak_setup():
    # No fundamentals, no news, weak-ish alignment -> below 75.
    decision = eval_buy(engine_cfg=cfg(min_confidence=75.0))
    assert decision.action == "HOLD"
    assert "min_confidence" in decision.vetoed_by
    assert decision.confidence is not None
    assert decision.confidence.total < 75.0


def test_confidence_components_normalized():
    engine = engine_with(cfg(min_confidence=75.0))
    conf = engine.confidence(
        candidate="BUY", mtf=controlled_mtf(),
        fundamentals=strong_fundamentals(), news_fresh=strong_news(),
        plan=eval_buy(engine_cfg=cfg()).plan or
        __import__("tradingagents.gold.levels", fromlist=["build_trade_plan"])
        .build_trade_plan(side="BUY", mtf=controlled_mtf(),
                          market=market_at(104.5), spec=gold_spec(),
                          min_rr=2.0, atr_sl_multiplier=1.5,
                          min_sl_atr_multiple=0.5),
    )
    assert conf.total <= 100.0
    for key, weight in conf.weights.items():
        assert 0.0 <= conf.scores[key] <= weight, key


def test_confidence_weights_must_sum_to_100():
    with pytest.raises(ValueError):
        cfg(weights={"technical": 50.0}).validate()


def test_confidence_display_format():
    engine = engine_with(cfg())
    conf = engine.confidence(
        candidate="BUY", mtf=controlled_mtf(), fundamentals=None,
        news_fresh=[], plan=eval_buy().plan,
    )
    text = conf.display()
    assert "Technical" in text and "Total" in text
    assert "/100" in text


# ----------------------------------------------------- trader level handling
def test_trader_stop_flows_into_plan_when_sane():
    decision = eval_buy(trader_stop=103.0)
    assert decision.tradeable
    assert decision.plan.stop_source == "TRADER"


def test_insane_trader_stop_is_rejected():
    decision = eval_buy(trader_stop=110.0)  # wrong side for BUY
    assert decision.tradeable
    assert decision.plan.stop_source != "TRADER"


# --------------------------------------------------------- engine determinism
def test_engine_is_deterministic():
    d1 = eval_buy(fundamentals=strong_fundamentals(), news_fresh=strong_news())
    d2 = eval_buy(fundamentals=strong_fundamentals(), news_fresh=strong_news())
    assert d1.action == d2.action
    if d1.plan and d2.plan:
        assert d1.plan.entry == d2.plan.entry
        assert d1.plan.stop_loss == d2.plan.stop_loss
        assert d1.plan.take_profits == d2.plan.take_profits
        assert d1.confidence.total == d2.confidence.total
