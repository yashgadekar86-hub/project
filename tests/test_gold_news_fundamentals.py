"""Phases 5, 6 tests: gold fundamentals provenance + news freshness/scoring."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tradingagents.gold.fundamentals import (
    collect_fundamentals,
    fundamentals_direction,
    score_fundamentals,
)
from tradingagents.gold.models import GoldFundamentalPoint, GoldFundamentals, NewsItem
from tradingagents.gold.news import (
    classify_item,
    collect_news,
    freshness_filter,
    news_score,
    news_sentiment,
)

NOW = datetime(2026, 9, 9, 13, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------- freshness
def _item(hours_old, title="Fed cuts rates", ts=True):
    return NewsItem(
        headline=title, source="Reuters",
        timestamp=NOW - timedelta(hours=hours_old) if ts else None,
        summary="", age_hours=hours_old if ts else None,
    )


def test_freshness_filter_splits_by_age():
    fresh, stale = freshness_filter(
        [_item(2), _item(30), _item(100), _item(1)], max_age_hours=36, now=NOW
    )
    assert len(fresh) == 3 and len(stale) == 1


def test_freshness_filter_rejects_missing_timestamps():
    fresh, stale = freshness_filter([_item(0, ts=False)], 36, now=NOW)
    assert fresh == [] and len(stale) == 1


def test_freshness_filter_rejects_future_timestamps():
    future = NewsItem(headline="from the future", source="x",
                      timestamp=NOW + timedelta(hours=5))
    fresh, stale = freshness_filter([future], 36, now=NOW)
    assert fresh == [] and len(stale) == 1


def test_old_news_cannot_masquerade_as_current():
    week_old = _item(24 * 7, title="Fed cuts rates (last week)")
    fresh, stale = freshness_filter([week_old], 36, now=NOW)
    assert stale == [week_old]


# ----------------------------------------------------------------- classify
def test_classify_item_dovish_headline_is_bullish_high_impact():
    item = classify_item(NewsItem(headline="Fed signals rate cut"))
    assert item.sentiment > 0.3
    assert item.gold_impact == "HIGH"


def test_classify_item_hawkish_headline_is_bearish():
    item = classify_item(NewsItem(headline="Fed officials signal rate hike path"))
    assert item.sentiment < -0.3


def test_classify_item_central_bank_buying_is_bullish():
    item = classify_item(NewsItem(
        headline="Poland's central bank bought 20 tonnes of gold in August"))
    assert item.sentiment > 0.3


def test_classify_item_neutral_headline_low_impact():
    item = classify_item(NewsItem(headline="Gold mining output steady in Q2"))
    assert abs(item.sentiment) < 0.3
    assert item.gold_impact == "LOW"


def test_classify_confidence_requires_timestamp_and_source():
    with_ts = classify_item(_item(2))
    no_ts = classify_item(NewsItem(headline="Fed cuts rates", source="UNKNOWN",
                                   timestamp=None))
    assert with_ts.confidence > no_ts.confidence


# ------------------------------------------------------------------- scores
def test_news_score_empty_is_zero():
    assert news_score([]) == 0.0


def test_news_score_saturates():
    items = [classify_item(_item(2, title="Fed dovish pivot fuels gold rally"))
             for _ in range(12)]
    assert news_score(items) == 1.0


def test_news_sentiment_weighted_average():
    items = [
        NewsItem(headline="a", sentiment=0.6, confidence=1.0),
        NewsItem(headline="b", sentiment=-0.2, confidence=0.5),
    ]
    assert news_sentiment(items) == pytest.approx((0.6 - 0.1) / 1.5, abs=1e-3)


# ----------------------------------------------------------------- collect
def test_collect_news_with_injected_fetcher():
    def fetcher(query, limit):
        return [{
            "title": f"Fed dovish on {query[:10]} #{i}",
            "publisher": "TestWire",
            "pub_date": (NOW - timedelta(hours=3)).isoformat(),
            "summary": "rates",
        } for i in range(2)]

    fresh, stale = collect_news(queries=["gold fed"], limit=5,
                                max_age_hours=36, now=NOW, fetcher=fetcher)
    assert len(fresh) == 2
    assert all(i.source == "TestWire" for i in fresh)
    assert all(i.sentiment > 0 for i in fresh)


def test_collect_news_deduplicates_titles():
    def fetcher(query, limit):
        return [{"title": "Same headline", "publisher": "A",
                 "pub_date": NOW.isoformat(), "summary": ""}] * 3

    fresh, _ = collect_news(queries=["q1", "q2"], now=NOW, fetcher=fetcher)
    assert len(fresh) == 1


def test_collect_news_fetcher_failure_degrades_to_empty():
    def boom(query, limit):
        raise RuntimeError("network down")

    fresh, stale = collect_news(queries=["q"], now=NOW, fetcher=boom)
    assert fresh == [] and stale == []


def test_collect_news_unparseable_timestamp_goes_to_stale():
    def fetcher(query, limit):
        return [{"title": "weird date", "publisher": "A",
                 "pub_date": "not-a-date", "summary": ""}]

    fresh, stale = collect_news(now=NOW, fetcher=fetcher)
    assert fresh == [] and len(stale) == 1


def test_collect_news_unix_timestamps_supported():
    def fetcher(query, limit):
        return [{"title": "unix ts", "publisher": "A",
                 "pub_date": (NOW - timedelta(hours=1)).timestamp(),
                 "summary": ""}]

    fresh, _ = collect_news(now=NOW, fetcher=fetcher)
    assert len(fresh) == 1


# ------------------------------------------------------------- fundamentals
def test_collect_fundamentals_with_fetcher():
    def fetcher(alias, curr_date, look_back_days):
        return [("2026-09-08", 2.5)]  # fresh: ~29h old

    f = collect_fundamentals("2026-09-09", now=NOW, fetcher=fetcher)
    assert len(f.points) == 10
    real = f.get("real_yield_10y")
    assert real is not None and real.value == 2.5
    assert real.source == "FRED"
    assert real.timestamp is not None
    assert real.freshness_hours is not None and real.freshness_hours > 0


def test_collect_fundamentals_failure_is_unknown_never_fabricated():
    def boom(alias, curr_date, look_back_days):
        raise RuntimeError("FRED down")

    f = collect_fundamentals("2026-09-09", now=NOW, fetcher=boom)
    assert all(not p.available for p in f.points)
    assert all(p.value is None for p in f.points)


def test_collect_fundamentals_partial_failure_only_affects_that_series():
    def fetcher(alias, curr_date, look_back_days):
        if alias == "real_yield_10y":
            raise RuntimeError("series gone")
        return [("2026-09-08", 1.0)]

    f = collect_fundamentals("2026-09-09", now=NOW, fetcher=fetcher)
    assert f.get("real_yield_10y") is not None and not f.get("real_yield_10y").available
    assert f.get("dollar_index") is not None and f.get("dollar_index").available


def test_stale_fundamentals_marked():
    def fetcher(alias, curr_date, look_back_days):
        return [("2020-01-01", 1.0)]  # ancient

    f = collect_fundamentals("2026-09-09", now=NOW, fetcher=fetcher)
    pt = f.get("dollar_index")
    assert "STALE" in pt.source


def test_score_fundamentals_rewards_availability():
    full = GoldFundamentals(points=[
        GoldFundamentalPoint(name=f"p{i}", value=1.0, source="FRED",
                             timestamp=NOW, freshness_hours=1.0)
        for i in range(10)
    ])
    empty = GoldFundamentals(points=[
        GoldFundamentalPoint(name=f"p{i}", value=None, source="UNKNOWN")
        for i in range(10)
    ])
    assert score_fundamentals(full) == 1.0
    assert score_fundamentals(empty) == 0.0


def test_fundamentals_direction_from_real_yields_and_dollar():
    def funda(real, dxy):
        return GoldFundamentals(points=[
            GoldFundamentalPoint(name="real_yield_10y", value=real),
            GoldFundamentalPoint(name="dollar_index", value=dxy),
        ])

    from tradingagents.gold.models import Bias

    assert fundamentals_direction(funda(0.0, 100.0)) == Bias.BULLISH
    assert fundamentals_direction(funda(2.5, 125.0)) == Bias.BEARISH
    assert fundamentals_direction(funda(1.0, 110.0)) == Bias.NEUTRAL
    assert fundamentals_direction(GoldFundamentals()) == Bias.NEUTRAL


def test_fundamental_point_display_unknown():
    pt = GoldFundamentalPoint(name="cpi_yoy")
    assert pt.display() == "cpi_yoy: UNKNOWN"
    pt2 = GoldFundamentalPoint(name="dollar_index", value=100.12345, unit="index",
                               source="FRED", timestamp=NOW)
    assert "100.1235" in pt2.display() or "100.123" in pt2.display()
    assert "FRED" in pt2.display()
