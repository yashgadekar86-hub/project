"""Persistent state for the gold system (Phases 15, 25, 26).

A tiny JSON store (default ``results_dir/gold_state.json``) holding exactly
what the deterministic gates need to survive across process runs:

* the trading date + ``trades_today`` / ``realized_pnl_today`` counters
  (daily loss and trade-count limits),
* ``consecutive_losses``,
* the last executed ``signal_id`` + time (idempotency / dedupe window),
* open paper positions (paper-trading continuity).

Counters reset automatically when the calendar day (in the configured
timezone) changes. The store is deliberately dumb: no logic lives here, and
memory/research NEVER reads safety state — only the engine writes it.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)


@dataclass
class DayCounters:
    date: str = ""
    trades_today: int = 0
    realized_pnl_today: float = 0.0
    consecutive_losses: int = 0
    last_signal_id: str = ""
    last_signal_time: str = ""
    # Paper-trading continuity (persists across days; NOT day-scoped).
    paper_balance: float = 10_000.0
    open_paper_positions: list[dict] = field(default_factory=list)


class GoldStateStore:
    """Load/save the daily safety counters. Corrupt file -> fresh state."""

    def __init__(self, path: str | Path, tz_name: str = "UTC"):
        self.path = Path(path)
        self.tz_name = tz_name
        self.counters = DayCounters()
        self._load()

    # -- persistence ---------------------------------------------------------

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.counters = DayCounters(**{
                k: data.get(k) for k in DayCounters().__dict__
            })
        except (json.JSONDecodeError, TypeError, OSError) as exc:
            logger.warning("gold state file %s unreadable (%s); starting fresh.",
                           self.path, exc)
            self.counters = DayCounters()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(asdict(self.counters), indent=2), encoding="utf-8"
        )

    # -- day rollover --------------------------------------------------------

    def _today(self, now: datetime) -> str:
        try:
            tz = ZoneInfo(self.tz_name)
        except Exception:  # noqa: BLE001
            tz = timezone.utc
        return now.astimezone(tz).strftime("%Y-%m-%d")

    def rollover(self, now: datetime) -> None:
        """Reset day-scoped counters when the configured day changes."""
        today = self._today(now)
        if self.counters.date != today:
            self.counters.date = today
            self.counters.trades_today = 0
            self.counters.realized_pnl_today = 0.0
            # consecutive_losses and last_signal_id persist across days.

    # -- mutations (called by the runner after an execution/paper fill) ------

    def record_trade(self, pnl: float, now: datetime) -> None:
        self.rollover(now)
        self.counters.trades_today += 1
        self.counters.realized_pnl_today += round(float(pnl), 2)
        if pnl < 0:
            self.counters.consecutive_losses += 1
        elif pnl > 0:
            self.counters.consecutive_losses = 0

    def record_signal(self, signal_id: str, now: datetime) -> None:
        self.counters.last_signal_id = signal_id
        self.counters.last_signal_time = now.isoformat(timespec="seconds")

    def set_paper_positions(self, positions: list[dict]) -> None:
        self.counters.open_paper_positions = positions
