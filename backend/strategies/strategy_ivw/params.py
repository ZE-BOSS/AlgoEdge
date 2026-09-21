"""
backend/strategies/strategy_ivw/params.py

IVW_v1 — "Implied Volatility Walls" (OmegaTools, TradingView, Oct 2024), rebuilt from
its published description and traded the way the 2026-09-19 study found it pays:
as a BREAKOUT through the wall, not a fade off it.

Defaults are rule B of that study (implementation/IVW-STUDY-2026-09-19.md):
90th-percentile wall, low cumulative-volatility regime, liquidation bubble on the
breakout bar. Out of sample (2020-2026, 16 markets): +0.15R per trade, positive on
10 of 10 markets with enough trades.
"""

from dataclasses import dataclass


@dataclass
class IVWParams:
    wall_percentile: float = 90.0
    """The wall sits at this percentile of the last `lookback_days` sessions' maximum
    excursion from their open (up for the upper wall, down for the lower). The
    indicator's "chosen percentile". 70-90 tested; 90 had the best drawdown."""

    lookback_days: int = 60
    """Completed daily sessions the excursion percentile is taken over."""

    regime_filter: str = "low"
    """Cumulative-volatility regime a breakout must start in: 20-session summed
    range, ranked against its own last 250 sessions — "low" (bottom third),
    "mid", "high", or "any". A breakout out of a QUIET stretch is the one that
    carried; out of an already-volatile one it failed."""

    require_bubble: bool = True
    """The breakout bar must be a "liquidation bubble" (OmegaTools' open-source
    logic): its extreme at least `bubble_price_sigma` standard deviations beyond an
    inverse-volume-weighted equilibrium of the previous `bubble_lookback` bars, AND
    its volume at least `bubble_volume_sigma` deviations above theirs."""

    bubble_lookback: int = 50
    bubble_price_sigma: float = 2.0
    bubble_volume_sigma: float = 2.0

    forecast_filter: str = "any"
    """"contracting" trades only when a HAR range forecast (fitted on the previous
    500 sessions) expects today's range below 0.9x its recent normal — the
    indicator's machine-learning "Exp. Vol" step, replaced by the benchmark the
    volatility literature finds ML rarely beats. "any" = off (rule B)."""

    stop_width_frac: float = 0.5
    """Stop = this fraction of the wall's width (wall minus the session open) back
    from the entry. The target is one full width, so the default is 1:2."""

    side: str = "both"
    """"both", "long" or "short"."""

    close_at_day_end: bool = True
    """Flat at the end of the broker's day — every measured trade was intraday."""
