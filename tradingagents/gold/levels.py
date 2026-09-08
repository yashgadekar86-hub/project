"""Deterministic SL/TP construction from market structure (Phases 11-13).

Rules (all deterministic; the LLM's proposed levels are *inputs to consider*,
never authority):

**Stop loss** for a BUY, in priority order:
  1. A trader/LLM stop ONLY if it is on the correct side AND respects the
     minimum/maximum distance sanity window (it is advice, not authority).
  2. Below the nearest structural support (clustered swing lows) with a
     small ATR buffer so the level is *behind* the noise.
  3. ATR fallback: ``entry - atr_sl_multiplier * ATR``.

**Take profit** for a BUY: nearest structural resistance ABOVE entry that
satisfies ``min_rr``; if no structure qualifies, an ATR-extension target
(``entry + n * ATR``) is allowed since it is an independent volatility
measure. A pure ``entry + min_rr * risk`` target is NEVER manufactured by
default (``allow_rr_fallback`` stays off) — if no honest target clears
``min_rr``, the plan is invalid and the engine must HOLD.

Every level is validated against broker constraints (stops level, tick size,
digits) before use.
"""

from __future__ import annotations

import math

from .models import (
    BrokerSymbolSpec,
    MarketContext,
    MultiTimeframeView,
    Side,
    TradePlan,
)

# Structural stops get this much ATR of extra room beyond the level.
STRUCTURE_SL_BUFFER_ATR = 0.25
# Sanity windows on the SL distance, in ATR multiples.
MAX_SL_ATR_MULTIPLE = 5.0
# ATR-extension TP candidates (multipliers of ATR, not of risk).
TP_ATR_CANDIDATES = (2.0, 2.5, 3.0, 4.0)


def _round_to_tick(price: float, tick: float, digits: int) -> float:
    if tick <= 0:
        return round(price, digits)
    steps = round(price / tick)
    return round(steps * tick, digits)


def _min_stop_distance(spec: BrokerSymbolSpec, market: MarketContext) -> float:
    """Broker minimum distance for SL/TP from the entry price."""
    dist = spec.stops_level_price()
    spread = market.spread_price or 0.0
    # Practically, a stop tighter than the current spread is untradeable.
    return max(dist, spread * 1.0)


def build_trade_plan(
    side: Side,
    mtf: MultiTimeframeView,
    market: MarketContext,
    spec: BrokerSymbolSpec,
    min_rr: float,
    atr_sl_multiplier: float,
    min_sl_atr_multiple: float,
    working_timeframe: str = "H1",
    trader_stop: float | None = None,
    trader_take_profits: list[float] | None = None,
    allow_rr_fallback: bool = False,
    tp_levels: int = 1,
    preferred_rr_max: float = 3.0,
) -> TradePlan:
    """Construct and validate a deterministic trade plan (or mark it invalid)."""
    plan = TradePlan(side=side)

    price = market.ask if side == "BUY" else market.bid  # market entry side
    if price is None:
        price = market.mid
    if price is None or price <= 0:
        plan.invalid_reason = "no usable market price"
        return plan
    plan.entry = _round_to_tick(float(price), spec.tick_size, spec.digits)

    # Working timeframe for structure, else the lowest usable snapshot.
    snap = mtf.per_tf.get(working_timeframe) or next(
        (s for s in reversed(list(mtf.per_tf.values())) if s.usable), None
    )
    if snap is None or snap.structure is None:
        plan.invalid_reason = "no usable structure snapshot"
        return plan
    structure = snap.structure
    atr_value = structure.atr or (snap.indicators.get("atr") if snap.indicators else None)
    if atr_value is None or atr_value <= 0:
        plan.invalid_reason = "ATR unavailable (DATA_UNAVAILABLE)"
        return plan
    plan.atr = float(atr_value)

    if side not in ("BUY", "SELL"):
        plan.invalid_reason = f"cannot build levels for side {side!r}"
        return plan

    min_dist = _min_stop_distance(spec, market)

    # ------------------------------------------------------------------ SL
    def structural_stop() -> float | None:
        if side == "BUY":
            # nearest support below entry
            lvl = structure.support[0] if structure.support else None
            if lvl is None and structure.swing_lows:
                below = [s.price for s in structure.swing_lows if s.price < plan.entry]
                lvl = max(below) if below else None
            if lvl is None:
                return None
            return lvl - STRUCTURE_SL_BUFFER_ATR * atr_value
        lvl = structure.resistance[0] if structure.resistance else None
        if lvl is None and structure.swing_highs:
            above = [s.price for s in structure.swing_highs if s.price > plan.entry]
            lvl = min(above) if above else None
        if lvl is None:
            return None
        return lvl + STRUCTURE_SL_BUFFER_ATR * atr_value

    candidates: list[tuple[float, str]] = []

    if trader_stop is not None:
        ts = float(trader_stop)
        correct_side = ts < plan.entry if side == "BUY" else ts > plan.entry
        if correct_side:
            candidates.append((ts, "TRADER"))

    struct_sl = structural_stop()
    if struct_sl is not None:
        correct_side = struct_sl < plan.entry if side == "BUY" else struct_sl > plan.entry
        if correct_side:
            candidates.append((struct_sl, "STRUCTURE"))

    atr_sl = (
        plan.entry - atr_sl_multiplier * atr_value
        if side == "BUY"
        else plan.entry + atr_sl_multiplier * atr_value
    )
    candidates.append((atr_sl, "ATR"))

    for cand, source in candidates:
        dist = abs(plan.entry - cand)
        if dist < min_dist:
            continue  # too tight for the broker / spread
        if dist < min_sl_atr_multiple * atr_value:
            continue  # inside the noise band
        if dist > MAX_SL_ATR_MULTIPLE * atr_value:
            continue  # structurally absurd stop
        plan.stop_loss = _round_to_tick(cand, spec.tick_size, spec.digits)
        plan.stop_source = source
        break

    if plan.stop_loss is None:
        plan.invalid_reason = (
            "no valid stop-loss (structure/trader/ATR all failed sanity checks)"
        )
        return plan
    plan.risk_distance = abs(plan.entry - plan.stop_loss)

    # ------------------------------------------------------------------ TP
    risk = plan.risk_distance
    tp_candidates: list[tuple[float, str]] = []

    # Structural targets first (BUY: resistance above; SELL: support below).
    levels = list(structure.resistance) if side == "BUY" else list(structure.support)
    if side == "SELL":
        levels = sorted(levels, reverse=True)
    for lvl in levels:
        tp_candidates.append((float(lvl), "STRUCTURE"))

    # Trader-proposed targets are considered (never authoritative).
    for tp in trader_take_profits or []:
        if tp is None:
            continue
        tp = float(tp)
        correct = tp > plan.entry if side == "BUY" else tp < plan.entry
        if correct:
            tp_candidates.append((tp, "TRADER"))

    # ATR-extension targets (independent volatility measure, not RR math).
    for mult in TP_ATR_CANDIDATES:
        tp_candidates.append(
            (plan.entry + mult * atr_value if side == "BUY"
             else plan.entry - mult * atr_value, "ATR_MULT")
        )

    if allow_rr_fallback:
        tp_candidates.append(
            (plan.entry + min_rr * risk if side == "BUY"
             else plan.entry - min_rr * risk, "RR_FALLBACK")
        )

    def rr_of(tp: float) -> float:
        reward = (tp - plan.entry) if side == "BUY" else (plan.entry - tp)
        return reward / risk if risk > 0 else 0.0

    # Qualifying targets: honest RR >= min_rr (epsilon for float boundaries),
    # and not absurdly far away.
    qualifying = [
        (tp, src) for tp, src in tp_candidates
        if rr_of(tp) >= min_rr - 1e-9 and rr_of(tp) <= 12.0
    ]
    # Structure first (a real level beats a naked volatility multiple), then
    # the trader's own target, then ATR extensions, then the explicit
    # RR fallback. Within the winning priority, nearest wins.
    _priority = {"STRUCTURE": 0, "TRADER": 1, "ATR_MULT": 2, "RR_FALLBACK": 3}
    qualifying.sort(key=lambda t: (_priority.get(t[1], 9),
                                  abs(t[0] - plan.entry)))

    if not qualifying:
        plan.invalid_reason = (
            f"no realistic target satisfies min RR {min_rr:.2f} "
            "(structure and ATR extensions both too close)"
        )
        return plan

    chosen: list[float] = []
    for tp, src in qualifying:
        if len(chosen) >= max(1, tp_levels):
            break
        # TP2/TP3 must be progressively further out.
        if chosen and abs(tp - plan.entry) <= abs(chosen[-1] - plan.entry):
            continue
        chosen.append(_round_to_tick(tp, spec.tick_size, spec.digits))
        if len(chosen) == 1:
            plan.tp_source = src
    plan.take_profits = chosen
    plan.rr = rr_of(plan.take_profits[0])
    plan.valid = True
    return plan


def validate_levels_against_broker(plan: TradePlan,
                                   spec: BrokerSymbolSpec) -> list[str]:
    """Final broker-side validation. Returns a list of problems (empty = OK)."""
    problems: list[str] = []
    if not plan.valid:
        return [plan.invalid_reason or "plan invalid"]
    if plan.entry is None or plan.stop_loss is None:
        return ["missing entry or stop"]

    tick = spec.tick_size if spec.tick_size > 0 else 10 ** (-spec.digits)
    for name, level in (("SL", plan.stop_loss), ("TP1", plan.tp1)):
        if level is None:
            continue
        # Level must be on the correct side.
        if name == "SL":
            if plan.side == "BUY" and level >= plan.entry:
                problems.append("SL not below entry for BUY")
            if plan.side == "SELL" and level <= plan.entry:
                problems.append("SL not above entry for SELL")
        if name == "TP1":
            if plan.side == "BUY" and level <= plan.entry:
                problems.append("TP not above entry for BUY")
            if plan.side == "SELL" and level >= plan.entry:
                problems.append("TP not below entry for SELL")
        # Level must respect the broker's minimum stops distance.
        dist = abs(plan.entry - level)
        min_dist = spec.stops_level_price()
        if min_dist > 0 and dist < min_dist - tick:
            problems.append(
                f"{name} distance {dist:.{spec.digits}f} below broker "
                f"stops level {min_dist:.{spec.digits}f}"
            )
        # Level must align to tick size (no sub-tick prices).
        if tick > 0 and not math.isclose(level / tick, round(level / tick), abs_tol=1e-6):
            problems.append(f"{name} not aligned to tick size {tick}")
    return problems
