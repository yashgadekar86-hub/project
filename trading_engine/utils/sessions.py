"""Trading session detection (Sydney / Tokyo / London / New York) in UTC."""
from __future__ import annotations

from datetime import datetime, time, timezone
from typing import List, Optional, Tuple


# Approximate UTC open/close times for each major FX session
SESSIONS = {
    "Sydney":  {"open": time(22, 0), "close": time(7, 0)},
    "Tokyo":   {"open": time(0, 0),  "close": time(9, 0)},
    "London":  {"open": time(7, 0),  "close": time(16, 0)},
    "NewYork": {"open": time(13, 0), "close": time(22, 0)},
}


def _time_to_minutes(t: time) -> int:
    return t.hour * 60 + t.minute


def current_sessions(now: Optional[datetime] = None) -> List[str]:
    """Return list of currently open sessions (handles Sydney wrapping over midnight)."""
    if now is None:
        now = datetime.now(timezone.utc)
    t = now.time()
    t_min = _time_to_minutes(t)
    open_sessions: List[str] = []
    for name, hrs in SESSIONS.items():
        o = _time_to_minutes(hrs["open"])
        c = _time_to_minutes(hrs["close"])
        if o <= c:
            is_open = o <= t_min < c
        else:
            # wraps midnight (e.g., Sydney 22:00-07:00)
            is_open = t_min >= o or t_min < c
        if is_open:
            open_sessions.append(name)
    return open_sessions


def next_session(now: Optional[datetime] = None) -> Tuple[str, int]:
    """Return (session_name, minutes_until_open) for the next session to open."""
    if now is None:
        now = datetime.now(timezone.utc)
    t_min = _time_to_minutes(now.time())
    candidates = []
    for name, hrs in SESSIONS.items():
        o = _time_to_minutes(hrs["open"])
        minutes_to_open = (o - t_min) % (24 * 60)
        candidates.append((name, minutes_to_open))
    candidates.sort(key=lambda x: x[1])
    return candidates[0]


def is_overlap(s1: str, s2: str, now: Optional[datetime] = None) -> bool:
    open_now = current_sessions(now)
    return s1 in open_now and s2 in open_now


def london_newyork_overlap(now: Optional[datetime] = None) -> bool:
    return is_overlap("London", "NewYork", now)
