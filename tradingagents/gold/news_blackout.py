"""Deterministic high-impact news blackout filter (Phase 7).

A *blackout* is a time window around a scheduled high-impact US event in which
the final decision is forced to HOLD — regardless of what the LLM says. The
LLM can NEVER override this; only the operator flag
``ALLOW_TRADE_DURING_BLACKOUT`` (environment / config) can, and it is
explicitly not exposed to the agent layer.

Event sources, in priority order:

1. A user-supplied JSON calendar (``GOLD_EVENT_CALENDAR`` path) — exact
   dates/times for CPI, PPI, GDP, speeches, or anything else. This is the
   recommended way to get precise CPI/PPI/GDP release times.
2. Built-in recurrence rules for events with fixed, published schedules:
   NFP (first Friday of the month, 08:30 US Eastern) and FOMC decisions
   (second day of the scheduled 2025/2026 meetings, 14:00 US Eastern).

Times are defined in **US Eastern** and converted with the IANA zone database
so DST transitions are always correct.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .models import BlackoutStatus, HighImpactEvent

logger = logging.getLogger(__name__)

_ET = ZoneInfo("America/New_York")
_UTC = timezone.utc

# Scheduled FOMC meetings (published by the Federal Reserve). The rate
# decision + press conference happen on the SECOND day at 14:00 ET.
# Sources: federalreserve.gov meeting calendars.
FOMC_DECISION_DATES = {
    2025: ["2025-01-29", "2025-03-19", "2025-05-07", "2025-06-18",
           "2025-07-30", "2025-09-17", "2025-10-29", "2025-12-10"],
    2026: ["2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17",
           "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-09"],
}

# Codes the deterministic layer treats as high impact by default.
DEFAULT_HIGH_IMPACT_CODES = {
    "CPI", "NFP", "FOMC", "FED_RATE", "POWELL", "PPI", "GDP",
    "NONFARM", "UNEMPLOYMENT",
}


def _et(year: int, month: int, day: int, hour: int, minute: int) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=_ET).astimezone(_UTC)


def _first_friday(year: int, month: int) -> int:
    """Day-of-month of the first Friday (NFP release day)."""
    d = datetime(year, month, 1)
    # weekday(): Mon=0 .. Sun=6; Friday=4
    offset = (4 - d.weekday()) % 7
    return 1 + offset


def generate_recurring_events(from_dt: datetime, to_dt: datetime,
                              codes: set[str] | None = None) -> list[HighImpactEvent]:
    """Deterministic events with fixed schedules inside [from_dt, to_dt].

    NFP: first Friday of every month at 08:30 ET.
    FOMC: the published decision dates at 14:00 ET.
    """
    codes = codes or DEFAULT_HIGH_IMPACT_CODES
    events: list[HighImpactEvent] = []
    start = from_dt.astimezone(_UTC)
    end = to_dt.astimezone(_UTC)

    if "NFP" in codes or "NONFARM" in codes:
        cur = datetime(start.year, start.month, 1, tzinfo=_UTC)
        while cur <= end:
            day = _first_friday(cur.year, cur.month)
            when = _et(cur.year, cur.month, day, 8, 30)
            if start - timedelta(days=2) <= when <= end + timedelta(days=2):
                events.append(HighImpactEvent(
                    code="NFP",
                    name="US Non-Farm Payrolls (BLS)",
                    timestamp=when,
                    source="RECURRENCE:first-friday-0830ET",
                ))
            # advance one month
            cur = (cur.replace(day=1) + timedelta(days=32)).replace(day=1)

    if "FOMC" in codes or "FED_RATE" in codes:
        for _year, dates in FOMC_DECISION_DATES.items():
            for d in dates:
                y, m, dd = (int(x) for x in d.split("-"))
                when = _et(y, m, dd, 14, 0)
                if start <= when <= end:
                    events.append(HighImpactEvent(
                        code="FOMC",
                        name=f"FOMC rate decision {d}",
                        timestamp=when,
                        source="STATIC:FOMC-calendar",
                    ))
    events.sort(key=lambda e: e.timestamp)
    return events


def load_calendar_file(path: str | Path) -> list[HighImpactEvent]:
    """Load a user-supplied calendar JSON.

    Expected format (times in ISO-8601 with offset; all-day events use
    00:00 local machine time)::

        [
          {"code": "CPI", "name": "US CPI (Nov)", "time": "2026-03-11T08:30:00-05:00"},
          {"code": "POWELL", "name": "Powell testimony", "time": "2026-03-04T10:00:00-05:00"}
        ]
    """
    p = Path(path)
    if not p.exists():
        logger.warning("Event calendar file %s not found; skipping.", p)
        return []
    data = json.loads(p.read_text(encoding="utf-8"))
    events: list[HighImpactEvent] = []
    for item in data:
        when = datetime.fromisoformat(str(item["time"]))
        if when.tzinfo is None:
            when = when.replace(tzinfo=_ET)
        events.append(HighImpactEvent(
            code=str(item.get("code", "UNKNOWN")).upper(),
            name=str(item.get("name", item.get("code", "event"))),
            timestamp=when.astimezone(_UTC),
            source=f"FILE:{p.name}",
        ))
    events.sort(key=lambda e: e.timestamp)
    return events


@dataclass
class NewsBlackoutCalendar:
    """Deterministic blackout evaluator."""

    before_minutes: int = 30
    after_minutes: int = 30
    events: list[HighImpactEvent] = field(default_factory=list)

    # -- construction --------------------------------------------------------

    @classmethod
    def build(cls, now: datetime, before_minutes: int = 30,
              after_minutes: int = 30, codes: set[str] | None = None,
              calendar_file: str | Path | None = None) -> NewsBlackoutCalendar:
        """Assemble the calendar: user file events + recurring rules."""
        events: list[HighImpactEvent] = []
        if calendar_file:
            events.extend(load_calendar_file(calendar_file))
        # A generous window so rules cover events near `now` in either zone.
        events += generate_recurring_events(
            now - timedelta(days=3), now + timedelta(days=40), codes=codes
        )
        # De-duplicate identical (code, timestamp) pairs.
        seen: set[tuple[str, datetime]] = set()
        unique = []
        for e in sorted(events, key=lambda e: e.timestamp):
            key = (e.code, e.timestamp)
            if key not in seen:
                seen.add(key)
                unique.append(e)
        return cls(before_minutes=before_minutes,
                   after_minutes=after_minutes, events=unique)

    # -- evaluation ----------------------------------------------------------

    def _nearest(self, now: datetime) -> HighImpactEvent | None:
        nearest, best = None, None
        for e in self.events:
            delta = abs((e.timestamp - now).total_seconds())
            if best is None or delta < best:
                nearest, best = e, delta
        return nearest

    def check(self, now: datetime) -> BlackoutStatus:
        """Is ``now`` inside any blackout window?"""
        now = now.astimezone(_UTC) if now.tzinfo else now.replace(tzinfo=_UTC)
        for e in self.events:
            open_start = e.timestamp - timedelta(minutes=self.before_minutes)
            open_end = e.timestamp + timedelta(minutes=self.after_minutes)
            if open_start <= now <= open_end:
                mins = (e.timestamp - now).total_seconds() / 60.0
                return BlackoutStatus(
                    active=True,
                    reason=(
                        f"NEWS BLACKOUT: {e.code} ({e.name}) at "
                        f"{e.timestamp.isoformat()}; now is "
                        f"{f'{mins:.0f} min before' if mins >= 0 else f'{-mins:.0f} min after'}"
                    ),
                    event=e,
                    minutes_to_event=mins,
                )
        nearest = self._nearest(now)
        if nearest is not None:
            return BlackoutStatus(
                active=False, reason="No high-impact event inside window.",
                event=nearest,
                minutes_to_event=(nearest.timestamp - now).total_seconds() / 60.0,
            )
        return BlackoutStatus(active=False, reason="No events on calendar.")
