"""Position sizing from ACTUAL broker symbol economics (Phase 14).

Never assumes 1 lot = 100 oz. Money-at-risk per lot is derived from the
symbol's real ``trade_tick_size`` / ``trade_tick_value`` (the MT5-correct
way) with a ``contract_size`` fallback when tick value is unreported.

Rounding is deliberately FLOOR to the broker's ``volume_step`` so realized
risk can only be <= the configured risk, never above it. When even the
broker's ``volume_min`` would exceed the allowed risk, the result is
NO TRADE — never an oversized position.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import BrokerSymbolSpec, TradePlan


@dataclass
class SizingResult:
    volume: float | None
    risk_amount: float
    loss_per_lot: float
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.volume is not None and self.volume > 0


def loss_per_lot(stop_distance: float, spec: BrokerSymbolSpec) -> float:
    """Money lost per 1.0 lot if price moves ``stop_distance`` against us.

    Preferred: ticks * tick_value (tick_value is per lot per tick, in the
    account currency when quote == account). Fallback: distance * contract
    size (correct when the quote currency equals the account currency).
    """
    if stop_distance <= 0:
        raise ValueError("stop_distance must be positive")
    if spec.tick_size and spec.tick_size > 0 and spec.tick_value and spec.tick_value > 0:
        ticks = stop_distance / spec.tick_size
        return ticks * spec.tick_value
    return stop_distance * (spec.contract_size or 1.0)


def calculate_volume(
    plan: TradePlan,
    spec: BrokerSymbolSpec,
    equity: float,
    risk_percent: float,
) -> SizingResult:
    """Risk-based sizing with broker volume rules. NEVER exceeds allowed risk."""
    if not plan.valid or plan.risk_distance is None or plan.risk_distance <= 0:
        return SizingResult(None, 0.0, 0.0, "plan has no valid stop distance")

    risk_amount = equity * (risk_percent / 100.0)
    per_lot = loss_per_lot(plan.risk_distance, spec)
    if per_lot <= 0:
        return SizingResult(None, risk_amount, 0.0,
                            "symbol economics produced non-positive loss per lot")

    raw_volume = risk_amount / per_lot

    vstep = spec.volume_step if spec.volume_step > 0 else 0.01
    vmin = spec.volume_min if spec.volume_min > 0 else vstep
    vmax = spec.volume_max if spec.volume_max > 0 else raw_volume

    # FLOOR to the step: realized risk <= configured risk, always.
    steps = int(raw_volume / vstep)  # floor
    volume = round(steps * vstep, 8)

    if volume < vmin:
        # Even the minimum lot would risk more than allowed -> NO TRADE.
        min_risk = per_lot * vmin
        return SizingResult(
            None, risk_amount, per_lot,
            f"minimum broker volume {vmin} risks {min_risk:.2f} > allowed "
            f"{risk_amount:.2f} ({risk_percent}% of {equity:.2f})",
        )
    if volume > vmax:
        volume = vmax

    # Realized risk after rounding (for the audit trail).
    plan.volume = volume
    plan.risk_amount = round(per_lot * volume, 2)
    return SizingResult(volume, risk_amount, per_lot)
