"""
backend/strategies/strategy_trend/params.py

TrendBreakout_v1 — Donchian channel breakout with a chandelier trailing stop.

Defaults are the measured ones: a 20-day channel, a 2.0 x daily-ATR initial
stop, and a 1.5 x daily-ATR trail anchored on the best close of the last 100
bars. Deriv M15, 2021-10 -> 2026-09, 10 markets, 876 trades: +0.063R per trade,
40.0% win rate, payoff 1.86, t +2.17, +32.6% on $10,000 at 0.5% risk with a 6.3%
maximum drawdown. Positive on 9 of the 10 (see Implementation/
STRATEGIES-SHIPPED-2026-09-25.md for the per-market table and the caveats).

The TARGET is not a parameter here, the same as ORBParams: the order's
take-profit is the slot's resolved `tp1_rr`. This strategy has no target at all
in its measured form — it leaves on the trail — so `strategy_defaults.py` sets
`tp1_rr` far enough away that it never fills. Putting a target field here would
be a setting the order never reads.
"""

from dataclasses import dataclass


@dataclass
class TrendBreakoutParams:
    channel_days: int = 20
    """Length of the Donchian channel, in DAYS of bars (20 days of M15 bars is
    1,920 of them). A close beyond the previous channel is the signal; the
    current bar is excluded from the channel it has to beat. 10/20/40 were
    swept — 20 is the Turtle-lineage value and measured best."""

    stop_atr_multiple: float = 2.0
    """Initial stop, in daily ATR. Wide on purpose: a trend entry is early by
    construction and a tight stop turns the winners into losers before the move
    starts. This also sets R, so it sets the position size."""

    trail_atr_multiple: float = 1.5
    """Chandelier trailing stop, in daily ATR back from the best close since
    entry. This is the ONLY exit in the measured rule — 40% of trades win and
    the top five are a third of the profit, which is only possible with no
    profit target in the way."""

    trail_lookback_bars: int = 100
    """Bars the trail's best-close anchor looks back over (never past the entry
    bar). 0 means "every bar since entry", which cannot be reproduced live
    because live hands the exit a fixed-length fetch — see
    strategies/core/trail.py. 100, 300 and unbounded measured the same."""

    atr_days: int = 14
    """Days in the daily ATR. Both the stop and the trail are multiples of it."""

    side: str = "both"
    """"both", "long" or "short". Both sides were positive; longs carried more."""

    vol_filter: str = "off"
    """Trade only in a volatility regime, measured as this instrument's own
    20-day realised volatility over its 60-day (strategies/core/vol_regime.py):

      "off"        every signal, the measured baseline
      "expanding"  only when the ratio is above `vol_expanding_above`
      "quiet"      only when it is below `vol_quiet_below`

    "expanding" is the one with evidence. Chosen on 2021-10 -> 2024-09 and
    scored unchanged after, on ten markets:

        expanding   +0.216R in-sample (n 66)  ->  +0.232R out (n 56), PF 2.89
        unfiltered  +0.102R           (n 444) ->  +0.023R     (n 432)

    Almost no decay, the bucket ordering held, and the mechanism is plain — a
    breakout is a bet on continuation, and continuation is likelier when the
    market is speeding up. It is OFF by default because it rests on 56
    out-of-sample trades (t +1.79) and cuts trade count by about 85%. Turning it
    on is the single biggest improvement measured for this strategy."""

    vol_short_days: int = 20
    """Days in the fast realised-volatility window."""

    vol_long_days: int = 60
    """Days in the slow one. Also sets how much history the filter needs, so
    raising it widens WINDOW_BARS and slows every run."""

    vol_expanding_above: float = 1.15
    """`vol_filter="expanding"` needs the ratio at or above this. 1.15 is the
    tested boundary; it was not tuned per instrument, on purpose."""

    vol_quiet_below: float = 0.85
    """`vol_filter="quiet"` needs the ratio at or below this."""
