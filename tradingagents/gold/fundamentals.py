"""Gold-specific fundamentals (Phase 5).

Gold has no EPS/P&E/revenue. Its "fundamentals" are the macro drivers:

* DXY / dollar strength
* US Treasury nominal + real yields (opportunity cost of gold)
* Fed funds rate and policy expectations
* Inflation (CPI, PPI, expectations)
* Growth/labour (GDP, NFP, unemployment) as policy inputs
* Central-bank purchases / ETF flows (reported, lagged)

This module assembles :class:`GoldFundamentals` with full provenance
(value, timestamp, source, freshness). Unavailable data is ``UNKNOWN`` —
never fabricated. Deterministic scoring feeds the confidence engine; the
qualitative interpretation stays with the LLM analysts.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from .models import Bias, GoldFundamentalPoint, GoldFundamentals

logger = logging.getLogger(__name__)

# name -> (FRED alias, unit, human label, max_age_hours for "fresh")
GOLD_MACRO_SERIES = [
    ("real_yield_10y", "real_yield_10y", "%", "10y TIPS real yield", 72),
    ("real_yield_5y", "real_yield_5y", "%", "5y TIPS real yield", 72),
    ("nominal_yield_10y", "10y_treasury", "%", "10y Treasury yield", 72),
    ("nominal_yield_2y", "2y_treasury", "%", "2y Treasury yield", 72),
    ("fed_funds_rate", "fed_funds_rate", "%", "Fed funds rate", 24 * 45),
    ("dollar_index", "dxy", "index", "Broad dollar index", 24 * 10),
    ("cpi_yoy", "cpi", "index", "CPI (SA)", 24 * 45),
    ("core_cpi", "core_cpi", "index", "Core CPI (SA)", 24 * 45),
    ("inflation_expectations", "inflation_expectations", "%", "10y breakeven", 72),
    ("gold_fix", "gold_price", "USD/oz", "LBMA PM gold fix", 24 * 10),
]


def _parse_date(text: str) -> datetime | None:
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def collect_fundamentals(
    curr_date: str,
    now: datetime | None = None,
    fetcher=None,
    look_back_days: int = 400,
) -> GoldFundamentals:
    """Assemble the gold macro dashboard.

    ``fetcher(alias, curr_date, look_back_days) -> list[(date_str, value)]``
    is injectable for tests; the default wraps the FRED vendor (point-in-time
    safe). Any failure at the series level degrades to UNKNOWN for that
    series only — the rest of the dashboard still builds.
    """
    if fetcher is None:
        from tradingagents.dataflows.fred import get_series_points

        fetcher = get_series_points
    now = now or datetime.now(timezone.utc)

    points: list[GoldFundamentalPoint] = []
    for name, alias, unit, _label, max_age in GOLD_MACRO_SERIES:
        pt = GoldFundamentalPoint(name=name, unit=unit, source="UNKNOWN")
        try:
            rows = fetcher(alias, curr_date, look_back_days)
        except Exception as exc:  # noqa: BLE001 - degrade to UNKNOWN
            logger.debug("fundamental %s fetch failed: %s", name, exc)
            rows = []
        if rows:
            date_str, value = rows[-1]
            ts = _parse_date(date_str)
            pt.value = float(value)
            pt.timestamp = ts
            pt.source = "FRED"
            if ts is not None:
                pt.freshness_hours = (now - ts).total_seconds() / 3600.0
                if pt.freshness_hours > max_age:
                    pt.source = f"FRED (STALE {pt.freshness_hours:.0f}h)"
        points.append(pt)

    return GoldFundamentals(points=points)


def score_fundamentals(f: GoldFundamentals) -> float:
    """Deterministic 0..1 score of how *available* the macro picture is.

    This is deliberately a DATA-AVAILABILITY score (not a direction score):
    direction interpretation is the LLM's job; we only grade how much hard
    evidence the decision rests on. Missing/stale series reduce the score.
    """
    if not f.points:
        return 0.0
    total = 0.0
    for p in f.points:
        if not p.available:
            continue
        weight = 1.0
        if p.freshness_hours is not None and p.freshness_hours > 24 * 40:
            weight = 0.6  # monthly series are naturally lagged; partial credit
        total += weight
    return round(min(total / len(f.points), 1.0), 4)


def fundamentals_direction(f: GoldFundamentals) -> Bias:
    """Crude deterministic macro tilt used ONLY for context, not decisions.

    Real yields and the dollar are gold's two dominant drivers: both down is
    supportive, both up is hostile. This never vetoes or approves a trade on
    its own — it is reported alongside the LLM's interpretation.
    """
    real = f.get("real_yield_10y")
    nom = f.get("nominal_yield_10y")
    dxy = f.get("dollar_index")

    bull = 0
    bear = 0
    # Trend needs two observations; with only the level we stay neutral.
    for pt in (real, nom, dxy):
        if pt is None or not pt.available:
            continue
    # Level-based heuristic vs long-run anchors (documented, crude).
    if real is not None and real.available and real.value is not None:
        if real.value <= 0.5:
            bull += 1
        elif real.value >= 2.0:
            bear += 1
    if dxy is not None and dxy.available and dxy.value is not None:
        # DTWEXBGS long-run ~110-120
        if dxy.value <= 105:
            bull += 1
        elif dxy.value >= 120:
            bear += 1

    if bull >= 2 and bear == 0:
        return Bias.BULLISH
    if bear >= 2 and bull == 0:
        return Bias.BEARISH
    return Bias.NEUTRAL
