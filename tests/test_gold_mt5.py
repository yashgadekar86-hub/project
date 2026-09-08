"""Tests for the gold/commodity pipeline and the MetaTrader 5 integration."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from cli.models import AssetType
from cli.utils import detect_asset_type
from tradingagents.brokers.gold_executor import (
    RATING_TO_DIRECTION,
    GoldExecutor,
    GoldExecutorConfig,
    extract_price_levels,
)
from tradingagents.brokers.mt5_broker import MT5Broker, MT5Config, OrderResult
from tradingagents.gold_config import build_gold_config, is_gold_symbol

# ---------------------------------------------------------------------------
# Symbol / asset-type classification
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ticker",
    ["XAUUSD", "xauusd", "GOLD", "GC=F", "XAU"],
)
def test_gold_classified_as_commodity(ticker):
    assert detect_asset_type(ticker) == AssetType.COMMODITY


@pytest.mark.parametrize(
    "ticker",
    ["BTC-USD", "BTCUSD", "ETH-USDT"],
)
def test_crypto_still_classified_as_crypto(ticker):
    assert detect_asset_type(ticker) == AssetType.CRYPTO


@pytest.mark.parametrize(
    "ticker",
    ["SPY", "AAPL", "NVDA", "0700.HK"],
)
def test_equities_still_classified_as_stock(ticker):
    assert detect_asset_type(ticker) == AssetType.STOCK


@pytest.mark.parametrize(
    "symbol,expected",
    [
        ("XAUUSD", True),
        ("GC=F", True),
        ("GOLD", True),
        ("xau", True),
        ("SPY", False),
        ("AAPL", False),
        ("BTC-USD", False),
        ("", False),
        (None, False),
    ],
)
def test_is_gold_symbol(symbol, expected):
    assert is_gold_symbol(symbol) is expected


# ---------------------------------------------------------------------------
# Gold config overlay
# ---------------------------------------------------------------------------


def test_build_gold_config_swaps_news_queries():
    cfg = build_gold_config({
        "global_news_queries": ["S&P 500 earnings"],
        "global_news_lookback_days": 7,
        "benchmark_ticker": None,
    })
    assert any("gold" in q.lower() for q in cfg["global_news_queries"])
    assert "S&P 500 earnings" not in cfg["global_news_queries"]


# ---------------------------------------------------------------------------
# Trader plan parsing
# ---------------------------------------------------------------------------


def test_extract_price_levels_full():
    plan = (
        "**Action**: Buy\n\n**Reasoning**: momentum\n\n"
        "**Entry Price**: 2,412.50\n\n**Stop Loss**: 2380.0\n"
    )
    entry, stop = extract_price_levels(plan)
    assert entry == 2412.50
    assert stop == 2380.0


def test_extract_price_levels_absent():
    assert extract_price_levels("") == (None, None)
    assert extract_price_levels("**Action**: Hold") == (None, None)


# ---------------------------------------------------------------------------
# Rating -> direction mapping
# ---------------------------------------------------------------------------


def _executor(allow_short=False, min_strength="Overweight"):
    broker = MagicMock(spec=MT5Broker)
    broker.config = MT5Config(dry_run=True)
    return GoldExecutor(broker, GoldExecutorConfig(
        allow_short=allow_short, min_strength_to_trade=min_strength,
    ))


@pytest.mark.parametrize("rating,direction", [
    ("Buy", "BUY"),
    ("Overweight", "BUY"),
    ("Hold", "HOLD"),
    ("Underweight", "SELL"),
    ("Sell", "SELL"),
])
def test_rating_to_direction(rating, direction):
    assert RATING_TO_DIRECTION[rating] == direction


@pytest.mark.parametrize("decision,expected", [
    ("**Rating**: Buy", "BUY"),
    ("**Rating**: Overweight", "BUY"),
    ("**Rating**: Hold", "HOLD"),
    ("**Rating**: Sell", "SELL"),
    ("**Rating**: Underweight", "SELL"),
    ("garbage no rating here at all", None),
])
def test_decision_to_direction(decision, expected):
    assert _executor().decision_to_direction(decision) == expected


def test_review_signal_never_trades():
    # A decision with no parseable rating yields None -> no orders.
    ex = _executor()
    assert ex.decision_to_direction("we could not decide anything") is None


def test_min_strength_blocks_overweight_when_set_to_buy():
    ex = _executor(min_strength="Buy")
    assert ex.decision_to_direction("**Rating**: Overweight") == "HOLD"
    assert ex.decision_to_direction("**Rating**: Buy") == "BUY"


# ---------------------------------------------------------------------------
# Executor behaviour with a stub broker
# ---------------------------------------------------------------------------


class StubBroker:
    def __init__(self, positions=None, dry_run=True):
        self.config = MT5Config(dry_run=dry_run)
        self._positions = positions or []
        self.orders = []
        self.closed = []

    def resolve_symbol(self):
        return "XAUUSD"

    def positions(self, sym):
        return self._positions

    def tick(self, sym):
        return SimpleNamespace(bid=2400.0, ask=2400.5)

    def symbol_info(self, sym):
        return _fake_symbol_info()

    def calculate_volume(self, stop_distance, sym):
        return 0.05

    def _default_stop_distance(self, tick, info):
        return 12.0

    def market_order(self, action, volume=None, sl=None, tp=None, price=None,
                     symbol=None, comment=""):
        if action.startswith("CLOSE_"):
            self.closed.append(action)
            res = OrderResult(dry_run=self.config.dry_run, action=action,
                              symbol=symbol, comment="closed")
        else:
            res = OrderResult(dry_run=self.config.dry_run, action=action,
                              symbol=symbol, volume=volume, sl=sl, tp=tp,
                              price=price, comment="opened")
        self.orders.append(res)
        return res


def test_buy_signal_opens_long():
    broker = StubBroker()
    ex = GoldExecutor(broker, GoldExecutorConfig())
    results = ex.execute("**Rating**: Buy", trader_plan="")
    assert len(results) == 1
    assert results[0].action == "BUY"


def test_hold_leaves_position_open_by_default():
    pos = SimpleNamespace(type=0, volume=0.1, ticket=1)
    broker = StubBroker(positions=[pos])
    ex = GoldExecutor(broker, GoldExecutorConfig())
    results = ex.execute("**Rating**: Hold")
    assert results == []
    assert broker.closed == []


def test_hold_with_close_on_hold_flattens():
    pos = SimpleNamespace(type=0, volume=0.1, ticket=1)
    broker = StubBroker(positions=[pos])
    ex = GoldExecutor(broker, GoldExecutorConfig(close_on_hold=True))
    results = ex.execute("**Rating**: Hold")
    assert broker.closed == ["CLOSE_BUY"]
    assert all(r.action.startswith("CLOSE") for r in results)


def test_sell_flattens_long_when_short_disallowed():
    pos = SimpleNamespace(type=0, volume=0.1, ticket=1)
    broker = StubBroker(positions=[pos])
    ex = GoldExecutor(broker, GoldExecutorConfig(allow_short=False))
    results = ex.execute("**Rating**: Sell")
    # Should close the long but not open a short.
    assert broker.closed == ["CLOSE_BUY"]
    assert all(r.action.startswith("CLOSE") for r in results)


def test_sell_opens_short_when_allowed():
    broker = StubBroker()
    ex = GoldExecutor(broker, GoldExecutorConfig(allow_short=True))
    results = ex.execute("**Rating**: Sell")
    assert any(r.action == "SELL" for r in results)


def test_buy_when_already_long_is_noop():
    pos = SimpleNamespace(type=0, volume=0.1, ticket=1)
    broker = StubBroker(positions=[pos])
    ex = GoldExecutor(broker, GoldExecutorConfig())
    results = ex.execute("**Rating**: Buy")
    # The existing long is kept; no new BUY is added.
    assert not any(r.action == "BUY" for r in results)


def test_trader_stop_loss_flows_into_order_sl():
    """A realistic PM decision + Trader plan end-to-end through the executor."""
    broker = StubBroker()  # bid=2400.0, ask=2400.5
    ex = GoldExecutor(broker, GoldExecutorConfig())
    decision = "**Rating**: Buy\n\n**Executive Summary**: ... momentum ..."
    trader_plan = (
        "**Action**: Buy\n\n**Entry Price**: 2401.0\n\n**Stop Loss**: 2380.0\n\n"
        "FINAL TRANSACTION PROPOSAL: **BUY**"
    )
    results = ex.execute(decision, trader_plan=trader_plan)
    assert len(results) == 1
    order = results[0]
    assert order.action == "BUY"
    assert order.price == 2400.5          # market ask fill
    assert order.sl == 2380.0             # trader's stop honored
    assert order.tp is not None and order.tp > order.price  # TP on the far side
    assert order.volume == 0.05           # from StubBroker.calculate_volume


def test_bearish_stop_only_used_when_on_correct_side():
    """A trader stop ABOVE entry must be ignored for a BUY (wrong side)."""
    broker = StubBroker()
    ex = GoldExecutor(broker, GoldExecutorConfig())
    trader_plan = "**Action**: Buy\n\n**Stop Loss**: 2450.0"  # above ask -> invalid for long
    results = ex.execute("**Rating**: Buy", trader_plan=trader_plan)
    order = results[0]
    assert order.sl < order.price  # fell back to a distance-based stop below entry


# ---------------------------------------------------------------------------
# MT5Broker sizing / dry-run logic (no real terminal needed)
# ---------------------------------------------------------------------------


def _fake_symbol_info(**overrides):
    base = {
        "volume_min": 0.01, "volume_max": 100.0, "volume_step": 0.01,
        "trade_contract_size": 100.0, "digits": 2, "filling_mode": 2,
        "trade_stops_level": 0, "visible": True,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _broker_with_stubs(dry_run=True, equity=10_000.0):
    broker = MT5Broker(MT5Config(dry_run=dry_run, risk_percent=1.0))
    broker._mt5 = MagicMock()
    broker._initialized = True
    broker._resolved_symbol = "XAUUSD"
    broker.symbol_info = MagicMock(return_value=_fake_symbol_info())
    broker.tick = MagicMock(return_value=SimpleNamespace(bid=2400.0, ask=2400.5))
    broker.account_info = MagicMock(
        return_value=SimpleNamespace(equity=equity, balance=equity)
    )
    return broker


def test_calculate_volume_risk_based():
    broker = _broker_with_stubs(equity=10_000.0)
    # 1% risk = $100. SL distance $10, contract 100 -> $1000/lot -> 0.1 lot.
    vol = broker.calculate_volume(stop_distance=10.0)
    assert vol == pytest.approx(0.1, abs=1e-6)


def test_calculate_volume_snaps_to_step_and_min():
    broker = _broker_with_stubs(equity=1_000.0)  # 1% = $10 -> 0.01 lot floor
    vol = broker.calculate_volume(stop_distance=10.0)
    assert vol == 0.01


def test_dry_run_order_does_not_send():
    broker = _broker_with_stubs(dry_run=True)
    res = broker.market_order("BUY", volume=0.1, sl=2390.0, tp=2420.0)
    assert res.dry_run is True
    assert res.action == "BUY"
    broker._mt5.orders_send.assert_not_called()


def test_live_order_calls_orders_send():
    broker = _broker_with_stubs(dry_run=False)
    trade_result = SimpleNamespace(retcode=10009, comment="ok", _asdict=lambda: {})
    broker._mt5.orders_send.return_value = trade_result
    res = broker.market_order("BUY", volume=0.1, sl=2390.0, tp=2420.0)
    broker._mt5.orders_send.assert_called_once()
    assert res.retcode == 10009
    assert res.ok is True


def test_rejected_order_not_ok():
    broker = _broker_with_stubs(dry_run=False)
    trade_result = SimpleNamespace(retcode=10019, comment="no money", _asdict=lambda: {})
    broker._mt5.orders_send.return_value = trade_result
    res = broker.market_order("BUY", volume=5.0)
    assert res.ok is False


def test_order_result_ok_semantics():
    assert OrderResult(dry_run=True, action="BUY", symbol="X").ok is True
    assert OrderResult(dry_run=True, action="NONE", symbol="X").ok is False
    assert OrderResult(dry_run=False, action="BUY", symbol="X", retcode=10009).ok is True
    assert OrderResult(dry_run=False, action="BUY", symbol="X", retcode=10019).ok is False


def test_from_env_reads_credentials(monkeypatch):
    monkeypatch.setenv("MT5_LOGIN", "12345")
    monkeypatch.setenv("MT5_PASSWORD", "secret")
    monkeypatch.setenv("MT5_SERVER", "Broker-Live")
    monkeypatch.setenv("MT5_SYMBOL", "GOLD")
    monkeypatch.setenv("MT5_RISK_PERCENT", "2.5")
    monkeypatch.setenv("MT5_DRY_RUN", "false")
    cfg = MT5Config.from_env()
    assert cfg.login == 12345
    assert cfg.password == "secret"
    assert cfg.server == "Broker-Live"
    assert cfg.symbol == "GOLD"
    assert cfg.risk_percent == 2.5
    assert cfg.dry_run is False


# ---------------------------------------------------------------------------
# Commodity fundamentals analyst branch
# ---------------------------------------------------------------------------


def _fake_llm(report_text="report"):
    """Minimal LLM stand-in: bind_tools captures the tool list, and the piped
    chain returns a fixed AIMessage (plain functions coerce to RunnableLambda).
    """
    from langchain_core.messages import AIMessage

    captured = {}

    def fake_chain(_input):
        return AIMessage(content=report_text)

    class FakeLLM:
        def bind_tools(self, tools):
            captured["tools"] = [t.name for t in tools]
            return fake_chain

    return FakeLLM(), captured


def _base_state(asset_type):
    return {
        "trade_date": "2026-09-08",
        "asset_type": asset_type,
        "company_of_interest": "XAUUSD",
        "instrument_context": "",
        "messages": [("human", "XAUUSD")],
    }


def test_commodity_fundamentals_uses_macro_tools():
    from tradingagents.agents.analysts.fundamentals_analyst import (
        create_fundamentals_analyst,
    )

    llm, captured = _fake_llm("gold macro report")
    node = create_fundamentals_analyst(llm)
    out = node(_base_state("commodity"))

    assert set(captured["tools"]) == {"get_macro_indicators", "get_global_news"}
    assert out["fundamentals_report"] == "gold macro report"


def test_stock_fundamentals_keeps_company_tools():
    from tradingagents.agents.analysts.fundamentals_analyst import (
        create_fundamentals_analyst,
    )

    llm, captured = _fake_llm("company report")
    node = create_fundamentals_analyst(llm)
    out = node(_base_state("stock"))

    assert set(captured["tools"]) == {
        "get_fundamentals", "get_balance_sheet", "get_cashflow", "get_income_statement",
    }
    assert out["fundamentals_report"] == "company report"


def test_commodity_instrument_context_mentions_no_company_financials():
    from tradingagents.agents.utils.agent_utils import build_instrument_context

    ctx = build_instrument_context("GC=F", "commodity")
    assert "commodity" in ctx
    assert "company financials" in ctx


# ---------------------------------------------------------------------------
# Graph wiring for commodity mode
# ---------------------------------------------------------------------------


def test_fundamentals_toolnode_can_execute_macro_tools():
    """Commodity fundamentals calls get_macro_indicators / get_global_news;
    those must be executable from the fundamentals ToolNode."""
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    nodes = TradingAgentsGraph._create_tool_nodes(None)
    fundamentals_tools = set(nodes["fundamentals"].tools_by_name)
    assert {"get_macro_indicators", "get_global_news"} <= fundamentals_tools
    # company tools must still be present for the stock pipeline
    assert {"get_fundamentals", "get_balance_sheet",
            "get_cashflow", "get_income_statement"} <= fundamentals_tools


def test_graph_builds_for_commodity_analysts(tmp_path):
    """Full graph construction with the gold analyst set (LLMs mocked)."""
    import copy
    from unittest.mock import patch

    from tradingagents.default_config import DEFAULT_CONFIG
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    config = copy.deepcopy(DEFAULT_CONFIG)
    config["results_dir"] = str(tmp_path / "results")
    config["data_cache_dir"] = str(tmp_path / "cache")
    config["memory_log_path"] = str(tmp_path / "cache" / "memory.md")
    config = build_gold_config(config)
    client = MagicMock()
    client.get_llm.return_value = MagicMock()
    with patch(
        "tradingagents.graph.trading_graph.create_llm_client",
        return_value=client,
    ):
        ta = TradingAgentsGraph(
            selected_analysts=("market", "news", "fundamentals"),
            debug=False,
            config=config,
        )
    node_names = set(ta.graph.get_graph().nodes.keys())
    assert "Fundamentals Analyst" in node_names
    assert "Market Analyst" in node_names
    assert "News Analyst" in node_names
    assert "Portfolio Manager" in node_names
    # Sentinel (sentiment) analyst is intentionally NOT part of the gold setup.
    assert "Sentiment Analyst" not in node_names


# ---------------------------------------------------------------------------
# gold_mt5.py runner
# ---------------------------------------------------------------------------


def test_gold_runner_arg_parsing():
    import gold_mt5

    args = gold_mt5.parse_args([
        "--symbol", "XAUUSD", "--date", "2026-09-08", "--analysis-only",
    ])
    assert args.symbol == "XAUUSD"
    assert args.date == "2026-09-08"
    assert args.analysis_only is True
    assert args.live is False

    args = gold_mt5.parse_args(["--live", "--lots", "0.01", "--allow-short"])
    assert args.live is True
    assert args.lots == 0.01
    assert args.allow_short is True
