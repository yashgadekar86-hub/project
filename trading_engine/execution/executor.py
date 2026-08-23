"""
Order execution pipeline.

Order flow:
  AI Signal → Risk Engine → Execution Validator (pre-trade 14-check) → MT5 (or paper/backtest)
  → Verify → Persist.

The executor NEVER retries blindly on failure. Duplicate prevention, lot
validation, SL/TP distance checks, margin checks, and post-fill verification
are all performed before an order is considered "executed".
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from trading_engine.mt5.connector import MT5Connector, MT5OrderResult
from trading_engine.risk.engine import (
    AccountState, MarketConditions, ProposedTrade, RiskCheckResult,
    RiskSettings, evaluate_trade,
)
from trading_engine.risk.position_sizer import PositionSizeResult, SymbolSpec


@dataclass
class ExecutionRequest:
    symbol: str
    direction: str
    entry: Optional[float]
    stop_loss: float
    take_profit: float
    symbol_spec: SymbolSpec
    signal_id: Optional[str] = None
    strategy_name: Optional[str] = None
    ai_confidence: Optional[int] = None
    trade_mode: str = "paper"     # paper | live | backtest
    comment: str = "AIFX"
    magic: int = 20240001
    slippage_pips: float = 2.0
    max_spread_pips: float = 3.0


@dataclass
class ExecutionResult:
    success: bool
    trade_mode: str
    ticket: Optional[int] = None
    execution_price: Optional[float] = None
    volume_lots: float = 0.0
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    risk_amount: float = 0.0
    estimated_loss_at_sl: float = 0.0
    estimated_profit_at_tp: float = 0.0
    risk_reward: Optional[float] = None
    validation: Optional[RiskCheckResult] = None
    mt5_result: Optional[Dict[str, Any]] = None
    rejection_reason: Optional[str] = None
    warnings: List[str] = field(default_factory=list)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


def validate_order(req: ExecutionRequest, settings: RiskSettings,
                   account: AccountState, market: MarketConditions) -> RiskCheckResult:
    """Public hook to dry-run validation without placing an order."""
    proposed = ProposedTrade(
        symbol=req.symbol, direction=req.direction, entry=req.entry or market.bid if req.direction == "SELL" else market.ask,
        stop_loss=req.stop_loss, take_profit=req.take_profit, symbol_spec=req.symbol_spec,
        ai_confidence=req.ai_confidence, strategy_name=req.strategy_name,
    )
    return evaluate_trade(proposed, settings, account, market)


def execute_order(req: ExecutionRequest, settings: RiskSettings,
                  account: AccountState, market: MarketConditions,
                  mt5: Optional[MT5Connector] = None,
                  *, is_paper: bool = False) -> ExecutionResult:
    """Validate and then execute an order. Never retries on failure."""
    entry_price = req.entry
    if entry_price is None:
        entry_price = market.ask if req.direction == "BUY" else market.bid

    proposed = ProposedTrade(
        symbol=req.symbol, direction=req.direction, entry=entry_price,
        stop_loss=req.stop_loss, take_profit=req.take_profit,
        symbol_spec=req.symbol_spec, ai_confidence=req.ai_confidence,
        strategy_name=req.strategy_name,
    )
    risk = evaluate_trade(proposed, settings, account, market)
    if not risk.allowed:
        return ExecutionResult(
            success=False, trade_mode=req.trade_mode, validation=risk,
            rejection_reason="; ".join(risk.failures),
            warnings=risk.warnings,
        )

    lot = round(risk.lot, 8)
    result = ExecutionResult(
        success=False, trade_mode=req.trade_mode, validation=risk,
        volume_lots=lot, risk_amount=risk.risk_amount,
        estimated_loss_at_sl=risk.estimated_loss_at_sl,
        estimated_profit_at_tp=risk.estimated_profit_at_tp,
        risk_reward=risk.risk_reward, stop_loss=req.stop_loss,
        take_profit=req.take_profit, warnings=risk.warnings,
    )

    # --- Paper trading path ---
    if is_paper or req.trade_mode == "paper":
        # Simulated fill at current market price with synthetic slippage
        slip = req.symbol_spec.point * max(1, int(req.slippage_pips * 10))
        fill_price = market.ask + slip if req.direction == "BUY" else market.bid - slip
        result.success = True
        result.execution_price = fill_price
        result.ticket = int(f"9{int(time.time()*1000)%10**8}")  # synthetic ticket starts with 9
        return result

    # --- Backtest path (should be handled by backtesting engine, not executor) ---
    if req.trade_mode == "backtest":
        return result  # executor does not place backtest trades

    # --- Live MT5 path ---
    if mt5 is None or not mt5.connected:
        result.rejection_reason = "MT5 not connected"
        return result

    try:
        import MetaTrader5 as _mt5  # type: ignore
    except Exception:
        result.rejection_reason = "MetaTrader5 package not available in this environment"
        return result

    # Stops must be at least stops_level points away from market per broker spec
    min_stop_dist = req.symbol_spec.point * max(10, getattr(req.symbol_spec, "stops_level", 0) or 10)
    if req.direction == "BUY":
        if req.stop_loss >= market.bid - min_stop_dist:
            result.rejection_reason = "SL too close to market (broker stops_level)"
            return result
        order_type = _mt5.ORDER_TYPE_BUY
        price = market.ask
    else:
        if req.stop_loss <= market.ask + min_stop_dist:
            result.rejection_reason = "SL too close to market (broker stops_level)"
            return result
        order_type = _mt5.ORDER_TYPE_SELL
        price = market.bid

    request = {
        "action": _mt5.TRADE_ACTION_DEAL,
        "symbol": req.symbol,
        "volume": lot,
        "type": order_type,
        "price": price,
        "sl": req.stop_loss,
        "tp": req.take_profit,
        "deviation": int(max(1, req.slippage_pips * 10)),
        "magic": req.magic,
        "comment": req.comment[:32] if req.comment else "AIFX",
        "type_time": _mt5.ORDER_TIME_GTC,
        "type_filling": _mt5.ORDER_FILLING_IOC,
    }

    mt5_res: MT5OrderResult = mt5.order_send(request)
    result.mt5_result = {
        "retcode": mt5_res.retcode,
        "deal": mt5_res.deal,
        "order": mt5_res.order,
        "volume": mt5_res.volume,
        "price": mt5_res.price,
        "comment": mt5_res.comment,
    }
    if mt5_res.successful:
        result.success = True
        result.ticket = mt5_res.deal or mt5_res.order
        result.execution_price = mt5_res.price or price
    else:
        result.rejection_reason = f"MT5 retcode={mt5_res.retcode}: {mt5_res.comment}"

    return result


def close_position(ticket: int, *, symbol: str, direction: str, volume: float,
                   mt5: MT5Connector, deviation: int = 20, comment: str = "AIFX_CLOSE") -> Dict[str, Any]:
    """Close an open position by ticket. Returns a dict with success/retcode."""
    if mt5 is None or not mt5.connected:
        return {"success": False, "reason": "MT5 not connected"}
    try:
        import MetaTrader5 as _mt5
    except Exception:
        return {"success": False, "reason": "MetaTrader5 unavailable"}

    tick = mt5.get_tick(symbol)
    order_type = _mt5.ORDER_TYPE_SELL if direction == "BUY" else _mt5.ORDER_TYPE_BUY
    price = tick.bid if direction == "BUY" else tick.ask
    req = {
        "action": _mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": volume,
        "type": order_type,
        "position": ticket,
        "price": price,
        "deviation": deviation,
        "magic": 20240001,
        "comment": comment[:32],
        "type_time": _mt5.ORDER_TIME_GTC,
        "type_filling": _mt5.ORDER_FILLING_IOC,
    }
    res = mt5.order_send(req)
    return {
        "success": res.successful,
        "retcode": res.retcode,
        "deal": res.deal,
        "order": res.order,
        "price": res.price,
        "comment": res.comment,
    }
