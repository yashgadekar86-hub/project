"""
Position size calculator.

Correctly computes lot size from equity, risk %, entry, SL, and symbol spec
(contract size, point/pip size, tick value, lot min/max/step).

We do NOT hard-code pip values. Symbol specifications come from MT5 (or a
broker config object) so all pairs (including JPY, cross pairs, exotic FX,
metals, indices) are handled properly.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class SymbolSpec:
    name: str
    digits: int
    point: float
    contract_size: float  # e.g. 100000 for FX
    lot_min: float
    lot_max: float
    lot_step: float
    tick_size: float
    tick_value: float  # profit per tick per 1 lot (in profit currency, usually quoted ccy or USD)
    currency_base: str
    currency_profit: str
    currency_margin: str
    # The following fields are used by the trading engine but have sensible defaults.
    spread: float = 0.0
    spread_float: bool = True
    stops_level: int = 0
    freeze_level: int = 0
    trade_mode: int = 0
    trade_exemode: int = 0
    trade_calc_mode: int = 0
    swap_mode: int = 0
    swap_long: float = 0.0
    swap_short: float = 0.0
    visible: bool = True

    @property
    def pip_size(self) -> float:
        """Standard pip size: 10 points for 5/3-digit pairs, 1 point otherwise."""
        return self.point * (10 if self.digits in (3, 5) else 1)


@dataclass
class PositionSizeResult:
    risk_amount: float
    calculated_lot: float
    rounded_lot: float
    pip_value_per_lot: float
    sl_distance_pips: float
    estimated_loss_at_sl: float
    estimated_profit_at_tp: float
    risk_reward: Optional[float]
    warnings: list
    allowed: bool
    reason: Optional[str]


def _round_lot_to_step(lot: float, step: float, lot_min: float, lot_max: float) -> float:
    """Round lot to nearest broker lot step and clamp to [lot_min, lot_max]."""
    if step <= 0:
        step = 0.01
    rounded = round(lot / step) * step
    rounded = max(lot_min, min(lot_max, rounded))
    return round(rounded, 8)


def _pip_value_from_tick(spec: SymbolSpec, sl_distance_price: float) -> float:
    """
    Compute profit-per-1-lot-per-1-pip using the broker's tick_value/tick_size,
    which is the most reliable source (brokers sometimes report tick_value in
    account currency directly).
    """
    # tick_value = monetary value of 1 tick of size tick_size, for 1 lot
    # pip_size = price distance of one pip (we pass pip_size as the SL unit)
    pip_size = spec.point * (10 if spec.digits in (3, 5) else 1)
    if spec.tick_size and spec.tick_value:
        # How many ticks in a pip?
        ticks_per_pip = pip_size / spec.tick_size if spec.tick_size > 0 else 0
        pip_val = spec.tick_value * ticks_per_pip
        return pip_val
    # Fallback: generic FX approximation — only used if broker does not provide tick_value
    return spec.contract_size * pip_size


def calculate_position_size(
    *,
    equity: float,
    risk_pct: float,
    entry: float,
    stop_loss: float,
    spec: SymbolSpec,
    take_profit: Optional[float] = None,
    commission_per_lot: float = 0.0,
    swap_per_lot_est: float = 0.0,
    max_lot_override: Optional[float] = None,
) -> PositionSizeResult:
    """
    Calculate correct lot size given risk parameters.

    Returns a `PositionSizeResult` with `allowed` True/False and the reason
    if disallowed. The returned `rounded_lot` is guaranteed to respect
    [lot_min, lot_max] and `lot_step`, and will NOT exceed the requested risk
    (it is rounded DOWN to nearest lot step, not to nearest).
    """
    warnings: list = []

    if equity <= 0:
        return PositionSizeResult(0, 0, 0, 0, 0, 0, 0, None, ["Invalid equity"], False, "Equity must be positive")
    if not 0 < risk_pct <= 100:
        return PositionSizeResult(0, 0, 0, 0, 0, 0, 0, None, ["Invalid risk %"], False, "risk_pct must be in (0, 100]")
    if entry <= 0 or stop_loss <= 0:
        return PositionSizeResult(0, 0, 0, 0, 0, 0, 0, None, ["Invalid entry/SL"], False, "Entry and SL must be positive")
    if abs(entry - stop_loss) < spec.point:
        return PositionSizeResult(0, 0, 0, 0, 0, 0, 0, None, ["SL too close"], False, "Stop loss is too close to entry")

    risk_amount = equity * (risk_pct / 100.0)
    sl_distance_price = abs(entry - stop_loss)
    pip_size = spec.point * (10 if spec.digits in (3, 5) else 1)
    sl_distance_pips = sl_distance_price / pip_size if pip_size > 0 else 0

    pip_value_per_lot = _pip_value_from_tick(spec, sl_distance_price)
    if pip_value_per_lot <= 0:
        return PositionSizeResult(
            risk_amount, 0, 0, 0, sl_distance_pips, 0, 0, None,
            ["Cannot compute pip value from symbol spec"], False,
            "Unable to determine pip value; check symbol contract specification",
        )

    # Risk per lot (monetary loss per 1 lot when SL is hit)
    risk_per_lot = pip_value_per_lot * sl_distance_pips + commission_per_lot + swap_per_lot_est
    if risk_per_lot <= 0:
        return PositionSizeResult(
            risk_amount, 0, 0, pip_value_per_lot, sl_distance_pips, 0, 0, None,
            ["Risk per lot computed as zero/negative"], False, "Invalid risk-per-lot (check SL / symbol spec)",
        )

    raw_lot = risk_amount / risk_per_lot

    # Clamp / round. To NEVER exceed risk, we round DOWN to lot_step.
    step = spec.lot_step if spec.lot_step > 0 else 0.01
    floored_lot = int(raw_lot / step) * step
    lot_max = min(spec.lot_max, max_lot_override) if max_lot_override else spec.lot_max
    rounded_lot = max(spec.lot_min, min(lot_max, floored_lot))
    rounded_lot = round(rounded_lot, 8)

    estimated_loss = rounded_lot * risk_per_lot

    estimated_profit = None
    rr = None
    if take_profit is not None and take_profit > 0:
        tp_distance_price = abs(take_profit - entry)
        tp_distance_pips = tp_distance_price / pip_size if pip_size > 0 else 0
        profit_per_lot = pip_value_per_lot * tp_distance_pips - commission_per_lot
        estimated_profit = rounded_lot * profit_per_lot
        if estimated_loss > 0:
            rr = round(estimated_profit / estimated_loss, 2)
        if sl_distance_price > 0 and tp_distance_price > 0:
            rr_price = tp_distance_price / sl_distance_price
            rr = rr if rr is not None else round(rr_price, 2)

    if rounded_lot < spec.lot_min:
        return PositionSizeResult(
            risk_amount, raw_lot, 0, pip_value_per_lot, sl_distance_pips,
            estimated_loss, estimated_profit or 0, rr,
            ["Risk too small for min lot; min lot would exceed risk"],
            False, "Calculated lot is below broker minimum; trade not allowed",
        )
    if rounded_lot > lot_max:
        return PositionSizeResult(
            risk_amount, raw_lot, rounded_lot, pip_value_per_lot, sl_distance_pips,
            estimated_loss, estimated_profit or 0, rr,
            ["Calculated lot exceeds maximum lot"],
            False, "Calculated lot exceeds maximum allowed lot size",
        )
    if estimated_loss > risk_amount + 1e-9:
        # Safety: if rounding somehow pushed us over, disallow.
        return PositionSizeResult(
            risk_amount, raw_lot, rounded_lot, pip_value_per_lot, sl_distance_pips,
            estimated_loss, estimated_profit or 0, rr,
            ["Rounded lot exceeds risk budget"],
            False, "Position exceeds risk budget after lot rounding",
        )

    if rr is not None and rr < 1:
        warnings.append("Risk/reward below 1:1")

    return PositionSizeResult(
        risk_amount=round(risk_amount, 2),
        calculated_lot=round(raw_lot, 4),
        rounded_lot=rounded_lot,
        pip_value_per_lot=round(pip_value_per_lot, 4),
        sl_distance_pips=round(sl_distance_pips, 1),
        estimated_loss_at_sl=round(estimated_loss, 2),
        estimated_profit_at_tp=round(estimated_profit, 2) if estimated_profit is not None else 0,
        risk_reward=rr,
        warnings=warnings,
        allowed=True,
        reason=None,
    )
