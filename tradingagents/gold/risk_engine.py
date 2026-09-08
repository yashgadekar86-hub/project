"""The deterministic trade engine (Phases 10, 11, 15-19, 21).

This is the FINAL authority. The LLM pipeline (analysts -> debate -> trader ->
portfolio manager rating) produces reasoning and a *candidate* direction; this
engine validates everything against hard, reproducible rules and is the only
component allowed to turn a candidate into BUY/SELL.

Guarantees (each covered by a dedicated test):

* The LLM/news analyst CANNOT override: news blackout, min RR, daily loss,
  max positions, spread, session, confidence floor, or volume limits.
* Missing data at any layer degrades to HOLD — the engine never fails open.
* A trade is only ever proposed with a valid structural stop and a target
  that honestly clears ``min_rr``; nothing is manufactured.
* Long-only by default; a SELL candidate without ``allow_short`` is HOLD.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone

from .config import GoldConfig
from .levels import build_trade_plan, validate_levels_against_broker
from .models import (
    AccountContext,
    Bias,
    BrokerSymbolSpec,
    CheckResult,
    ConfidenceBreakdown,
    FinalDecision,
    GoldFundamentals,
    MarketContext,
    MultiTimeframeView,
    NewsItem,
    Side,
    TradePlan,
)
from .news import news_score, news_sentiment
from .news_blackout import NewsBlackoutCalendar
from .sessions import check_session
from .sizing import calculate_volume

logger = logging.getLogger(__name__)

# Rating -> candidate translation (Phase 21), also used by the runner.
RATING_TO_CANDIDATE = {
    "Buy": "BUY",
    "Overweight": "BUY",
    "Hold": "HOLD",
    "Underweight": "SELL",
    "Sell": "SELL",
}


def candidate_from_rating(rating: str | None, review: bool = False) -> Side:
    """Deterministic rating translator: REVIEW/UNKNOWN -> HOLD, never a trade."""
    if review or rating is None:
        return "HOLD"
    return RATING_TO_CANDIDATE.get(rating, "HOLD")


def signal_id(symbol: str, side: Side, entry: float | None,
              sl: float | None, tp1: float | None, when: datetime) -> str:
    """Stable id for idempotency (Phase 26): same plan -> same id."""
    payload = (
        f"{symbol}|{side}|{entry or 0:.2f}|{sl or 0:.2f}|{tp1 or 0:.2f}"
        f"|{when.strftime('%Y%m%d%H')}"
    )
    return hashlib.sha1(payload.encode()).hexdigest()[:16]


class DeterministicRiskEngine:
    """Validates candidates; the ONLY path from rating to order."""

    def __init__(self, config: GoldConfig, now: datetime | None = None):
        self.config = config
        self.now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)

    # ------------------------------------------------------------------ eval
    def evaluate(
        self,
        rating: str | None,
        mtf: MultiTimeframeView,
        market: MarketContext,
        spec: BrokerSymbolSpec,
        account: AccountContext,
        fundamentals: GoldFundamentals | None = None,
        news_fresh: list[NewsItem] | None = None,
        calendar: NewsBlackoutCalendar | None = None,
        trader_stop: float | None = None,
        trader_take_profits: list[float] | None = None,
        review: bool = False,
    ) -> FinalDecision:
        cfg = self.config
        decision = FinalDecision(timestamp=self.now)
        checks = decision.checks
        news_fresh = news_fresh or []

        def hold(reason: str, veto: str | None = None) -> FinalDecision:
            decision.action = "HOLD"
            decision.reasons.append(reason)
            if veto:
                decision.vetoed_by.append(veto)
                checks.append(CheckResult(name=veto, passed=False, reason=reason))
            return decision

        # ---- 0. Deterministic rating translation ---------------------------
        from tradingagents.agents.utils.rating import is_review

        candidate = candidate_from_rating(rating, review or is_review(rating or ""))
        checks.append(CheckResult(
            name="rating_translation", passed=True,
            reason=f"rating={rating!r} -> candidate={candidate}", value=candidate,
        ))
        if candidate == "HOLD":
            return hold(f"Portfolio rating {rating!r} translates to NO TRADE.")

        # ---- 1. Long-only gate (Phase 23) ----------------------------------
        if candidate == "SELL" and not cfg.allow_short:
            return hold(
                "SELL candidate but ALLOW_SHORT=false (long-only mode); "
                "an existing long would be flattened by the executor, not shorted.",
                veto="long_only",
            )

        # ---- 2. Data availability ------------------------------------------
        if not mtf.usable:
            return hold(
                f"multi-timeframe view unusable: {mtf.description}",
                veto="data_unavailable",
            )
        checks.append(CheckResult(name="data_available", passed=True,
                                 reason=mtf.description))

        price = market.ask if candidate == "BUY" else market.bid
        if price is None:
            price = market.mid
        if price is None or price <= 0:
            return hold("no market price available", veto="data_unavailable")

        # ---- 3. News blackout (LLM cannot override) (Phase 7) --------------
        if not cfg.allow_trade_during_blackout:
            cal = calendar or NewsBlackoutCalendar.build(
                self.now, cfg.news_blackout_before_min, cfg.news_blackout_after_min
            )
            status = cal.check(self.now)
            if status.active:
                return hold(status.reason, veto="news_blackout")
            checks.append(CheckResult(
                name="news_blackout", passed=True, reason=status.reason,
                value=status.minutes_to_event,
            ))
        else:
            checks.append(CheckResult(
                name="news_blackout", passed=True,
                reason="OPERATOR OVERRIDE: ALLOW_TRADE_DURING_BLACKOUT=true",
            ))

        # ---- 4. Session filter (Phase 18) ----------------------------------
        session = check_session(self.now, cfg.allowed_sessions, cfg.timezone)
        checks.append(CheckResult(
            name="session", passed=session.allowed,
            reason=session.describe(), value=session.current_sessions,
        ))
        if not session.allowed:
            return hold(session.describe(), veto="session")

        # ---- 5. Spread filter (Phase 16) -----------------------------------
        spread_pts = market.spread_points
        spread_price = market.spread_price
        max_price = cfg.max_spread_price
        if spread_pts is None and spread_price is not None and spec.point > 0:
            spread_pts = spread_price / spec.point
        spread_ok = (
            spread_pts is not None
            and spread_pts <= cfg.max_spread_points
            and (max_price is None or (spread_price or 0) <= max_price)
        )
        checks.append(CheckResult(
            name="spread", passed=spread_ok,
            reason=f"spread={spread_pts}pts (max {cfg.max_spread_points}pts"
                   + (f", {max_price} price" if max_price else "") + ")",
            value=spread_pts,
        ))
        if not spread_ok:
            return hold(f"spread too high: {spread_pts} points",
                        veto="spread")

        # ---- 6. Volatility filter (Phase 17) -------------------------------
        wt = mtf.per_tf.get(self.config.working_timeframe)
        atr_pct = wt.structure.atr_pct if wt and wt.structure else None
        # Deterministic sanity bands on ATR% of price for gold.
        vol_ok = atr_pct is not None and 0.02 <= atr_pct <= 3.0
        checks.append(CheckResult(
            name="volatility", passed=vol_ok,
            reason=f"{self.config.working_timeframe} ATR="
                   f"{atr_pct if atr_pct is None else round(atr_pct, 3)}% of price",
            value=atr_pct,
        ))
        if not vol_ok:
            return hold("volatility outside tradeable band (dead or explosive market)",
                        veto="volatility")

        # ---- 7. Higher-timeframe alignment (Phase 3) -----------------------
        aligned = mtf.aligned_direction
        wanted = Bias.BULLISH if candidate == "BUY" else Bias.BEARISH
        align_ok = aligned == wanted
        checks.append(CheckResult(
            name="htf_alignment", passed=align_ok,
            reason=f"HTF direction={aligned.value}, candidate={candidate} "
                   f"(alignment score {mtf.alignment_score:.2f})",
            value=aligned.value,
        ))
        if not align_ok:
            return hold(
                f"candidate {candidate} conflicts with HTF direction {aligned.value}",
                veto="htf_alignment",
            )

        # ---- 8. Account risk limits (Phase 15) -----------------------------
        if account.equity <= 0:
            return hold("account equity unavailable/zero", veto="account")

        if account.realized_pnl_today < 0:
            daily_loss_pct = abs(account.realized_pnl_today) / account.equity * 100.0
            if daily_loss_pct >= cfg.max_daily_loss:
                return hold(
                    f"daily loss {daily_loss_pct:.2f}% >= MAX_DAILY_LOSS "
                    f"{cfg.max_daily_loss}%",
                    veto="daily_loss",
                )

        if account.trades_today >= cfg.max_trades_per_day:
            return hold(
                f"trades today {account.trades_today} >= MAX_TRADES_PER_DAY "
                f"{cfg.max_trades_per_day}", veto="max_trades_per_day",
            )

        if account.consecutive_losses >= cfg.max_consecutive_losses:
            return hold(
                f"{account.consecutive_losses} consecutive losses >= "
                f"MAX_CONSECUTIVE_LOSSES {cfg.max_consecutive_losses}",
                veto="consecutive_losses",
            )

        already_same_dir = (
            (candidate == "BUY" and account.open_direction == "BUY")
            or (candidate == "SELL" and account.open_direction == "SELL")
        )
        if already_same_dir or account.open_positions >= cfg.max_open_positions:
            return hold(
                f"position limit: open={account.open_positions} "
                f"(max {cfg.max_open_positions})"
                + (", already positioned this direction" if already_same_dir else ""),
                veto="position_limit",
            )
        checks.append(CheckResult(
            name="risk_limits", passed=True,
            reason=f"equity={account.equity:.2f} open={account.open_positions} "
                   f"trades_today={account.trades_today}",
        ))

        # ---- 9. Build the deterministic plan (Phases 11-13) ----------------
        plan = build_trade_plan(
            side=candidate,
            mtf=mtf,
            market=market,
            spec=spec,
            min_rr=cfg.min_rr,
            atr_sl_multiplier=cfg.atr_sl_multiplier,
            min_sl_atr_multiple=cfg.min_sl_atr_multiple,
            trader_stop=trader_stop,
            trader_take_profits=trader_take_profits,
            tp_levels=cfg.tp_levels,
            preferred_rr_max=cfg.preferred_rr_max,
            working_timeframe=cfg.working_timeframe,
        )
        if not plan.valid:
            return hold(f"trade plan invalid: {plan.invalid_reason}",
                        veto="plan_invalid")

        broker_problems = validate_levels_against_broker(plan, spec)
        if broker_problems:
            return hold("broker validation: " + "; ".join(broker_problems),
                        veto="broker_constraints")
        checks.append(CheckResult(
            name="levels", passed=True,
            reason=f"entry={plan.entry} sl={plan.stop_loss}({plan.stop_source}) "
                   f"tp1={plan.tp1}({plan.tp_source})",
        ))

        # ---- 10. RR gate (LLM cannot override) (Phase 11) ------------------
        if not self.rr_ok(plan):
            return hold(
                f"RR {plan.rr if plan.rr is None else round(plan.rr, 2)} < "
                f"MIN_RR {cfg.min_rr}",
                veto="min_rr",
            )
        checks.append(CheckResult(
            name="rr", passed=True,
            reason=f"RR={plan.rr:.2f} >= {cfg.min_rr}",
            value=plan.rr,
        ))

        # ---- 11. Position sizing (Phase 14) --------------------------------
        sizing = calculate_volume(plan, spec, account.equity, cfg.risk_per_trade)
        if not sizing.ok:
            return hold(f"sizing failed: {sizing.reason}", veto="sizing")
        checks.append(CheckResult(
            name="sizing", passed=True,
            reason=f"volume={plan.volume} risk={plan.risk_amount} "
                   f"(allowed {sizing.risk_amount:.2f})",
            value=plan.volume,
        ))

        # ---- 12. Confidence (Phase 19) --------------------------------------
        confidence = self.confidence(
            candidate=candidate, mtf=mtf, fundamentals=fundamentals,
            news_fresh=news_fresh, plan=plan,
        )
        decision.confidence = confidence
        checks.append(CheckResult(
            name="confidence", passed=confidence.total >= cfg.min_confidence,
            reason=f"total={confidence.total:.1f} (min {cfg.min_confidence})",
            value=confidence.total,
        ))
        if confidence.total < cfg.min_confidence:
            decision.vetoed_by.append("min_confidence")
            decision.reasons.append(
                f"confidence {confidence.total:.1f} < MIN_CONFIDENCE "
                f"{cfg.min_confidence}"
            )
            decision.action = "HOLD"
            return decision

        # ---- 13. Idempotency (Phase 26) -------------------------------------
        plan.signal_id = signal_id(
            cfg.symbol, candidate, plan.entry, plan.stop_loss, plan.tp1, self.now
        )
        if account.last_signal_id == plan.signal_id and account.last_signal_time:
            age_min = (self.now - account.last_signal_time).total_seconds() / 60.0
            if age_min <= cfg.dedupe_window_min:
                return hold(
                    f"duplicate signal {plan.signal_id} within "
                    f"{cfg.dedupe_window_min}min window",
                    veto="duplicate_signal",
                )
        checks.append(CheckResult(
            name="idempotency", passed=True, reason=f"signal_id={plan.signal_id}",
        ))

        decision.action = candidate
        decision.plan = plan
        decision.reasons.append(
            f"all deterministic checks passed; RR={plan.rr:.2f}, "
            f"confidence={confidence.total:.1f}, volume={plan.volume}"
        )
        return decision

    def rr_ok(self, plan: TradePlan) -> bool:
        """RR gate (Phase 11): the reward must honestly clear min_rr."""
        return plan.rr is not None and plan.rr >= self.config.min_rr

    # ------------------------------------------------------------- confidence
    def confidence(
        self,
        candidate: Side,
        mtf: MultiTimeframeView,
        fundamentals: GoldFundamentals | None,
        news_fresh: list[NewsItem],
        plan: TradePlan,
    ) -> ConfidenceBreakdown:
        """Deterministic weighted confidence (Phase 19).

        Components measure EVIDENCE QUALITY and AGREEMENT with the candidate —
        never the LLM's self-assessment. All components are normalized to
        their weight so the total is directly comparable to MIN_CONFIDENCE.
        """
        from .fundamentals import score_fundamentals

        w = self.config.weights
        wanted = 1.0 if candidate == "BUY" else -1.0

        # Technical: multi-timeframe alignment with the candidate.
        tech = mtf.alignment_score if mtf.usable else 0.0

        # Fundamental: availability + crude macro agreement (context only).
        if fundamentals is not None:
            avail = score_fundamentals(fundamentals)
            from .fundamentals import fundamentals_direction
            direction = fundamentals_direction(fundamentals)
            agree = (
                1.0 if (direction.value == "BULLISH" and wanted > 0)
                or (direction.value == "BEARISH" and wanted < 0)
                else 0.5 if direction.value == "NEUTRAL"
                else 0.15
            )
            funda = 0.6 * avail + 0.4 * agree
        else:
            funda = 0.0

        # News: evidence base + sentiment agreement.
        if news_fresh:
            base = news_score(news_fresh)
            senti = news_sentiment(news_fresh)
            agree = 1.0 if senti * wanted > 0.05 else (
                0.5 if abs(senti) <= 0.05 else 0.15
            )
            news = 0.6 * base + 0.4 * agree
        else:
            news = 0.0

        # Sentiment: derived from news sentiment agreement (no social feed
        # for gold; documented). No news at all -> half credit, not zero,
        # so missing sentiment alone cannot silently veto a trade.
        if news_fresh:
            senti = news_sentiment(news_fresh)
            sentiment = 1.0 if senti * wanted > 0.05 else (
                0.5 if abs(senti) <= 0.05 else 0.2
            )
        else:
            sentiment = 0.5

        # Structure: trend clarity + levels present on the working TF.
        snap = mtf.per_tf.get(self.config.working_timeframe)
        struct = 0.0
        if snap is not None and snap.usable and snap.structure is not None:
            st = snap.structure
            if st.trend != Bias.NEUTRAL:
                struct += 0.4
                if (st.trend.value == "BULLISH" and wanted > 0) or (
                    st.trend.value == "BEARISH" and wanted < 0
                ):
                    struct += 0.2
            if st.support:
                struct += 0.2
            if st.resistance:
                struct += 0.2
            if st.last_event != "UNKNOWN":
                struct += 0.1

        # RR quality: min_rr -> 60% of component, preferred -> 100%.
        if plan.rr is not None and plan.rr >= self.config.min_rr:
            span = max(self.config.preferred_rr_max - self.config.min_rr, 1e-9)
            rr = 0.6 + 0.4 * min(
                (plan.rr - self.config.min_rr) / span, 1.0
            )
        else:
            rr = 0.0

        scores = {
            "technical": round(tech * w["technical"], 1),
            "fundamental": round(funda * w["fundamental"], 1),
            "news": round(news * w["news"], 1),
            "sentiment": round(sentiment * w["sentiment"], 1),
            "structure": round(min(struct, 1.0) * w["structure"], 1),
            "rr": round(rr * w["rr"], 1),
        }
        return ConfidenceBreakdown(
            scores=scores, weights=dict(w), total=round(sum(scores.values()), 1),
        )
