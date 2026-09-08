"""Phase 7 + 18 tests: news blackout calendar and session filter."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from tradingagents.gold.news_blackout import (
    FOMC_DECISION_DATES,
    NewsBlackoutCalendar,
    generate_recurring_events,
)
from tradingagents.gold.sessions import (
    active_sessions,
    check_session,
    session_windows_display,
)


# ------------------------------------------------------------------ FOMC data
def test_fomc_dates_are_the_published_2026_schedule():
    assert FOMC_DECISION_DATES[2026] == [
        "2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17",
        "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-09",
    ]


def test_fomc_blackout_before_and_after():
    cal = NewsBlackoutCalendar.build(
        datetime(2026, 9, 8, tzinfo=timezone.utc), 30, 30
    )
    # decision day 2026-09-16 14:00 ET == 18:00 UTC
    assert cal.check(datetime(2026, 9, 16, 17, 45, tzinfo=timezone.utc)).active
    assert cal.check(datetime(2026, 9, 16, 18, 15, tzinfo=timezone.utc)).active
    assert not cal.check(datetime(2026, 9, 16, 17, 25, tzinfo=timezone.utc)).active
    assert not cal.check(datetime(2026, 9, 16, 18, 35, tzinfo=timezone.utc)).active


def test_nfp_first_friday_blackout():
    cal = NewsBlackoutCalendar.build(
        datetime(2026, 9, 8, tzinfo=timezone.utc), 30, 30
    )
    # Oct 2026 first Friday = Oct 2, 08:30 ET (EDT) = 12:30 UTC
    assert cal.check(datetime(2026, 10, 2, 12, 20, tzinfo=timezone.utc)).active
    assert not cal.check(datetime(2026, 10, 2, 11, 50, tzinfo=timezone.utc)).active


def test_dst_is_handled_by_zoneinfo():
    """NFP is 08:30 ET year-round: 12:30 UTC in EDT, 13:30 UTC in EST."""
    cal = NewsBlackoutCalendar.build(
        datetime(2026, 1, 2, tzinfo=timezone.utc), 30, 30
    )
    # Jan 2026 first Friday = Jan 2, EST -> 13:30 UTC
    assert cal.check(datetime(2026, 1, 2, 13, 25, tzinfo=timezone.utc)).active


def test_blackout_reason_names_the_event():
    cal = NewsBlackoutCalendar.build(
        datetime(2026, 9, 8, tzinfo=timezone.utc), 30, 30
    )
    status = cal.check(datetime(2026, 9, 16, 17, 50, tzinfo=timezone.utc))
    assert status.active
    assert "FOMC" in status.reason
    assert status.event is not None and status.event.code == "FOMC"


def test_recurring_events_window():
    lo = datetime(2026, 9, 1, tzinfo=timezone.utc)
    hi = datetime(2026, 9, 30, tzinfo=timezone.utc)
    events = generate_recurring_events(lo, hi)
    codes = [e.code for e in events]
    assert codes.count("NFP") == 1  # Sep 4 first Friday
    assert codes.count("FOMC") == 1  # Sep 16


def test_calendar_file_events_are_loaded(tmp_path: Path):
    f = tmp_path / "calendar.json"
    f.write_text(json.dumps([
        {"code": "CPI", "name": "US CPI (Aug)",
         "time": "2026-09-15T08:30:00-04:00"},
        {"code": "POWELL", "name": "Powell speech",
         "time": "2026-09-10T14:00:00+00:00"},
    ]), encoding="utf-8")
    cal = NewsBlackoutCalendar.build(
        datetime(2026, 9, 9, tzinfo=timezone.utc), 30, 30, calendar_file=f
    )
    # CPI at 12:30 UTC on Sep 15
    assert cal.check(datetime(2026, 9, 15, 12, 25, tzinfo=timezone.utc)).active
    assert cal.check(datetime(2026, 9, 10, 13, 40, tzinfo=timezone.utc)).active


def test_missing_calendar_file_degrades_gracefully(tmp_path: Path):
    cal = NewsBlackoutCalendar.build(
        datetime(2026, 9, 9, tzinfo=timezone.utc), 30, 30,
        calendar_file=tmp_path / "nope.json",
    )
    assert cal.events  # recurring rules still populated


def test_custom_before_after_windows():
    cal = NewsBlackoutCalendar.build(
        datetime(2026, 9, 8, tzinfo=timezone.utc), before_minutes=120,
        after_minutes=0,
    )
    # 2h before FOMC (18:00 UTC): 16:30 is inside, 15:45 is not
    assert cal.check(datetime(2026, 9, 16, 16, 30, tzinfo=timezone.utc)).active
    assert not cal.check(datetime(2026, 9, 16, 15, 45, tzinfo=timezone.utc)).active


# ------------------------------------------------------------------- sessions
def test_sessions_utc_definition():
    from datetime import datetime, timezone

    def dt(h, m=0):
        return datetime(2026, 9, 9, h, m, tzinfo=timezone.utc)
    assert active_sessions(dt(3)) == ["Asian"]
    assert set(active_sessions(dt(9))) == {"London"}
    assert set(active_sessions(dt(14))) == {"London", "NewYork", "Overlap"}
    assert set(active_sessions(dt(18))) == {"NewYork"}
    assert active_sessions(dt(21)) == []


def test_session_gate_default_allows_london_ny_overlap():
    status = check_session(
        datetime(2026, 9, 9, 14, 0, tzinfo=timezone.utc),
        ["London", "NewYork", "Overlap"],
    )
    assert status.allowed


def test_session_gate_blocks_asian_by_default():
    status = check_session(
        datetime(2026, 9, 9, 3, 0, tzinfo=timezone.utc),
        ["London", "NewYork", "Overlap"],
    )
    assert not status.allowed
    assert status.current_sessions == ["Asian"]


def test_session_gate_configurable():
    status = check_session(
        datetime(2026, 9, 9, 3, 0, tzinfo=timezone.utc), ["Asian"]
    )
    assert status.allowed


def test_session_display_in_configured_timezone_not_hardcoded():
    """IST display works via IANA zones; nothing is hard-coded to IST."""
    windows = session_windows_display(
        datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc), "Asia/Kolkata"
    )
    assert windows["Asian"].startswith("05:30")
    assert "Asia/Kolkata" in windows["Asian"]
    utc_windows = session_windows_display(
        datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc), "UTC"
    )
    assert utc_windows["Asian"].startswith("00:00")


def test_session_display_bad_tz_falls_back_to_utc():
    windows = session_windows_display(
        datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc), "Not/AZone"
    )
    assert "UTC" in windows["Asian"]


def test_session_describe_mentions_permission():
    ok = check_session(
        datetime(2026, 9, 9, 14, 0, tzinfo=timezone.utc), ["Overlap"]
    )
    assert "ALLOWED" in ok.describe()
    blocked = check_session(
        datetime(2026, 9, 9, 21, 0, tzinfo=timezone.utc), ["Overlap"]
    )
    assert "BLOCKED" in blocked.describe()
