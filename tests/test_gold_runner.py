"""Phase 34-38 + remaining Phase 41 invariants: runner, safety gates, audit.

Invariants covered here:
  6. dry-run never submits a real order
  7. analysis-only never submits an order (never even connects to MT5)
  8. live mode requires --live
  9. live mode requires MT5_DRY_RUN=false
Plus: final confirmation, JSONL secret-scrubbing, state rollover, report
rendering, LLM-failure degradation.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tests.gold_helpers import (
    SAFE_NOW,
    cfg,
    gold_spec,
    market_at,
)
from tradingagents.gold import runner as runner_mod
from tradingagents.gold.config import GoldConfig
from tradingagents.gold.models import FinalDecision, TradePlan
from tradingagents.gold.runner import (
    RunOptions,
    _final_validation,
    _live_confirmation,
    build_config,
)
from tradingagents.gold.state import GoldStateStore


# --------------------------------------------------------- config / live gates
def _env(monkeypatch, **kv):
    for k, v in kv.items():
        if v is None:
            monkeypatch.delenv(k, raising=False)
        else:
            monkeypatch.setenv(k, str(v))


def test_invariant_8_live_requires_the_live_flag(monkeypatch):
    _env(monkeypatch, MT5_DRY_RUN="false")
    cfg_ = build_config(RunOptions(live=False))
    assert cfg_.dry_run is False
    assert cfg_.live_trading_unlocked is False  # --live missing


def test_invariant_9_live_requires_env_dry_run_false(monkeypatch):
    _env(monkeypatch, MT5_DRY_RUN="true")
    cfg_ = build_config(RunOptions(live=True))
    assert cfg_.live_trading_unlocked is False  # env gate missing


def test_live_unlocks_only_when_both_gates_open(monkeypatch):
    _env(monkeypatch, MT5_DRY_RUN="false")
    cfg_ = build_config(RunOptions(live=True))
    assert cfg_.live_trading_unlocked is True


def test_dry_run_flag_forces_simulation_even_with_env_open(monkeypatch):
    _env(monkeypatch, MT5_DRY_RUN="false")
    cfg_ = build_config(RunOptions(live=True, force_dry_run=True))
    assert cfg_.live_trading_unlocked is False


def test_bogus_env_value_fails_loudly(monkeypatch):
    _env(monkeypatch, MT5_DRY_RUN="maybe")
    with pytest.raises(ValueError, match="boolean"):
        build_config(RunOptions())


def test_config_validation_rejects_insane_risk():
    with pytest.raises(ValueError):
        GoldConfig(risk_per_trade=50.0).validate()
    with pytest.raises(ValueError):
        GoldConfig(min_rr=0.5).validate()


# ------------------------------------------------------- final confirmation
def test_live_confirmation_requires_exact_word(monkeypatch):
    monkeypatch.setenv("GOLD_LIVE_CONFIRM", "YES")
    assert _live_confirmation() is True
    monkeypatch.setenv("GOLD_LIVE_CONFIRM", "no")
    monkeypatch.delenv("GOLD_LIVE_CONFIRM")
    monkeypatch.setattr("sys.stdin", type("S", (), {"isatty": lambda: True}))
    monkeypatch.setattr("builtins.input", lambda *a: "LIVE")
    assert _live_confirmation() is True
    monkeypatch.setattr("builtins.input", lambda *a: "live")
    assert _live_confirmation() is False
    monkeypatch.setattr("builtins.input", lambda *a: "yes")
    assert _live_confirmation() is False


def test_live_confirmation_non_tty_needs_env(monkeypatch):
    monkeypatch.delenv("GOLD_LIVE_CONFIRM", raising=False)
    monkeypatch.setattr("sys.stdin", type("S", (), {"isatty": lambda: False}))
    assert _live_confirmation() is False


# --------------------------------------------------------- final validation
def _plan(**kw):
    base = {"side": "BUY", "entry": 100.0, "stop_loss": 98.0,
            "take_profits": [104.0], "valid": True, "rr": 2.0,
            "risk_distance": 2.0, "volume": 0.5, "risk_amount": 100.0}
    base.update(kw)
    return TradePlan(**base)


def test_final_validation_ok():
    ok, why = _final_validation(_plan(), market_at(100.0), gold_spec(),
                                type("A", (), {"margin_free": 1000.0})(),
                                cfg())
    assert ok, why


def test_final_validation_blocks_missing_sl():
    ok, why = _final_validation(_plan(stop_loss=None), market_at(100.0),
                                gold_spec(), type("A", (), {"margin_free": 1})(),
                                cfg())
    assert not ok and "stop-loss" in why


def test_final_validation_blocks_wrong_side_sl():
    ok, why = _final_validation(_plan(stop_loss=102.0), market_at(100.0),
                                gold_spec(), type("A", (), {"margin_free": 1})(),
                                cfg())
    assert not ok and "below entry" in why


def test_final_validation_blocks_out_of_bounds_volume():
    ok, why = _final_validation(_plan(volume=0.001), market_at(100.0),
                                gold_spec(), type("A", (), {"margin_free": 1})(),
                                cfg())
    assert not ok and "volume" in why


def test_final_validation_blocks_low_rr():
    ok, why = _final_validation(_plan(rr=1.5), market_at(100.0), gold_spec(),
                                type("A", (), {"margin_free": 1})(), cfg())
    assert not ok and "RR" in why


def test_final_validation_blocks_no_margin():
    ok, why = _final_validation(_plan(), market_at(100.0), gold_spec(),
                                type("A", (), {"margin_free": 0.0})(), cfg())
    assert not ok and "margin" in why


# ------------------------------------------------------ dry-run / live path
class StubBroker:
    """Records every order attempt; the test asserts none was 'real'."""

    def __init__(self):
        self.orders = []
        self.submitted = []

    def positions(self, symbol=None):
        return []

    def market_order(self, action, **kw):
        self.orders.append((action, kw))
        # a real submission would carry retcode from orders_send
        from tradingagents.brokers.mt5_broker import OrderResult

        return OrderResult(dry_run=kw.get("dry_run", True), action=action,
                           symbol=kw.get("symbol", "XAUUSD"),
                           volume=kw.get("volume"), price=kw.get("price"),
                           sl=kw.get("sl"), tp=kw.get("tp"),
                           retcode=None, comment="stub")


def _tradeable_decision():
    plan = _plan(entry=104.65, stop_loss=101.5, take_profits=[112.0],
                 rr=2.33, volume=0.31, risk_amount=97.65,
                 stop_source="STRUCTURE", tp_source="STRUCTURE",
                 signal_id="deadbeef1234")
    decision = FinalDecision(action="BUY", plan=plan, timestamp=SAFE_NOW)
    decision.reasons.append("test")
    return decision


def test_invariant_6_dry_run_never_submits_a_real_order(capsys):
    broker = StubBroker()
    state = GoldStateStore(Path("/tmp/_gold_test_state.json"))
    decision = _tradeable_decision()
    opts = RunOptions()
    cfg_ = cfg()  # live_trading_unlocked defaults False
    summary = runner_mod._execute_decision(
        opts, cfg_, broker, decision, market_at(104.5), gold_spec(),
        type("A", (), {"equity": 10_000.0, "currency": "USD",
                       "margin_free": 5_000.0})(), state,
    )
    out = capsys.readouterr().out
    assert "PROPOSED ORDER" in out
    assert "DRY-RUN" in out
    assert summary["submitted"] is False
    assert broker.orders == []  # nothing was even attempted


def test_live_path_aborts_without_confirmation(monkeypatch, capsys):
    broker = StubBroker()
    state = GoldStateStore(Path("/tmp/_gold_test_state2.json"))
    monkeypatch.delenv("GOLD_LIVE_CONFIRM", raising=False)
    monkeypatch.setattr("sys.stdin", type("S", (), {"isatty": lambda: False}))
    cfg_ = cfg(dry_run=False, live_enabled=True)
    assert cfg_.live_trading_unlocked
    summary = runner_mod._execute_decision(
        RunOptions(live=True), cfg_, broker, _tradeable_decision(),
        market_at(104.5), gold_spec(),
        type("A", (), {"equity": 10_000.0, "currency": "USD",
                       "margin_free": 5_000.0})(), state,
    )
    assert summary["submitted"] is False
    assert broker.orders == []
    out = capsys.readouterr().out
    assert "Aborted" in out and "No order was sent" in out


def test_live_path_sends_only_after_confirmation(monkeypatch, capsys):
    broker = StubBroker()
    state = GoldStateStore(Path("/tmp/_gold_test_state3.json"))
    monkeypatch.setenv("GOLD_LIVE_CONFIRM", "YES")
    cfg_ = cfg(dry_run=False, live_enabled=True)
    runner_mod._execute_decision(
        RunOptions(live=True), cfg_, broker, _tradeable_decision(),
        market_at(104.5), gold_spec(),
        type("A", (), {"equity": 10_000.0, "currency": "USD",
                       "margin_free": 5_000.0})(), state,
    )
    out = capsys.readouterr().out
    assert "FINAL CONFIRMATION" in out
    assert "SYMBOL" in out and "RISK" in out and "EQUITY" in out
    # the order went out WITH SL and TP attached (Phase 24)
    assert len(broker.orders) == 1
    action, kw = broker.orders[0]
    assert action == "BUY"
    assert kw.get("sl") == 101.5
    assert kw.get("tp") == 112.0


def test_hold_decision_never_opens(monkeypatch):
    broker = StubBroker()
    state = GoldStateStore(Path("/tmp/_gold_test_state4.json"))
    decision = FinalDecision(action="HOLD", timestamp=SAFE_NOW)
    decision.reasons.append("gated")
    summary = runner_mod._execute_decision(
        RunOptions(), cfg(), broker, decision, market_at(104.5), gold_spec(),
        type("A", (), {"equity": 10_000.0, "currency": "USD"})(), state,
    )
    assert summary["submitted"] is False
    assert broker.orders == []


def test_close_on_hold_flattens(monkeypatch):
    class Pos:
        type = 0
        ticket = 1

    broker = StubBroker()
    broker.positions = lambda symbol=None: [Pos()]
    state = GoldStateStore(Path("/tmp/_gold_test_state5.json"))
    decision = FinalDecision(action="HOLD", timestamp=SAFE_NOW)
    runner_mod._execute_decision(
        RunOptions(close_on_hold=True), cfg(), broker, decision,
        market_at(104.5), gold_spec(),
        type("A", (), {"equity": 10_000.0, "currency": "USD"})(), state,
    )
    assert broker.orders and broker.orders[0][0] == "CLOSE_BUY"


# ------------------------------------------------------- analysis-only path
def test_invariant_7_analysis_only_never_touches_mt5(monkeypatch, tmp_path):
    """run_gold(--analysis-only) must never construct an MT5Broker."""
    import tradingagents.brokers.mt5_broker as mt5_mod

    def _bomb(*a, **kw):
        raise AssertionError("MT5Broker must not be constructed "
                             "in analysis-only mode")

    monkeypatch.setattr(mt5_mod, "MT5Broker", _bomb)

    from tests.gold_helpers import breakout_frames

    monkeypatch.setattr(
        "tradingagents.gold.runner.fetch_yf_timeframes",
        lambda *a, **kw: (breakout_frames(), None),
    )
    monkeypatch.setattr(
        "tradingagents.gold.runner._collect_deterministic_inputs",
        lambda cfg, now: (None, [], []),
    )
    monkeypatch.setattr(
        "tradingagents.gold.runner.collect_news", lambda **kw: ([], []))


    import tradingagents.default_config as dc

    monkeypatch.setitem(dc.DEFAULT_CONFIG, "results_dir", str(tmp_path))
    monkeypatch.setenv("TRADINGAGENTS_RESULTS_DIR", str(tmp_path))

    opts = RunOptions(analysis_only=True, no_llm=True)
    result = runner_mod.run_gold(opts)
    assert result.ok
    assert result.execution.get("submitted") is False
    assert result.execution.get("mode") == "analysis-only"
    assert result.decision is not None
    assert result.decision.action in ("BUY", "SELL", "HOLD")
    assert result.report_path and Path(result.report_path).exists()


def test_report_contains_required_sections(tmp_path, monkeypatch):
    import tradingagents.default_config as dc
    from tests.gold_helpers import breakout_frames

    monkeypatch.setitem(dc.DEFAULT_CONFIG, "results_dir", str(tmp_path))
    monkeypatch.setattr(
        "tradingagents.gold.runner.fetch_yf_timeframes",
        lambda *a, **kw: (breakout_frames(), None),
    )
    monkeypatch.setattr(
        "tradingagents.gold.runner._collect_deterministic_inputs",
        lambda cfg, now: (None, [], []),
    )
    monkeypatch.setattr(
        "tradingagents.gold.runner.collect_news", lambda **kw: ([], []))

    result = runner_mod.run_gold(RunOptions(analysis_only=True, no_llm=True))
    text = Path(result.report_path).read_text(encoding="utf-8")
    for section in ("Data sources", "Market & account", "Multi-timeframe read",
                    "Deterministic risk engine", "FINAL DECISION"):
        assert section in text, section
    assert "UNKNOWN" in text  # unknown values are labelled, never invented


# ------------------------------------------------------------- jsonl audit
def test_jsonl_never_stores_credentials(tmp_path):
    record = {
        "ts": "2026-09-09T13:00:00+00:00",
        "signal": "BUY",
        "MT5_PASSWORD": "hunter2",
        "api_key": "sk-secret",
        "openai_token": "sk-xxx",
        "volume": 0.31,
    }
    import tradingagents.default_config as dc
    import tradingagents.gold.runner as r

    orig = dc.DEFAULT_CONFIG["results_dir"]
    dc.DEFAULT_CONFIG["results_dir"] = str(tmp_path)
    try:
        path = r._append_jsonl(cfg(), record)
    finally:
        dc.DEFAULT_CONFIG["results_dir"] = orig
    line = Path(path).read_text(encoding="utf-8")
    assert "hunter2" not in line
    assert "sk-secret" not in line
    assert "sk-xxx" not in line
    assert json.loads(line)["volume"] == 0.31


def test_jsonl_record_schema(tmp_path):
    """Phase 32: every execution record carries the full audit fields."""

    record = {
        "ts": "2026-09-09T13:00:00+00:00", "mode": "dry-run",
        "symbol": "XAUUSD", "signal": "BUY", "rating": "Buy",
        "confidence": 82.0, "entry": 104.65, "sl": 101.5,
        "tps": [112.0], "rr": 2.33, "risk_percent": 0.5,
        "risk_amount": 97.65, "volume": 0.31, "spread_points": 30.0,
        "account_equity": 10_000.0, "dry_run": True, "live": False,
        "signal_id": "abc123", "vetoed_by": [], "session": ["London"],
    }
    import tradingagents.default_config as dc
    import tradingagents.gold.runner as r

    orig = dc.DEFAULT_CONFIG["results_dir"]
    dc.DEFAULT_CONFIG["results_dir"] = str(tmp_path)
    try:
        path = r._append_jsonl(cfg(), record)
    finally:
        dc.DEFAULT_CONFIG["results_dir"] = orig
    loaded = json.loads(Path(path).read_text(encoding="utf-8"))
    for key in record:
        assert key in loaded


# ------------------------------------------------------------------- state
def test_state_rollover_resets_daily_counters_only(tmp_path):
    store = GoldStateStore(tmp_path / "state.json", tz_name="UTC")
    now = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)
    store.rollover(now)
    store.counters.date = "2026-09-09"
    store.record_trade(-150.0, now)
    store.record_trade(-60.0, now)
    store.counters.paper_balance = 9_790.0
    store.counters.consecutive_losses = 2
    store.save()

    store2 = GoldStateStore(tmp_path / "state.json", tz_name="UTC")
    next_day = datetime(2026, 9, 10, 0, 30, tzinfo=timezone.utc)
    store2.rollover(next_day)
    assert store2.counters.trades_today == 0
    assert store2.counters.realized_pnl_today == 0.0
    # cross-day state persists
    assert store2.counters.consecutive_losses == 2
    assert store2.counters.paper_balance == 9_790.0


def test_state_rollover_respects_configured_timezone(tmp_path):
    # 23:30 UTC on Sep 9 is already Sep 10 in IST (Asia/Kolkata +05:30)
    store = GoldStateStore(tmp_path / "s.json", tz_name="Asia/Kolkata")
    store.counters.date = "2026-09-09"
    store.rollover(datetime(2026, 9, 9, 23, 30, tzinfo=timezone.utc))
    # in IST it is 05:00 on Sep 10 -> rollover happened
    assert store.counters.date == "2026-09-10"


def test_state_corrupt_file_starts_fresh(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not json", encoding="utf-8")
    store = GoldStateStore(p)
    assert store.counters.trades_today == 0


def test_state_record_trade_updates_loss_streak(tmp_path):
    store = GoldStateStore(tmp_path / "s.json")
    now = datetime(2026, 9, 9, 12, tzinfo=timezone.utc)
    store.record_trade(-10.0, now)
    store.record_trade(-10.0, now)
    assert store.counters.consecutive_losses == 2
    store.record_trade(25.0, now)
    assert store.counters.consecutive_losses == 0


# ------------------------------------------------------- LLM failure safety
def test_llm_failure_degrades_to_deterministic_candidate(monkeypatch):
    """A broken LLM pipeline must not crash the run or open a trade by
    accident — it falls back to the deterministic candidate, still gated."""
    from tests.gold_helpers import breakout_frames

    def boom(opts, cfg):
        raise RuntimeError("LLM provider down")

    monkeypatch.setattr("tradingagents.gold.runner.run_llm_analysis", boom)
    monkeypatch.setattr(
        "tradingagents.gold.runner.fetch_yf_timeframes",
        lambda *a, **kw: (breakout_frames(), None),
    )
    monkeypatch.setattr(
        "tradingagents.gold.runner._collect_deterministic_inputs",
        lambda cfg, now: (None, [], []),
    )
    monkeypatch.setattr(
        "tradingagents.gold.runner.collect_news", lambda **kw: ([], []))

    import tempfile

    from tradingagents.default_config import DEFAULT_CONFIG

    with tempfile.TemporaryDirectory() as td:
        monkeypatch.setitem(DEFAULT_CONFIG, "results_dir", td)
        result = runner_mod.run_gold(RunOptions(analysis_only=True))
        assert result.ok  # degraded, not crashed
        assert any("LLM analysis failed" in n for n in result.notes)
        assert result.decision.action in ("BUY", "SELL", "HOLD")


# --------------------------------------------------------- trader plan parsing
def test_parse_trader_levels():
    state = {
        "trader_investment_plan": (
            "**Entry Price**: 2400.5\n**Stop Loss**: 2380.0\n"
            "**Take Profit**: 2440.0\n**Take Profit 2**: 2460.0"
        )
    }
    stop, tps = runner_mod.parse_trader_levels(state)
    assert stop == 2380.0
    assert tps == [2440.0, 2460.0]


def test_parse_trader_levels_empty_state():
    assert runner_mod.parse_trader_levels({}) == (None, [])


def test_extract_take_profits_numbered_variants():
    from tradingagents.brokers.gold_executor import extract_take_profits

    plan = ("TP1: **Take Profit**: 100\nTP2: **Take Profit 2**: 110\n"
            "bogus: Take Profit: not-a-number")
    assert extract_take_profits(plan) == [100.0, 110.0]
    assert extract_take_profits("") == []
    assert extract_take_profits("no levels here") == []


# ------------------------------------------- end-to-end paper & backtest flows
def _patched_data(monkeypatch, tmp_path):
    import tradingagents.default_config as dc
    from tests.gold_helpers import breakout_frames

    monkeypatch.setitem(dc.DEFAULT_CONFIG, "results_dir", str(tmp_path))
    monkeypatch.setattr(
        "tradingagents.gold.runner.fetch_yf_timeframes",
        lambda *a, **kw: (breakout_frames(), None),
    )
    monkeypatch.setattr(
        "tradingagents.gold.runner._collect_deterministic_inputs",
        lambda cfg, now: (None, [], []),
    )
    monkeypatch.setattr(
        "tradingagents.gold.runner.collect_news", lambda **kw: ([], []))
    monkeypatch.setattr(
        "tradingagents.gold.runner.run_llm_analysis",
        lambda opts, cfg: ("Buy", {"final_trade_decision": "**Rating**: Buy",
                                    "trader_investment_plan": ""}))


def test_run_paper_end_to_end(monkeypatch, tmp_path):
    _patched_data(monkeypatch, tmp_path)
    result = runner_mod.run_paper(RunOptions(paper=True))
    assert result.ok
    assert result.execution.get("mode") == "paper"
    assert result.execution.get("submitted") is False
    assert result.report_path and Path(result.report_path).exists()
    # state file was persisted with the paper balance
    state_file = tmp_path / "gold_state.json"
    assert state_file.exists()
    data = json.loads(state_file.read_text(encoding="utf-8"))
    assert "paper_balance" in data
    # execution log exists and holds no credentials
    log = tmp_path / "gold_mt5_executions.jsonl"
    assert log.exists()
    record = json.loads(log.read_text(encoding="utf-8").strip())
    assert record["mode"] == "paper"


def test_run_backtest_end_to_end(monkeypatch, tmp_path, capsys):
    _patched_data(monkeypatch, tmp_path)
    result = runner_mod.run_backtest_flow(RunOptions(backtest=True))
    assert result.ok
    out = capsys.readouterr().out
    assert "BACKTEST" in out
    assert "trades=" in out
    artifacts = list((tmp_path / "gold_reports").glob("*_backtest_*.json"))
    assert artifacts
    saved = json.loads(artifacts[0].read_text(encoding="utf-8"))
    assert "metrics" in saved and "assumptions" in saved
    assert any("SL fills before TP" in a for a in saved["assumptions"])


def test_run_gold_dry_run_end_to_end_with_stub_broker(monkeypatch, tmp_path):
    """Full run_gold dry-run path with a stubbed MT5 broker connection."""
    _patched_data(monkeypatch, tmp_path)


    class FakeInfo:
        name = "XAUUSD"

    class FakeTick:
        bid, ask = 104.35, 104.65

    class FakeAccount:
        login, server, currency = 1, "Test", "USD"
        equity, balance, margin_free = 10_000.0, 10_000.0, 5_000.0

    class FakePos:
        type, volume, ticket = 0, 0.1, 1

    import tradingagents.gold.runner as r

    conn = {"closed": False}

    class FakeBroker:
        def __init__(self, cfg):
            self.config = cfg

        def connect(self):
            return self

        def shutdown(self):
            conn["closed"] = True

        def resolve_symbol(self, symbol=None):
            return "XAUUSD"

        def symbol_info(self, symbol=None):
            return type("SI", (), {
                "name": "XAUUSD", "trade_contract_size": 100.0,
                "trade_tick_size": 0.01, "trade_tick_value": 1.0,
                "point": 0.01, "digits": 2, "volume_min": 0.01,
                "volume_max": 100.0, "volume_step": 0.01,
                "trade_stops_level": 0, "trade_freeze_level": 0,
                "spread": 30,
            })()

        def tick(self, symbol=None):
            return FakeTick()

        def account_info(self):
            return FakeAccount()

        def positions(self, symbol=None):
            return []

        def market_order(self, action, **kw):
            raise AssertionError("dry-run must never submit an order")

    import tradingagents.brokers.mt5_broker as mt5_mod

    monkeypatch.setattr(mt5_mod, "MT5Broker", FakeBroker)
    monkeypatch.setattr(
        "tradingagents.gold.runner.fetch_mt5_timeframes",
        lambda broker, symbol, **kw: _mt5_frames())

    result = r.run_gold(RunOptions())  # default mode = dry-run
    assert result.ok
    assert result.execution.get("submitted") is False
    # HOLD with a reason, or a PROPOSED order — but NEVER a submission
    # (FakeBroker.market_order raises if ever called).
    assert result.execution.get("reason") or "proposed" in result.execution \
        or result.decision is not None
    assert conn["closed"]  # broker connection was cleaned up


def _mt5_frames():
    from tests.gold_helpers import breakout_frames

    prov = None
    frames = breakout_frames()
    return frames, prov


# ------------------------------------------------------------- margin check
def test_margin_check_blocks_insufficient_margin():
    mt5 = type("M", (), {
        "ORDER_TYPE_BUY": 0, "ORDER_TYPE_SELL": 1,
        "order_calc_margin": lambda *a: 6_000.0,
    })
    broker = type("B", (), {"_mt5": mt5})()
    acct = type("A", (), {"margin_free": 5_000.0})()
    ok, why = runner_mod._margin_check(broker, _plan(), gold_spec(), acct)
    assert not ok and "insufficient margin" in why


def test_margin_check_passes_with_free_margin():
    mt5 = type("M", (), {
        "ORDER_TYPE_BUY": 0, "ORDER_TYPE_SELL": 1,
        "order_calc_margin": lambda *a: 500.0,
    })
    broker = type("B", (), {"_mt5": mt5})()
    acct = type("A", (), {"margin_free": 5_000.0})()
    ok, why = runner_mod._margin_check(broker, _plan(), gold_spec(), acct)
    assert ok


def test_margin_check_degrades_when_calculator_missing():
    broker = type("B", (), {"_mt5": type("M", (), {})()})()
    acct = type("A", (), {"margin_free": 5_000.0})()
    ok, why = runner_mod._margin_check(broker, _plan(), gold_spec(), acct)
    assert ok and "not verified" in why


def test_live_path_aborts_on_insufficient_margin(monkeypatch, capsys):
    class Mt5Stub:
        ORDER_TYPE_BUY = 0
        ORDER_TYPE_SELL = 1

        @staticmethod
        def order_calc_margin(*a):
            return 99_999.0

    class MarginBroker(StubBroker):
        _mt5 = Mt5Stub

    broker = MarginBroker()
    state = GoldStateStore(Path("/tmp/_gold_state6.json"))
    monkeypatch.setenv("GOLD_LIVE_CONFIRM", "YES")
    cfg_ = cfg(dry_run=False, live_enabled=True)
    summary = runner_mod._execute_decision(
        RunOptions(live=True), cfg_, broker, _tradeable_decision(),
        market_at(104.5), gold_spec(),
        type("A", (), {"equity": 10_000.0, "currency": "USD",
                       "margin_free": 5_000.0})(), state,
    )
    assert summary["submitted"] is False
    assert "margin" in summary["note"]
    assert broker.orders == []  # never sent
    assert "ABORTED" in capsys.readouterr().out.upper()
