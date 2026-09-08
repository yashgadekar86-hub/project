"""Phase 3 tests: the multi-timeframe decision hierarchy.

The spec's canonical example:

    D1 bullish, H4 bullish, H1 bullish, M15 bearish pullback, M5 bullish
    reversal  =>  "higher-timeframe bullish trend with bearish pullback and
    potential bullish entry confirmation"  — NOT "3 bullish + 1 bearish = BUY".

Proven here: higher timeframes dominate, a conflicting H1 cancels, and the
output is a hierarchy-aware description, never a naive vote count.
"""

from __future__ import annotations

from tests.gold_helpers import (
    breakout_frames,
    bullish_bearish_frames,
    candles_from_path,
    mtf_of,
    range_path,
    trend_path,
)
from tradingagents.gold.models import Bias
from tradingagents.gold.timeframes import (
    analyze_timeframe,
    describe,
)


def test_spec_example_bullish_with_bearish_pullback():
    frames = bullish_bearish_frames(bearish_m15=True)
    view = mtf_of(frames)
    assert view.macro_trend == Bias.BULLISH
    assert view.major_structure == Bias.BULLISH
    assert view.bias == Bias.BULLISH
    assert view.setup == Bias.BEARISH          # the pullback
    assert view.entry_confirmation == Bias.BULLISH
    # HTF dominates: still bullish overall (NOT a vote count that cancels).
    assert view.aligned_direction == Bias.BULLISH
    # The description states the hierarchy, not a tally.
    d = view.description.lower()
    assert "d1 macro trend: bullish" in d
    assert "m15 setup: bearish" in d


def test_conflicting_h1_cancels_direction():
    frames = bullish_bearish_frames(bearish_h1=True)
    view = mtf_of(frames)
    assert view.macro_trend == Bias.BULLISH
    assert view.major_structure == Bias.BULLISH
    assert view.bias == Bias.BEARISH
    # H1 is the trading bias: a bearish H1 vetoes the bullish HTF read.
    assert view.aligned_direction == Bias.NEUTRAL


def test_lower_timeframes_do_not_veto_htf():
    frames = bullish_bearish_frames(bearish_m15=True)
    view = mtf_of(frames)
    # M15 bearish alone must not neutralize the D1+H4+H1 bullish stack.
    assert view.aligned_direction == Bias.BULLISH


def test_alignment_score_rewards_agreement_and_penalizes_gaps():
    full = mtf_of(bullish_bearish_frames())
    partial = mtf_of({"D1": bullish_bearish_frames()["D1"],
                       "H1": bullish_bearish_frames()["H1"]})
    assert full.alignment_score > partial.alignment_score
    assert 0.0 <= partial.alignment_score <= 1.0


def test_view_unusable_without_d1_and_h1():
    frames = bullish_bearish_frames()
    del frames["D1"]
    view = mtf_of(frames)
    assert not view.usable
    frames2 = bullish_bearish_frames()
    del frames2["H1"]
    assert not mtf_of(frames2).usable
    assert mtf_of(bullish_bearish_frames()).usable


def test_analyze_timeframe_short_data_is_data_unavailable():
    df = candles_from_path(trend_path(20, seed=1))
    snap = analyze_timeframe("H1", df, min_candles=60)
    assert not snap.usable
    assert "DATA_UNAVAILABLE" in snap.reason


def test_analyze_timeframe_none_frame_is_data_unavailable():
    snap = analyze_timeframe("H1", None, min_candles=60)
    assert not snap.usable
    assert "DATA_UNAVAILABLE" in snap.reason


def test_neutral_range_yields_neutral_read():
    frames = breakout_frames()
    # replace D1 with a symmetric range that ENDS at the midline (105) so the
    # indicator read is neither stretched up nor down.
    d1_path = range_path(200, 100.0, 110.0) + [104.0, 104.5, 105.0, 105.5, 106.0]
    frames["D1"] = candles_from_path(d1_path, freq="1D")
    view = mtf_of(frames)
    assert view.macro_trend == Bias.NEUTRAL


def test_describe_labels_missing_timeframes():
    view = mtf_of({"D1": candles_from_path(trend_path(100, seed=2), freq="1D")})
    d = describe(view)
    assert "NO DATA" in d


def test_range_below_ema_reads_bearish_not_bullish():
    """A range ending below its EMA50 is a legitimate BEARISH bias read."""
    view = mtf_of(breakout_frames())
    assert view.usable
    assert view.per_tf["H1"].bias == Bias.BEARISH
    # H1 bearish cancels the bullish HTF read (trading-bias rule).
    assert view.aligned_direction == Bias.NEUTRAL
