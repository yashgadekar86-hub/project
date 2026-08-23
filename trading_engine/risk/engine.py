"""
Risk Engine — non-bypassable gating before any trade.

Performs the pre-trade checks listed in spec section 14 plus additional
safety checks (kill switch, daily loss, drawdown, consecutive losses, exposure,
news, session, spread, margin, SL/TP validity, duplicate, etc.).

The Risk Engine is independent of the AI/strategy layer. Even if AI says BUY
with 99% confidence, the Risk Engine can (and must) reject the trade if any
hard rule is violated.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from trading_engine.risk.position_sizer import (
    PositionSizeResult, SymbolSpec, calculate_position_size,
)


@dataclass
class RiskSettings:
    """Per-account risk configuration (populated from UserSettings)."""
    risk_per_trade_pct: float = 0.5
    max_daily_loss_pct: float = 2.0
    max_drawdown_pct: float = 5.0
    max_simultaneous_trades: int = 3
    max_lot_size: float = 10.0
    min_risk_reward: float = 2.0
    max_spread_pips_majors: float = 3.0
    max_slippage_pips: float = 2.0
    daily_trade_limit: int = 20
    max_consecutive_losses: int = 3
    cooldown_after_losses_minutes: int = 60
    min_ai_confidence: int = 70
    news_filter_enabled: bool = False
    session_filter_enabled: bool = False
    allowed_sessions: List[str] = field(default_factory=lambda: ["London", "NewYork"])
    one_trade_per_symbol: bool = True
    max_exposure_per_currency_pct: float = 20.0
    stop_loss_required: bool = True
    take_profit_required: bool = True
    allow_short: bool = True
    allow_long: bool = True


@dataclass
class MarketConditions:
    """Current market state for the symbol."""
    symbol: str
    bid: float
    ask: float
    spread_pips: float
    market_open: bool
    data_fresh: bool
    data_last_update: Optional[datetime] = None
    session: str = "Unknown"
    high_news_risk: bool = False
    volatility_pips: float = 0.0


@dataclass
class AccountState:
    """Current account snapshot."""
    balance: float
    equity: float
    margin_free: float
    margin_level: Optional[float]
    currency: str
    open_trades_count: int = 0
    open_positions: List[Dict[str, Any]] = field(default_factory=list)
    daily_pnl: float = 0.0
    weekly_pnl: float = 0.0
    drawdown_pct_from_peak: float = 0.0
    consecutive_losses: int = 0
    last_loss_time: Optional[datetime] = None
    trades_today: int = 0
    peak_equity: float = 0.0
    kill_switch_active: bool = False
    mt5_connected: bool = True
    trading_enabled_by_user: bool = True


@dataclass
class ProposedTrade:
    symbol: str
    direction: str  # BUY / SELL
    entry: float
    stop_loss: Optional[float]
    take_profit: Optional[float]
    symbol_spec: SymbolSpec
    ai_confidence: Optional[int] = None
    risk_reward: Optional[float] = None
    strategy_name: Optional[str] = None
    signal_reasoning: List[str] = field(default_factory=list)


@dataclass
class RiskCheckResult:
    allowed: bool
    risk_amount: float = 0.0
    lot: float = 0.0
    estimated_loss_at_sl: float = 0.0
    estimated_profit_at_tp: float = 0.0
    risk_reward: Optional[float] = None
    pip_value_per_lot: float = 0.0
    sl_distance_pips: float = 0.0
    passed_checks: Dict[str, bool] = field(default_factory=dict)
    failures: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    position_size: Optional[PositionSizeResult] = None


def _is_in_allowed_session(session: str, allowed: List[str]) -> bool:
    return (session in allowed) or (not allowed)


def evaluate_trade(
    proposed: ProposedTrade,
    settings: RiskSettings,
    account: AccountState,
    market: MarketConditions,
) -> RiskCheckResult:
    """Run all pre-trade checks and return result. Never raises."""
    checks: Dict[str, bool] = {}
    failures: List[str] = []
    warnings: List[str] = []

    def check(name: str, ok: bool, msg_on_fail: str, *, warning_only: bool = False) -> bool:
        checks[name] = ok
        if not ok:
            if warning_only:
                warnings.append(msg_on_fail)
            else:
                failures.append(msg_on_fail)
        return ok

    # 1) Kill switch
    check("kill_switch", not account.kill_switch_active, "Kill switch is active — all trading halted")

    # 2) MT5 connected
    check("mt5_connected", account.mt5_connected, "MT5 is not connected — trading halted")

    # 3) User enabled
    check("trading_enabled", account.trading_enabled_by_user, "Trading is disabled by user")

    # 4) Market open
    check("market_open", market.market_open, f"Market closed for {market.symbol}")

    # 5) Data fresh
    check("data_fresh", market.data_fresh, "Market data is stale — trading halted")

    # 6) Spread
    spread_ok = market.spread_pips <= settings.max_spread_pips_majors
    check("spread", spread_ok,
          f"Spread {market.spread_pips:.1f} pips exceeds max {settings.max_spread_pips_majors:.1f}")

    # 7) News risk
    if settings.news_filter_enabled:
        check("news_risk", not market.high_news_risk,
              "High-impact news event nearby — trading blocked")
    elif market.high_news_risk:
        warnings.append("High-impact news nearby (news filter is disabled)")

    # 8) Session
    if settings.session_filter_enabled:
        check("session", _is_in_allowed_session(market.session, settings.allowed_sessions),
              f"Current session '{market.session}' not in allowed sessions")

    # 9) Max daily loss
    if settings.max_daily_loss_pct > 0 and account.balance > 0:
        daily_loss_pct = (-account.daily_pnl / account.balance * 100) if account.daily_pnl < 0 else 0
        check("daily_loss", daily_loss_pct < settings.max_daily_loss_pct,
              f"Max daily loss ({settings.max_daily_loss_pct}%) exceeded — trading disabled for the day")

    # 10) Max drawdown
    if settings.max_drawdown_pct > 0:
        check("drawdown", account.drawdown_pct_from_peak < settings.max_drawdown_pct,
              f"Max drawdown ({settings.max_drawdown_pct}%) exceeded — trading halted")

    # 11) Max simultaneous trades
    check("max_trades", account.open_trades_count < settings.max_simultaneous_trades,
          f"Max simultaneous trades reached ({settings.max_simultaneous_trades})")

    # 12) Daily trade limit
    check("trade_limit", account.trades_today < settings.daily_trade_limit,
          f"Daily trade limit reached ({settings.daily_trade_limit})")

    # 13) Consecutive losses cooldown
    if (account.consecutive_losses >= settings.max_consecutive_losses
            and account.last_loss_time is not None):
        cooldown_end = account.last_loss_time.timestamp() + settings.cooldown_after_losses_minutes * 60
        in_cooldown = datetime.now(timezone.utc).timestamp() < cooldown_end
        check("consecutive_loss_cooldown", not in_cooldown,
              f"Cooldown active after {account.consecutive_losses} consecutive losses")

    # 14) Direction allowed
    if proposed.direction == "BUY":
        check("long_allowed", settings.allow_long, "Long trades are disabled")
    else:
        check("short_allowed", settings.allow_short, "Short trades are disabled")

    # 15) SL / TP required
    if settings.stop_loss_required:
        check("sl_present", proposed.stop_loss is not None and proposed.stop_loss > 0,
              "Stop loss is required")
    if settings.take_profit_required:
        check("tp_present", proposed.take_profit is not None and proposed.take_profit > 0,
              "Take profit is required")

    # 16) SL/TP placement validity
    if proposed.stop_loss and proposed.take_profit and proposed.entry > 0:
        if proposed.direction == "BUY":
            check("sl_below_entry", proposed.stop_loss < proposed.entry, "For BUY, SL must be below entry")
            check("tp_above_entry", proposed.take_profit > proposed.entry, "For BUY, TP must be above entry")
        else:
            check("sl_above_entry", proposed.stop_loss > proposed.entry, "For SELL, SL must be above entry")
            check("tp_below_entry", proposed.take_profit < proposed.entry, "For SELL, TP must be below entry")

    # 17) One trade per symbol (duplicate prevention)
    if settings.one_trade_per_symbol:
        same_symbol = [p for p in account.open_positions if p.get("symbol") == proposed.symbol]
        check("duplicate", len(same_symbol) == 0, f"Already have an open position on {proposed.symbol}")

    # 18) AI confidence
    if proposed.ai_confidence is not None:
        check("ai_confidence", proposed.ai_confidence >= settings.min_ai_confidence,
              f"AI confidence {proposed.ai_confidence}% below minimum {settings.min_ai_confidence}%")

    # 19) Exposure warning (base currency concentration)
    if settings.max_exposure_per_currency_pct < 100:
        base = proposed.symbol[:3] if len(proposed.symbol) >= 6 else proposed.symbol
        same_ccy = [p for p in account.open_positions if str(p.get("symbol", ""))[:3] == base]
        if len(same_ccy) >= 3:
            warnings.append(f"Multiple open positions share base currency {base}")

    # 20) Margin sanity
    if account.margin_free <= 0:
        check("margin_free", False, "No free margin available")

    hard_failures = [f for f in failures if f not in warnings]
    if hard_failures:
        return RiskCheckResult(
            allowed=False,
            passed_checks=checks,
            failures=failures,
            warnings=warnings,
        )

    # 21) Position sizing (NEVER exceeds risk)
    sl = proposed.stop_loss if proposed.stop_loss else 0
    tp = proposed.take_profit
    psr = calculate_position_size(
        equity=account.equity,
        risk_pct=settings.risk_per_trade_pct,
        entry=proposed.entry,
        stop_loss=sl,
        take_profit=tp,
        spec=proposed.symbol_spec,
        max_lot_override=settings.max_lot_size,
    )
    if not psr.allowed:
        failures.append(psr.reason or "Position size calculation rejected the trade")
        return RiskCheckResult(
            allowed=False,
            position_size=psr,
            passed_checks=checks,
            failures=failures,
            warnings=warnings + psr.warnings,
        )

    # 22) R:R
    rr = psr.risk_reward if psr.risk_reward is not None else proposed.risk_reward
    if rr is not None and settings.min_risk_reward > 0:
        check("risk_reward", rr >= settings.min_risk_reward,
              f"Risk/Reward {rr} below minimum {settings.min_risk_reward}")

    # 23) Margin sufficiency vs estimated loss
    if psr.estimated_loss_at_sl > account.margin_free * 0.95:
        check("margin_sufficient", False,
              "Estimated loss exceeds 95% of free margin")

    # 24) Abnormal volatility (if market.volatility_pips available)
    if market.volatility_pips > 0 and market.spread_pips > market.volatility_pips * 0.5:
        warnings.append(
            f"Spread ({market.spread_pips:.1f} pips) is >50% of recent ATR — illiquid conditions"
        )

    final_failures = [f for f in failures if f not in warnings]
    allowed = len(final_failures) == 0

    return RiskCheckResult(
        allowed=allowed,
        risk_amount=psr.risk_amount,
        lot=psr.rounded_lot,
        estimated_loss_at_sl=psr.estimated_loss_at_sl,
        estimated_profit_at_tp=psr.estimated_profit_at_tp,
        risk_reward=rr,
        pip_value_per_lot=psr.pip_value_per_lot,
        sl_distance_pips=psr.sl_distance_pips,
        passed_checks=checks,
        failures=failures,
        warnings=warnings + psr.warnings,
        position_size=psr,
    )
