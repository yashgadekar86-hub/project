"""Turn a TradingAgents gold decision into an MT5 order.

The multi-agent pipeline ends with a Portfolio Manager rating on a 5-tier
scale (Buy / Overweight / Hold / Underweight / Sell) plus a Trader plan that
may carry explicit entry and stop-loss price levels. This module maps that
decision onto a concrete MT5 market order for gold.

Decision mapping (deliberately conservative):

    Buy        -> BUY   (full conviction long)
    Overweight -> BUY   (still a long; size is governed by risk_percent)
    Hold       -> NONE  (do nothing, but optionally flatten)
    Underweight-> SELL  (short, or flatten a long -- see ``allow_short``)
    Sell       -> SELL
    REVIEW     -> NONE  (unparseable decision is never traded)

Safety:

* ``dry_run`` defaults to ``True``; nothing touches the account until the user
  explicitly opts out.
* ``allow_short`` defaults to ``False`` for gold. Many retail gold accounts
  are long-only CFDs; when the signal is bearish we *flatten* an existing long
  instead of opening a short unless the user opts in.
* ``close_on_hold`` (default ``False``) controls whether a Hold signal flattens
  any existing position; by default Hold means "leave the position alone".
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from tradingagents.agents.utils.rating import extract_rating, is_review

from .mt5_broker import MT5Broker, OrderResult

logger = logging.getLogger(__name__)


# Rating -> direction. Kept as a module constant so tests can assert on it.
RATING_TO_DIRECTION = {
    "Buy": "BUY",
    "Overweight": "BUY",
    "Hold": "HOLD",
    "Underweight": "SELL",
    "Sell": "SELL",
}


@dataclass
class GoldExecutorConfig:
    allow_short: bool = False
    close_on_hold: bool = False
    # When True, an opposite-direction signal flattens the existing position
    # before (possibly) opening the new one. Recommended for netting accounts.
    flatten_on_reversal: bool = True
    # Minimum rating strength required to actually trade. "Overweight" lets
    # the two bullish tiers trade; setting "Buy" trades only the strongest.
    min_strength_to_trade: str = "Overweight"


_STRENGTH = {"Buy": 3, "Overweight": 2, "Hold": 0, "Underweight": 2, "Sell": 3}


def _meets_strength(rating: str, min_strength: str) -> bool:
    return _STRENGTH.get(rating, 0) >= _STRENGTH.get(min_strength, 2)


def extract_price_levels(trader_plan: str) -> tuple[float | None, float | None]:
    """Pull (entry_price, stop_loss) out of the Trader's markdown plan.

    The plan renders ``**Entry Price**: 2412.5`` and ``**Stop Loss**: 2380.0``
    when the Trader could state levels. Both are optional; anything unparsable
    yields ``None`` so the executor falls back to market price / configured
    stop distance instead of guessing.
    """
    entry = stop = None
    if not trader_plan:
        return None, None

    m = re.search(r"Entry Price\*{0,2}\s*[:\-]\s*\**\s*([\d,]+(?:\.\d+)?)", trader_plan, re.I)
    if m:
        entry = _to_float(m.group(1))
    m = re.search(r"Stop Loss\*{0,2}\s*[:\-]\s*\**\s*([\d,]+(?:\.\d+)?)", trader_plan, re.I)
    if m:
        stop = _to_float(m.group(1))
    return entry, stop


def _to_float(text: str) -> float | None:
    try:
        return float(text.replace(",", ""))
    except (TypeError, ValueError):
        return None


class GoldExecutor:
    """Maps the agent decision onto MT5 orders for gold."""

    def __init__(
        self,
        broker: MT5Broker,
        config: GoldExecutorConfig | None = None,
    ):
        self.broker = broker
        self.config = config or GoldExecutorConfig()

    # -- decision parsing ----------------------------------------------------

    def decision_to_direction(self, final_decision: str) -> str | None:
        """Return ``BUY`` / ``SELL`` / ``HOLD`` or ``None`` (no trade)."""
        rating = extract_rating(final_decision)
        if rating is None or is_review(rating):
            logger.warning("No parseable rating (or REVIEW) -> no trade.")
            return None
        direction = RATING_TO_DIRECTION.get(rating)
        if direction is None:
            logger.warning("Unknown rating %r -> no trade.", rating)
            return None
        if direction != "HOLD" and not _meets_strength(rating, self.config.min_strength_to_trade):
            logger.info("Rating %r below trade threshold -> no trade.", rating)
            return "HOLD"
        return direction

    # -- execution -----------------------------------------------------------

    def execute(
        self,
        final_decision: str,
        trader_plan: str = "",
        symbol: str | None = None,
    ) -> list[OrderResult]:
        """Run the decision. Returns a list of OrderResults (may be empty).

        The list form accommodates reversal handling: flattening an existing
        opposite position and then opening the new one are two separate orders.
        """
        direction = self.decision_to_direction(final_decision)
        if direction is None:
            return []

        sym = symbol or self.broker.resolve_symbol()
        open_positions = self.broker.positions(sym)
        results: list[OrderResult] = []

        entry_price, trader_stop = extract_price_levels(trader_plan)

        if direction == "HOLD":
            if self.config.close_on_hold and open_positions:
                results.extend(self._flatten_all(sym, open_positions))
            else:
                logger.info("Hold signal: no action%s.",
                            "" if not open_positions else f" (keeping {len(open_positions)} position)")
            return results

        # Determine current directional exposure (0=buy long, 1=sell short).
        long_pos = [p for p in open_positions if int(p.type) == 0]
        short_pos = [p for p in open_positions if int(p.type) == 1]

        if direction == "BUY":
            if short_pos and self.config.flatten_on_reversal:
                results.extend(self._flatten_all(sym, short_pos))
            if long_pos:
                logger.info("Already long %s; adding not supported, skipping new BUY.", sym)
                return results
            results.append(self._open("BUY", sym, entry_price, trader_stop))

        else:  # SELL
            if long_pos and self.config.flatten_on_reversal:
                results.extend(self._flatten_all(sym, long_pos))
            if short_pos:
                logger.info("Already short %s; skipping new SELL.", sym)
                return results
            if not self.config.allow_short:
                # Bearish signal on a long-only setup: we only ever flatten the
                # long (done above); a fresh short is never opened.
                if not long_pos:
                    logger.info(
                        "Bearish signal but allow_short=False and no long to "
                        "flatten; no order placed."
                    )
                return results
            results.append(self._open("SELL", sym, entry_price, trader_stop))

        return results

    # -- helpers -------------------------------------------------------------

    def _flatten_all(self, sym, positions) -> list[OrderResult]:
        out = []
        for pos in positions:
            action = "CLOSE_BUY" if int(pos.type) == 0 else "CLOSE_SELL"
            out.append(self.broker.market_order(action, symbol=sym,
                                                comment="TA-flatten"))
        return out

    def _open(self, action: str, sym: str, entry_price, trader_stop) -> OrderResult:
        """Open a position at market, deriving SL/TP.

        SL logic (anchored to the actual market entry price):
          1. Trader's stop loss if present and on the correct side of entry.
          2. Configured ``stop_loss_distance`` away from entry.
          3. Broker default (~0.5% of price).
        TP is placed at ``take_profit_distance`` when configured, otherwise at
        the same distance as the SL (1:1). The Trader's ``entry_price`` target
        is informational only -- this executor fills at market.
        """
        cfg = self.broker.config
        tick = self.broker.tick(sym)
        info = self.broker.symbol_info(sym)
        digits = int(getattr(info, "digits", 2) or 2)

        if action == "BUY":
            price = round(float(tick.ask), digits)
            if trader_stop is not None and trader_stop < price:
                sl = round(trader_stop, digits)
            else:
                dist = cfg.stop_loss_distance or self.broker._default_stop_distance(tick, info)
                sl = round(price - dist, digits)
            tp_dist = cfg.take_profit_distance or (price - sl)
            tp = round(price + tp_dist, digits) if tp_dist > 0 else None
        else:
            price = round(float(tick.bid), digits)
            if trader_stop is not None and trader_stop > price:
                sl = round(trader_stop, digits)
            else:
                dist = cfg.stop_loss_distance or self.broker._default_stop_distance(tick, info)
                sl = round(price + dist, digits)
            tp_dist = cfg.take_profit_distance or (sl - price)
            tp = round(price - tp_dist, digits) if tp_dist > 0 else None

        if entry_price is not None:
            logger.info(
                "Trader entry target %s noted; filling %s at market %s.",
                entry_price, action, price,
            )

        # Size from the actual SL distance so risk_percent is honored exactly.
        sl_distance = abs(price - sl)
        if cfg.fixed_lots is not None:
            volume = cfg.fixed_lots
        else:
            volume = self.broker.calculate_volume(sl_distance, sym)

        return self.broker.market_order(
            action,
            volume=volume,
            sl=sl,
            tp=tp,
            price=price,
            symbol=sym,
            comment="TradingAgents-gold",
        )
