"""Unit tests for the non-bypassable Risk Engine."""
from __future__ import annotations

import pytest
from trading_engine.risk.engine import (
    AccountState, MarketConditions, ProposedTrade, RiskSettings, evaluate_trade,
)
from trading_engine.risk.position_sizer import SymbolSpec
from datetime import datetime, timezone


def _eurusd_spec() -> SymbolSpec:
    return SymbolSpec(
        name="EURUSD", digits=5, point=0.00001,
        contract_size=100_000, lot_min=0.01, lot_max=100.0, lot_step=0.01,
        tick_size=0.00001, tick_value=1.0,
        currency_base="EUR", currency_profit="USD", currency_margin="EUR",
        spread=0.00012, spread_float=True, stops_level=20, freeze_level=0,
        trade_mode=0, trade_exemode=0, trade_calc_mode=0, swap_mode=0,
        swap_long=0, swap_short=0, visible=True,
    )


def _settings(**overrides) -> RiskSettings:
    base = RiskSettings()
    for k, v in overrides.items():
        setattr(base, k, v)
    return base


def _account(**overrides) -> AccountState:
    a = AccountState(
        balance=10_000.0, equity=10_000.0, margin_free=9_500.0,
        margin_level=400.0, currency="USD",
    )
    for k, v in overrides.items():
        setattr(a, k, v)
    return a


def _market(**overrides) -> MarketConditions:
    m = MarketConditions(
        symbol="EURUSD", bid=1.08000, ask=1.08010, spread_pips=1.0,
        market_open=True, data_fresh=True, session="London",
        high_news_risk=False, volatility_pips=10.0,
    )
    for k, v in overrides.items():
        setattr(m, k, v)
    return m


def test_normal_trade_passes():
    spec = _eurusd_spec()
    proposed = ProposedTrade(
        symbol="EURUSD", direction="BUY",
        entry=1.08000, stop_loss=1.07500, take_profit=1.09250,
        symbol_spec=spec, ai_confidence=85, risk_reward=2.5, strategy_name="trend_following",
    )
    res = evaluate_trade(proposed, _settings(risk_per_trade_pct=1.0), _account(), _market())
    assert res.allowed, res.failures
    assert res.lot > 0
    assert res.estimated_loss_at_sl <= 100.0 + 0.01  # $100 risk (1% of 10k)


def test_spread_too_high_rejects():
    spec = _eurusd_spec()
    proposed = ProposedTrade(
        symbol="EURUSD", direction="BUY",
        entry=1.08000, stop_loss=1.07500, take_profit=1.09250,
        symbol_spec=spec, ai_confidence=85,
    )
    res = evaluate_trade(
        proposed,
        _settings(risk_per_trade_pct=1.0, max_spread_pips_majors=1.5),
        _account(),
        _market(spread_pips=4.0),
    )
    assert not res.allowed
    assert any("spread" in f.lower() for f in res.failures), res.failures


def test_market_closed_rejects():
    spec = _eurusd_spec()
    proposed = ProposedTrade(
        symbol="EURUSD", direction="BUY",
        entry=1.08000, stop_loss=1.07500, take_profit=1.09250,
        symbol_spec=spec, ai_confidence=90,
    )
    res = evaluate_trade(proposed, _settings(risk_per_trade_pct=1.0), _account(), _market(market_open=False))
    assert not res.allowed
    assert any("market" in f.lower() and "closed" in f.lower() for f in res.failures)


def test_stale_data_rejects():
    spec = _eurusd_spec()
    proposed = ProposedTrade(
        symbol="EURUSD", direction="BUY",
        entry=1.08000, stop_loss=1.07500, take_profit=1.09250,
        symbol_spec=spec, ai_confidence=90,
    )
    res = evaluate_trade(proposed, _settings(risk_per_trade_pct=1.0), _account(), _market(data_fresh=False))
    assert not res.allowed
    assert any("stale" in f.lower() for f in res.failures)


def test_kill_switch_rejects():
    spec = _eurusd_spec()
    proposed = ProposedTrade(
        symbol="EURUSD", direction="BUY",
        entry=1.08000, stop_loss=1.07500, take_profit=1.09250,
        symbol_spec=spec, ai_confidence=90,
    )
    res = evaluate_trade(proposed, _settings(risk_per_trade_pct=1.0), _account(kill_switch_active=True), _market())
    assert not res.allowed
    assert any("kill" in f.lower() for f in res.failures)


def test_min_confidence_rejects():
    spec = _eurusd_spec()
    proposed = ProposedTrade(
        symbol="EURUSD", direction="BUY",
        entry=1.08000, stop_loss=1.07500, take_profit=1.09250,
        symbol_spec=spec, ai_confidence=55,
    )
    res = evaluate_trade(proposed, _settings(risk_per_trade_pct=1.0, min_ai_confidence=70), _account(), _market())
    assert not res.allowed
    assert any("confidence" in f.lower() for f in res.failures)


def test_sl_required_rejects_missing_sl():
    spec = _eurusd_spec()
    proposed = ProposedTrade(
        symbol="EURUSD", direction="BUY",
        entry=1.08000, stop_loss=None, take_profit=1.09250,
        symbol_spec=spec, ai_confidence=90,
    )
    res = evaluate_trade(proposed, _settings(stop_loss_required=True), _account(), _market())
    assert not res.allowed
    assert any("stop" in f.lower() for f in res.failures)


def test_tp_required_rejects_missing_tp():
    spec = _eurusd_spec()
    proposed = ProposedTrade(
        symbol="EURUSD", direction="BUY",
        entry=1.08000, stop_loss=1.07500, take_profit=None,
        symbol_spec=spec, ai_confidence=90,
    )
    res = evaluate_trade(proposed, _settings(take_profit_required=True), _account(), _market())
    assert not res.allowed
    assert any("take" in f.lower() and "profit" in f.lower() for f in res.failures)


def test_buy_sl_above_entry_rejected():
    spec = _eurusd_spec()
    proposed = ProposedTrade(
        symbol="EURUSD", direction="BUY",
        entry=1.08000, stop_loss=1.08500, take_profit=1.09000,
        symbol_spec=spec, ai_confidence=80,
    )
    res = evaluate_trade(proposed, _settings(risk_per_trade_pct=1.0), _account(), _market())
    assert not res.allowed
    assert any("sl" in f.lower() and "below" in f.lower() for f in res.failures)


def test_max_daily_loss_blocks_new_trade():
    spec = _eurusd_spec()
    proposed = ProposedTrade(
        symbol="EURUSD", direction="BUY",
        entry=1.08000, stop_loss=1.07500, take_profit=1.09250,
        symbol_spec=spec, ai_confidence=90,
    )
    res = evaluate_trade(
        proposed,
        _settings(risk_per_trade_pct=1.0, max_daily_loss_pct=2.0),
        _account(daily_pnl=-250.0),  # 2.5% drawdown on 10k > 2% limit
        _market(),
    )
    assert not res.allowed
    assert any("daily" in f.lower() and "loss" in f.lower() for f in res.failures)


def test_max_simultaneous_trades_blocks():
    spec = _eurusd_spec()
    proposed = ProposedTrade(
        symbol="EURUSD", direction="BUY",
        entry=1.08000, stop_loss=1.07500, take_profit=1.09250,
        symbol_spec=spec, ai_confidence=90,
    )
    res = evaluate_trade(
        proposed,
        _settings(risk_per_trade_pct=1.0, max_simultaneous_trades=2),
        _account(open_trades_count=2, open_positions=[{"symbol": "EURUSD"}, {"symbol": "GBPUSD"}]),
        _market(),
    )
    assert not res.allowed
    assert any("simultaneous" in f.lower() or "max" in f.lower() for f in res.failures)


def test_min_rr_blocks_bad_rr_setup():
    spec = _eurusd_spec()
    # Only 10 pips SL and 10 pips TP → 1:1 RR, below min 2
    proposed = ProposedTrade(
        symbol="EURUSD", direction="BUY",
        entry=1.08000, stop_loss=1.07990, take_profit=1.08010,
        symbol_spec=spec, ai_confidence=90,
    )
    res = evaluate_trade(proposed, _settings(risk_per_trade_pct=0.1, min_risk_reward=2.0), _account(), _market())
    assert not res.allowed
    assert any("risk" in f.lower() and "reward" in f.lower() for f in res.failures)


def test_news_filter_blocks_when_enabled():
    spec = _eurusd_spec()
    proposed = ProposedTrade(
        symbol="EURUSD", direction="BUY",
        entry=1.08000, stop_loss=1.07500, take_profit=1.09250,
        symbol_spec=spec, ai_confidence=90,
    )
    res = evaluate_trade(
        proposed,
        _settings(news_filter_enabled=True),
        _account(),
        _market(high_news_risk=True),
    )
    assert not res.allowed
    assert any("news" in f.lower() for f in res.failures)


def test_mt5_disconnected_blocks_live():
    spec = _eurusd_spec()
    proposed = ProposedTrade(
        symbol="EURUSD", direction="BUY",
        entry=1.08000, stop_loss=1.07500, take_profit=1.09250,
        symbol_spec=spec, ai_confidence=90,
    )
    res = evaluate_trade(proposed, _settings(risk_per_trade_pct=1.0), _account(mt5_connected=False), _market())
    assert not res.allowed
    assert any("mt5" in f.lower() or "connected" in f.lower() for f in res.failures)


def test_zero_free_margin_blocks():
    spec = _eurusd_spec()
    proposed = ProposedTrade(
        symbol="EURUSD", direction="BUY",
        entry=1.08000, stop_loss=1.07500, take_profit=1.09250,
        symbol_spec=spec, ai_confidence=90,
    )
    res = evaluate_trade(proposed, _settings(risk_per_trade_pct=1.0), _account(margin_free=0.0), _market())
    assert not res.allowed
    assert any("margin" in f.lower() for f in res.failures)


def test_duplicate_position_blocked():
    spec = _eurusd_spec()
    proposed = ProposedTrade(
        symbol="EURUSD", direction="BUY",
        entry=1.08000, stop_loss=1.07500, take_profit=1.09250,
        symbol_spec=spec, ai_confidence=90,
    )
    res = evaluate_trade(
        proposed,
        _settings(risk_per_trade_pct=1.0, one_trade_per_symbol=True),
        _account(open_positions=[{"symbol": "EURUSD"}]),
        _market(),
    )
    assert not res.allowed
    assert any("duplicate" in f.lower() or "already" in f.lower() for f in res.failures)
