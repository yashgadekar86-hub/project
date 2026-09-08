"""FX/gold session filter (Phase 18).

Session windows are defined in **UTC** (fixed, documented approximations of
the real dealing sessions) so behaviour never depends on the machine's local
clock. The configurable IANA timezone is used for *display* and for the
daily-boundary logic, never hard-coded to IST or any other zone.

    Asian   00:00–08:00 UTC   (Tokyo open)
    London  07:00–16:00 UTC   (London dealing hours)
    NewYork 12:00–20:00 UTC   (US session)
    Overlap 12:00–16:00 UTC   (London+NY liquidity window)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

# name -> (start_hour_utc, end_hour_utc), end exclusive.
SESSIONS_UTC: dict[str, tuple[int, int]] = {
    "Asian": (0, 8),
    "London": (7, 16),
    "NewYork": (12, 20),
    "Overlap": (12, 16),
}


@dataclass
class SessionStatus:
    current_sessions: list[str]
    allowed: bool
    display_tz: str
    detail: str = ""

    def describe(self) -> str:
        active = ", ".join(self.current_sessions) if self.current_sessions else "none (off-hours)"
        return (
            f"Session(s) active: {active} | trading "
            f"{'ALLOWED' if self.allowed else 'BLOCKED'} (times shown in {self.display_tz})"
        )


def _as_utc(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc)


def active_sessions(now: datetime | None = None) -> list[str]:
    """Names of sessions whose UTC window contains ``now``."""
    utc_now = _as_utc(now)
    hour = utc_now.hour + utc_now.minute / 60.0
    out = []
    for name, (start, end) in SESSIONS_UTC.items():
        if start <= hour < end:
            out.append(name)
    return out


def session_windows_display(now: datetime | None = None,
                            tz_name: str = "UTC") -> dict[str, str]:
    """Render each session's UTC window in the configured display timezone."""
    try:
        tz = ZoneInfo(tz_name)
    except Exception:  # noqa: BLE001 - bad tz falls back to UTC, loudly labelled
        tz = ZoneInfo("UTC")
        tz_name = "UTC"
    utc_now = _as_utc(now)
    base = utc_now.replace(hour=0, minute=0, second=0, microsecond=0)
    out = {}
    for name, (start, end) in SESSIONS_UTC.items():
        s = base.replace(hour=start).astimezone(tz).strftime("%H:%M")
        e = base.replace(hour=end % 24).astimezone(tz).strftime("%H:%M")
        out[name] = f"{s}-{e} {tz_name}"
    return out


def check_session(now: datetime | None, allowed_sessions: list[str],
                  tz_name: str = "UTC") -> SessionStatus:
    """Deterministic session gate."""
    current = active_sessions(now)
    allowed_set = {s.strip() for s in allowed_sessions if s.strip()}
    allowed = bool(allowed_set) and any(s in allowed_set for s in current)
    windows = session_windows_display(now, tz_name)
    detail = "; ".join(f"{k}: {v}" for k, v in windows.items())
    return SessionStatus(
        current_sessions=current, allowed=allowed,
        display_tz=tz_name, detail=detail,
    )
