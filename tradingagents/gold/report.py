"""Auditable analysis report rendering (Phase 31).

Every analysis run produces a markdown report stating WHERE each number came
from (DATA SOURCE), WHEN it was fetched (TIMESTAMP), and which values are
UNKNOWN — plus the full check log of the deterministic engine, so a human can
reconstruct exactly why the system traded or held.
"""

from __future__ import annotations

from datetime import datetime, timezone

from .models import (
    AccountContext,
    BrokerSymbolSpec,
    FinalDecision,
    GoldFundamentals,
    MarketContext,
    MultiTimeframeView,
    NewsItem,
)
from .sessions import SessionStatus


def _fmt_ts(ts: datetime | None) -> str:
    if ts is None:
        return "UNKNOWN"
    return ts.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def render_report(
    symbol: str,
    decision: FinalDecision,
    mtf: MultiTimeframeView,
    market: MarketContext,
    spec: BrokerSymbolSpec,
    account: AccountContext,
    fundamentals: GoldFundamentals | None,
    news_fresh: list[NewsItem],
    news_stale: list[NewsItem],
    session: SessionStatus | None,
    rating: str | None,
    bull_case: str = "",
    bear_case: str = "",
    trader_proposal: str = "",
    gc_reference_price: float | None = None,
    gc_reference_note: str = "",
) -> str:
    now = decision.timestamp or datetime.now(timezone.utc)
    lines: list[str] = []
    add = lines.append

    add(f"# Gold Analysis Report — {symbol}")
    add(f"\nGenerated: {_fmt_ts(now)}\n")

    # -- data sources --------------------------------------------------------
    add("## Data sources")
    add(f"- PRIMARY (trading): `{spec.symbol}` — broker symbol economics "
        f"(contract={spec.contract_size}, tick={spec.tick_size}, "
        f"vol {spec.volume_min}..{spec.volume_max} step {spec.volume_step}, "
        f"stops level {spec.stops_level_points} pts)")
    for tf, snap in sorted(mtf.per_tf.items()):
        prov = snap.provenance.label() if snap.provenance else "[UNKNOWN]"
        state = "OK" if snap.usable else snap.reason
        add(f"- {tf}: {snap.candles} candles {prov} — {state}")
    if gc_reference_price is not None:
        add(f"- REFERENCE: GC=F (COMEX futures) last={gc_reference_price} — "
            f"{gc_reference_note or 'research reference only, NOT the trading price'}")
    add("")

    # -- market / account ----------------------------------------------------
    add("## Market & account")
    add(f"- Broker bid/ask: {market.bid} / {market.ask} "
        f"(spread {market.spread_points} pts)")
    add(f"- Session: {session.describe() if session else 'UNKNOWN'}")
    add(f"- Equity: {account.equity:.2f} {account.currency} | "
        f"open positions: {account.open_positions} | "
        f"trades today: {account.trades_today} | "
        f"realized P/L today: {account.realized_pnl_today:.2f}")
    add("")

    # -- multi timeframe -----------------------------------------------------
    add("## Multi-timeframe read")
    add(f"`{mtf.description}`")
    add(f"- Aligned direction: **{mtf.aligned_direction.value}** "
        f"(score {mtf.alignment_score:.2f})")
    for tf in ("D1", "H4", "H1", "M15", "M5"):
        snap = mtf.per_tf.get(tf)
        if snap is None or not snap.usable:
            continue
        st = snap.structure
        ind = snap.indicators or {}
        add(f"\n### {tf} ({snap.provenance.label() if snap.provenance else ''})")
        add(f"- trend={st.trend.value} last_event={st.last_event}"
            f"({st.last_event_dir.value}) bias={snap.bias.value}")
        add(f"- close={ind.get('close')} ATR={_n(ind.get('atr'))} "
            f"RSI={_n(ind.get('rsi'))} MACD_hist={_n(ind.get('macd_hist'))} "
            f"EMA50={_n(ind.get('ema_50'))}")
        add(f"- support={[round(x, 2) for x in st.support]} "
            f"resistance={[round(x, 2) for x in st.resistance]}")
    add("")

    # -- fundamentals --------------------------------------------------------
    add("## Fundamentals (gold macro drivers)")
    if fundamentals is not None and fundamentals.points:
        for p in fundamentals.points:
            add(f"- {p.display()}"
                + (f" [freshness {p.freshness_hours:.0f}h]" if p.freshness_hours is not None else ""))
    else:
        add("- UNKNOWN (fundamental data unavailable)")
    add("")

    # -- news ----------------------------------------------------------------
    add("## News (fresh)")
    if news_fresh:
        for item in news_fresh[:10]:
            age = f", {item.age_hours:.0f}h old" if item.age_hours is not None else ""
            add(f"- [{_fmt_ts(item.timestamp)}{age}] ({item.source}) "
                f"{item.headline} — impact={item.gold_impact} "
                f"sentiment={item.sentiment:+.2f}")
    else:
        add("- UNKNOWN (no fresh news)")
    if news_stale:
        add(f"\n_({len(news_stale)} stale/undated items excluded by the "
            f"freshness filter)_")
    add("")

    # -- research layer ------------------------------------------------------
    if bull_case or bear_case or trader_proposal:
        add("## Research layer (LLM)")
        if bull_case:
            add(f"\n### Bull case\n> {bull_case[:1500]}")
        if bear_case:
            add(f"\n### Bear case\n> {bear_case[:1500]}")
        if trader_proposal:
            add(f"\n### Trader proposal\n> {trader_proposal[:1500]}")
        add(f"\nPortfolio rating: **{rating or 'UNKNOWN'}**")
        add("")

    # -- deterministic engine --------------------------------------------------
    add("## Deterministic risk engine")
    add(f"\n**{decision.summary()}**\n")
    for c in decision.checks:
        mark = "PASS" if c.passed else "FAIL"
        add(f"- [{mark}] {c.name}: {c.reason}")
    if decision.confidence is not None:
        add(f"\n```\n{decision.confidence.display()}\n```")
    if decision.plan is not None and decision.plan.valid:
        p = decision.plan
        tps = ", ".join(str(t) for t in p.take_profits)
        add(f"\n**Plan**: {p.side} entry={p.entry} SL={p.stop_loss} "
            f"({p.stop_source}) TP=[{tps}] ({p.tp_source}) RR={p.rr:.2f} "
            f"volume={p.volume} risk={p.risk_amount} signal_id={p.signal_id}")
    if decision.reasons:
        add("\n### Reasons")
        for r in decision.reasons:
            add(f"- {r}")
    add("\n---\n_Analysis for research purposes only. Not investment advice. "
        "Past performance does not guarantee future results._")
    return "\n".join(lines)


def _n(v) -> str:
    return "UNKNOWN" if v is None else f"{v:.4g}"
