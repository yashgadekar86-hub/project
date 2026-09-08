"""Gold news collection, freshness filtering, deterministic scoring (Phase 6).

Collects structured :class:`NewsItem`s (headline, source, timestamp, summary,
sentiment, gold impact, confidence), applies a hard freshness filter so stale
news can never masquerade as current, and computes a deterministic news
component for the confidence engine.

Sentiment here is keyword-based and conservative: it exists to *gate and
weight*, not to reason. The LLM news analyst does the qualitative work.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from .models import NewsItem

logger = logging.getLogger(__name__)

# Gold-relevant query set (matches gold_config.py's LLM queries).
GOLD_NEWS_QUERIES = [
    "gold price Federal Reserve interest rates real yields",
    "US dollar DXY inflation CPI Fed policy",
    "central bank gold buying reserves",
    "geopolitical risk safe haven gold demand",
    "gold ETF flows bullion market",
]

# Deterministic keyword sentiment. Each phrase maps to (sentiment, impact).
# Sentiment: +1 gold-positive, -1 gold-negative at face value.
_KEYWORDS: list[tuple[str, float, str]] = [
    (r"rate cut|cut rates|easing|dovish", +0.6, "HIGH"),
    (r"rate hike|raise rates|tightening|hawkish", -0.6, "HIGH"),
    (r"real yields? (fall|falling|drop|lower|decline)", +0.5, "MEDIUM"),
    (r"real yields? (rise|rising|climb|higher|surge)", -0.5, "MEDIUM"),
    (r"dollar (weaken|weakens|weakening|slips|falls)", +0.4, "MEDIUM"),
    (r"dollar (strengthen|strengthens|rallies|firms|rises)", -0.4, "MEDIUM"),
    (r"inflation (accelerat|jump|surge|hotter|rises)", +0.3, "HIGH"),
    (r"inflation (cool|cools|slows|eases|falls)", -0.3, "MEDIUM"),
    (r"central bank.{0,40}(buy|bought|purchas|add|added).{0,40}gold", +0.7, "HIGH"),
    (r"gold (demand|buying|inflow|rally|surge|climb)", +0.5, "MEDIUM"),
    (r"gold (outflow|selloff|sell-off|slump|plunge|drop)", -0.5, "MEDIUM"),
    (r"safe[- ]haven (demand|bid|flows?)", +0.5, "MEDIUM"),
    (r"risk[- ]on|risk appetite", -0.3, "LOW"),
    (r"geopolitical (tension|risk|escalat)|war|conflict|strike", +0.5, "MEDIUM"),
    (r"recession|slowdown|jobless claims rise", +0.4, "MEDIUM"),
]

_COMPILED = [(re.compile(p, re.I), s, i) for p, s, i in _KEYWORDS]


def _parse_pub_date(raw) -> datetime | None:
    if raw is None:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        pass
    try:
        return datetime.strptime(str(raw)[:19], "%Y-%m-%dT%H:%M:%S").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return None


def classify_item(item: NewsItem) -> NewsItem:
    """Attach deterministic sentiment / impact / confidence to a news item."""
    text = f"{item.headline} {item.summary}"
    sentiment = 0.0
    impacts: list[str] = []
    for pattern, score, impact in _COMPILED:
        if pattern.search(text):
            sentiment += score
            impacts.append(impact)
    # Clamp to [-1, 1]
    item.sentiment = max(-1.0, min(1.0, sentiment))
    if impacts:
        rank = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}
        item.gold_impact = max(impacts, key=lambda i: rank[i])
    else:
        item.gold_impact = "LOW"
    # Confidence grows with freshness + presence of a real timestamp/source.
    conf = 0.3
    if item.timestamp is not None:
        conf += 0.3
    if item.source and item.source != "UNKNOWN":
        conf += 0.2
    if item.gold_impact in ("HIGH", "MEDIUM"):
        conf += 0.2
    item.confidence = round(min(conf, 1.0), 3)
    return item


def freshness_filter(items: list[NewsItem], max_age_hours: float,
                      now: datetime | None = None) -> tuple[list[NewsItem], list[NewsItem]]:
    """Split items into (fresh, stale). Items with no timestamp are stale."""
    now = now or datetime.now(timezone.utc)
    fresh, stale = [], []
    for item in items:
        if item.timestamp is None:
            stale.append(item)
            continue
        ts = item.timestamp
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        age = (now - ts).total_seconds() / 3600.0
        # A timestamp in the future is invalid, not fresh.
        if age < -1.0:
            stale.append(item)
            continue
        item.age_hours = max(age, 0.0)
        (fresh if age <= max_age_hours else stale).append(item)
    return fresh, stale


def default_fetcher(query: str, limit: int) -> list[dict]:
    """Live fetch via yfinance Search (network). Injectable in tests."""
    import yfinance as yf

    search = yf.Search(query=query, news_count=limit, enable_fuzzy_query=True)
    out = []
    for article in search.news or []:
        if "content" in article:
            content = article["content"] or {}
            out.append({
                "title": content.get("title", ""),
                "publisher": (content.get("provider") or {}).get("displayName")
                             or article.get("publisher", "UNKNOWN"),
                "pub_date": content.get("pubDate"),
                "summary": content.get("summary", ""),
            })
        else:
            out.append({
                "title": article.get("title", ""),
                "publisher": article.get("publisher", "UNKNOWN"),
                "pub_date": article.get("providerPublishTime"),
                "summary": "",
            })
    return out


def collect_news(
    queries: list[str] | None = None,
    limit: int = 10,
    max_age_hours: float = 36.0,
    now: datetime | None = None,
    fetcher=None,
) -> tuple[list[NewsItem], list[NewsItem]]:
    """Collect + classify + freshness-filter gold news. Returns (fresh, stale)."""
    queries = queries or GOLD_NEWS_QUERIES
    fetcher = fetcher or default_fetcher
    now = now or datetime.now(timezone.utc)

    items: list[NewsItem] = []
    seen: set[str] = set()
    for query in queries:
        try:
            raw = fetcher(query, limit)
        except Exception as exc:  # noqa: BLE001 - news is best-effort
            logger.warning("news fetch failed for %r: %s", query, exc)
            continue
        for r in raw:
            title = (r.get("title") or "").strip()
            if not title or title in seen:
                continue
            seen.add(title)
            pub = r.get("pub_date")
            ts = _parse_pub_date(pub)
            if ts is None and isinstance(pub, (int, float)):
                # yfinance sometimes returns unix seconds
                try:
                    ts = datetime.fromtimestamp(float(pub), tz=timezone.utc)
                except (ValueError, OSError, OverflowError):
                    ts = None
            items.append(classify_item(NewsItem(
                headline=title,
                source=str(r.get("publisher") or "UNKNOWN"),
                timestamp=ts,
                summary=str(r.get("summary") or "")[:500],
            )))

    return freshness_filter(items, max_age_hours, now)


def news_score(items: list[NewsItem]) -> float:
    """Deterministic 0..1 availability score for the confidence engine.

    Rewards recent, timestamped, sourced, high-impact coverage. Direction is
    NOT scored here (that is the LLM's job); this grades the evidence base.
    """
    if not items:
        return 0.0
    total = 0.0
    for item in items:
        w = item.confidence
        if item.gold_impact == "HIGH":
            w *= 1.25
        total += w
    # 8 strong items saturate the score.
    return round(min(total / 8.0, 1.0), 4)


def news_sentiment(items: list[NewsItem]) -> float:
    """Weighted average sentiment in [-1, 1] (context metric only)."""
    if not items:
        return 0.0
    weight_sum = sum(i.confidence for i in items) or 1.0
    return round(sum(i.sentiment * i.confidence for i in items) / weight_sum, 4)
