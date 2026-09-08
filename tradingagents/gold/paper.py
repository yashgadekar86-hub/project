"""Paper trading simulator (Phase 27).

Conservative execution assumptions (documented, deliberate):

* **Entry**: filled at the ask (+ configured slippage) for BUY, at the bid
  (− slippage) for SELL. We always pay the spread.
* **SL/TP**: evaluated against the candle's high/low. **If a candle touches
  both SL and TP, the SL is assumed to have filled first** — the pessimistic
  reading. (Without tick data inside the bar, the optimistic reading
  systematically overstates backtest/paper results.)
* **Commission** (per lot, per side) and **slippage** (points, per side) are
  configurable and default to 0 only when unset — production use should set
  realistic values.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from .models import BrokerSymbolSpec

logger = logging.getLogger(__name__)


@dataclass
class PaperPosition:
    side: str                 # "BUY" | "SELL"
    entry: float
    stop_loss: float
    take_profits: list[float]
    volume: float
    opened_at: datetime
    risk_amount: float = 0.0
    signal_id: str = ""
    tp_hit: int = 0           # how many TPs filled (0 = still open)

    def to_dict(self) -> dict:
        return {
            "side": self.side, "entry": self.entry, "sl": self.stop_loss,
            "tps": self.take_profits, "volume": self.volume,
            "opened_at": self.opened_at.isoformat(timespec="seconds"),
            "risk_amount": self.risk_amount, "signal_id": self.signal_id,
            "tp_hit": self.tp_hit,
        }

    @classmethod
    def from_dict(cls, d: dict) -> PaperPosition:
        return cls(
            side=d["side"], entry=float(d["entry"]), stop_loss=float(d["sl"]),
            take_profits=[float(t) for t in d.get("tps", [])],
            volume=float(d["volume"]),
            opened_at=datetime.fromisoformat(d["opened_at"]),
            risk_amount=float(d.get("risk_amount", 0.0)),
            signal_id=d.get("signal_id", ""),
            tp_hit=int(d.get("tp_hit", 0)),
        )


@dataclass
class ClosedTrade:
    side: str
    entry: float
    exit: float
    volume: float
    pnl: float
    r_multiple: float
    opened_at: datetime
    closed_at: datetime
    exit_reason: str          # "SL" | "TP1" | "TP2" | "TP3" | "EOD" | "MANUAL"
    signal_id: str = ""
    commission: float = 0.0

    @property
    def holding_hours(self) -> float:
        return (self.closed_at - self.opened_at).total_seconds() / 3600.0


@dataclass
class PaperResult:
    filled: bool
    position: PaperPosition | None = None
    reason: str = ""


class PaperBroker:
    """Deterministic paper execution + position lifecycle."""

    def __init__(
        self,
        spec: BrokerSymbolSpec,
        starting_balance: float = 10_000.0,
        commission_per_lot: float = 0.0,
        slippage_points: float = 0.0,
    ):
        self.spec = spec
        self.starting_balance = float(starting_balance)
        self.balance = float(starting_balance)
        self.commission_per_lot = float(commission_per_lot)
        self.slippage_points = float(slippage_points)
        self.position: PaperPosition | None = None
        self.trades: list[ClosedTrade] = []
        self.equity_curve: list[tuple[datetime, float]] = []

    # -- economics -----------------------------------------------------------

    def _slippage_price(self) -> float:
        return self.slippage_points * self.spec.point

    def pnl_of(self, side: str, entry: float, exit_price: float,
               volume: float) -> float:
        """Gross P/L in account currency for a round turn."""
        diff = (exit_price - entry) if side == "BUY" else (entry - exit_price)
        gross = diff * self.spec.contract_size * volume
        return round(gross, 2)

    def _commission(self, volume: float) -> float:
        return round(self.commission_per_lot * volume * 2.0, 2)  # in + out

    # -- orders --------------------------------------------------------------

    def open(self, side: str, entry: float, stop_loss: float,
             take_profits: list[float], volume: float, now: datetime,
             risk_amount: float = 0.0, signal_id: str = "",
             ask: float | None = None, bid: float | None = None) -> PaperResult:
        if self.position is not None:
            return PaperResult(False, reason="paper position already open")
        slip = self._slippage_price()
        if side == "BUY":
            fill = (ask if ask is not None else entry) + slip
        else:
            fill = (bid if bid is not None else entry) - slip
        fill = round(fill, self.spec.digits)
        self.position = PaperPosition(
            side=side, entry=fill, stop_loss=stop_loss,
            take_profits=list(take_profits), volume=volume, opened_at=now,
            risk_amount=risk_amount, signal_id=signal_id,
        )
        # Commission is charged once per round turn, at close (see _commission).
        return PaperResult(True, position=self.position)

    def close_at(self, price: float, now: datetime,
                 reason: str = "MANUAL") -> ClosedTrade | None:
        """Close the open position at ``price`` (bid for BUY exit, ask for SELL)."""
        pos = self.position
        if pos is None:
            return None
        exit_price = round(price, self.spec.digits)
        gross = self.pnl_of(pos.side, pos.entry, exit_price, pos.volume)
        commission = self._commission(pos.volume)
        pnl = round(gross - commission, 2)
        risk = pos.risk_amount or 0.0
        r = round(pnl / risk, 3) if risk > 0 else 0.0
        trade = ClosedTrade(
            side=pos.side, entry=pos.entry, exit=exit_price, volume=pos.volume,
            pnl=pnl, r_multiple=r, opened_at=pos.opened_at, closed_at=now,
            exit_reason=reason, signal_id=pos.signal_id, commission=commission,
        )
        self.trades.append(trade)
        self.balance += pnl
        self.position = None
        return trade

    # -- candle processing ----------------------------------------------------

    def on_candle(self, candle, now: datetime | None = None) -> ClosedTrade | None:
        """Process one candle against the open position (SL-first rule)."""
        pos = self.position
        if pos is None:
            return None
        now = now or getattr(candle, "name", None) or datetime.now(timezone.utc)
        high = float(candle["high"])
        low = float(candle["low"])

        def touched(level: float) -> bool:
            return low <= level <= high

        if pos.side == "BUY":
            # PESSIMISTIC: if both SL and TP are inside the bar, SL first.
            if touched(pos.stop_loss):
                return self.close_at(pos.stop_loss, now, "SL")
            for i, tp in enumerate(pos.take_profits, start=1):
                if touched(tp):
                    return self.close_at(tp, now, f"TP{i}")
        else:
            if touched(pos.stop_loss):
                return self.close_at(pos.stop_loss, now, "SL")
            for i, tp in enumerate(pos.take_profits, start=1):
                if touched(tp):
                    return self.close_at(tp, now, f"TP{i}")
        return None

    def unrealized(self, price: float) -> float:
        pos = self.position
        if pos is None:
            return 0.0
        return self.pnl_of(pos.side, pos.entry, price, pos.volume)

    def equity(self, price: float) -> float:
        return round(self.balance + self.unrealized(price), 2)

    def mark(self, now: datetime, price: float) -> None:
        self.equity_curve.append((now, self.equity(price)))
