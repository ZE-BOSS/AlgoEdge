"""
backend/strategies/strategy_fib/params.py

FibTrend_v1 — buy (sell) the pullback to the 0.5 Fibonacci level of a confirmed
H4 swing leg, only in the direction of the 50-day trend, with no target: the
position leaves on a chandelier trailing stop.

Research: research/fib/FIB-USES-2026-10-25.md (variant G). The user's own fixed
0.5 / 0.786 / fib-0 rule was break-even on 20 markets x 18 variants; Fibonacci
only paid as the ENTRY of a trend system whose exit is a trail. Against 20
sign-flip surrogates per market (2023-10 -> 2026-10, H4):

    synthetics (12)   +0.183R/trade, +0.202R above surrogate, p < 0.05,
                      positive in 7 of 8 calendar years since 2019
    real CFDs (8)     +0.142R/trade, +0.166R above surrogate, p 0.20 (not significant)

In the app itself (scripts/run_app_backtest.py: costs, swap, next-open fills,
$10k at 1% compounded, 2023-10 -> 2026-10) all 20 markets together made 904
trades at +0.15R, $10,000 -> $26,898, max drawdown 27.6%; the last 9 months
+21.3%. Per-market tables: research/fib/FIB-APP-2026-10-25.md.

On real CFDs the same trend + trail entered AT ONCE, without waiting for the
pullback (entry_mode="immediate", stop_level=0.5), measured better: +0.189R,
p 0.05, carried by XAUUSD, XAGUSD and BTCUSD. Strong trends never come back to
0.5, so waiting for the fib there selects the weak ones.

The TARGET is not a parameter (see TrendBreakoutParams): the order's take-profit
is the slot's resolved `tp1_rr`, a far placeholder in strategy_defaults.
"""

from dataclasses import dataclass
from typing import Literal


@dataclass
class FibTrendParams:
    entry_mode: Literal["fib", "immediate"] = "fib"
    """"fib": enter on the first M15 bar that trades back to `entry_level` after
    the leg is confirmed (the measured synthetic form). "immediate": enter at the
    first M15 open after the leg is confirmed, no pullback (the measured real-CFD
    form, measured with stop_level 0.5 and swing_bars 3; strategy_defaults sets
    all three for the 8 real CFDs tested)."""

    swing_bars: int = 5
    """Fractal half-width, in H4 bars: a swing high needs `swing_bars` lower highs
    on each side, so a swing is only KNOWN `swing_bars` H4 bars (20 hours at 5)
    after it prints. That delay is what keeps the fib honest — it is never drawn
    on a swing that has not finished. 3 measured worse (smaller, noisier legs)."""

    min_leg_atr: float = 3.0
    """A leg must span at least this many H4 ATRs to count as a "clear trend"."""

    entry_level: float = 0.5
    """Fib level of the entry (1 = start of the leg, 0 = its end)."""

    stop_level: float = 0.786
    """Fib level of the initial stop. This sets R, and so the position size."""

    order_expiry_bars: int = 30
    """H4 bars (5 days) the setup stays live after the leg is confirmed. A setup
    also dies the moment price trades beyond the leg's end before the entry."""

    trend_filter: bool = True
    """Only legs in the direction of the trend: the confirmation bar's close on
    the trend side of the `trend_sma_bars` SMA, and that SMA rising (falling)
    over `trend_slope_bars`. Without it the result was noise."""

    trend_sma_bars: int = 300
    """H4 bars in the trend SMA (300 = 50 days)."""

    trend_slope_bars: int = 30
    """H4 bars over which the SMA's slope is measured."""

    trail_atr_multiple: float = 3.0
    """Chandelier trail: this many H4 ATRs back from the best H4 close since
    entry, ratcheting. The only exit in the measured rule."""

    atr_bars: int = 14
    """H4 bars in the ATR (Wilder) used by the leg size and the trail."""

    side: Literal["both", "long", "short"] = "both"
    """"both", "long" or "short". Longs carried most of the measured edge."""
