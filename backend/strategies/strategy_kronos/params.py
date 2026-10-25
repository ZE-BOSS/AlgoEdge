"""
backend/strategies/strategy_kronos/params.py

KRONOS_v1 - trade Kronos-mini's direction when its sampled paths agree.

Measured (research/kronos/FINDINGS-2026-10-19.md, section 6b), SPX500 H1,
FundedNext, Feb 2025 -> Oct 2026, app engine with spread and swap: 440 trades,
+0.152R, t 2.77, PF 1.39, positive in both halves, beat 30/30 random-direction
runs on the same bars. It was the best of 12 symbols tested and NDX100 was
negative, so it is a FORWARD-TEST candidate, not a proven edge.
"""

from dataclasses import dataclass


@dataclass
class KronosStrategyParams:
    timeframe: str = "H1"
    """The bars Kronos forecasts and the strategy decides on. Measured on H1."""

    min_agreement: float = 0.8
    """Share of the 5 sampled paths that must agree on direction: 0.8 = 4 of 5.
    3 of 5 (0.6) was measured too and has no edge."""

    hold_bars: int = 10
    """Close after this many bars. The forecast horizon is 12; 10 keeps one
    trade from overlapping the next decision bar."""

    stop_atr_multiple: float = 2.0
    """Stop in ATR(14) of the decision timeframe. R is measured against it."""

    target_rr: float = 6.0
    """A far target: the trade is meant to end on time, not on target."""

    model: str = "Kronos-mini"
    lookback: int = 512
    pred_len: int = 12
    sample_count: int = 5
    """Must match the precomputed features (they are keyed by model_id)."""
