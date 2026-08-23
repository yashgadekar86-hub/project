"""
AI Trade Explainer.

Generates the "Why BUY?" / "Why SELL?" / "Why NOT TRADE?" explanations from
STRUCTURED signal data. If an LLM API key is configured, it uses an LLM to
summarize; otherwise it builds a deterministic, data-grounded explanation
without inventing any indicators or prices.

We never ask the LLM for prices, SL, TP, or direction — only for prose.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional


def _deterministic_explanation(signal: Dict[str, Any]) -> Dict[str, List[str]]:
    """Build an explanation purely from structured fields — no LLM required."""
    why: List[str] = []
    why_not: List[str] = []

    decision = signal.get("decision", "NO_TRADE")
    regime = signal.get("market_regime", "UNKNOWN")
    scores = signal.get("scores", {})
    tf_align = signal.get("timeframe_alignment", {})
    reasons = signal.get("reasoning", [])
    warnings = signal.get("warnings", [])
    blocks = signal.get("block_reasons", [])
    rr = signal.get("risk_reward")
    conf = signal.get("confidence", 0)

    if decision in ("BUY", "SELL"):
        why.append(f"Decision: {decision} with {conf}% confidence.")
        why.append(f"Market regime: {regime}.")
        if scores:
            why.append(
                f"Scores — Trend {scores.get('trend',0)}/100, "
                f"Structure {scores.get('structure',0)}/100, "
                f"Momentum {scores.get('momentum',0)}/100, "
                f"Entry {scores.get('entry',0)}/100, "
                f"R:R {scores.get('risk_reward',0)}/100."
            )
        if tf_align:
            why.append("Timeframe alignment: " + ", ".join(f"{tf}={b}" for tf, b in tf_align.items()))
        for r in reasons[:5]:
            why.append(f"• {r}")
        if rr:
            why.append(f"Risk/Reward = 1:{rr}")
        for w in warnings:
            why_not.append(f"Warning: {w}")
    else:
        why_not.append("NO TRADE decision — conditions do not meet the bar.")
        if blocks:
            why_not.append("Block reasons: " + ", ".join(blocks))
        for w in warnings:
            why_not.append(f"• {w}")
        if conf:
            why_not.append(f"Confidence in NO_TRADE assessment: {conf}%.")

    return {"why": why, "why_not": why_not}


def explain_signal(signal: Dict[str, Any]) -> Dict[str, List[str]]:
    """
    Return {'why': [...], 'why_not': [...]} explanation.

    If $OPENAI_API_KEY / $ANTHROPIC_API_KEY is set we _could_ call an LLM here
    in the future, but the deterministic version is always the fallback and
    always safe because it is derived from structured outputs.
    """
    base = _deterministic_explanation(signal)

    # NOTE: LLM integration is deliberately opt-in and only summarizes.
    # It never determines direction, prices, SL, TP, or lot size.
    llm_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
    if not llm_key:
        return base

    try:
        return _llm_explain(signal, base)
    except Exception:
        return base


def _llm_explain(signal: Dict[str, Any], base: Dict[str, List[str]]) -> Dict[str, List[str]]:
    """
    Optional LLM summarization. Intentionally minimal — no network calls are
    made unless the user explicitly configures a key AND opts in. The
    deterministic explanation is always returned on any failure.
    """
    # Placeholder for future optional integration.
    return base
