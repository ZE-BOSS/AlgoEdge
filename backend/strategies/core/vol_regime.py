"""
backend/strategies/core/vol_regime.py

Is this instrument's volatility expanding or dying, relative to its own norm?

WHY A RATIO AND NOT VIX
-----------------------
The regime work on 2026-09-28 started with VIX, which is the right measure for
US equities and useless for gold, BTC or FX — and worse, it is a daily FRED
series, so a live strategy could not read today's value in time to act on it.

The ratio of SHORT-window realised volatility to LONG-window realised
volatility is self-normalising, computable from the bars the strategy already
holds, and asks the same question a volatility tercile asks: is this market
quiet or loud relative to its own recent normal. It needs no external feed and
works on every instrument.

WHAT IT MEASURED
----------------
`scripts/run_vix_regime_filter.py`, TrendBreakout_v1 on ten markets, bucket
chosen on 2021-10 → 2024-09 and scored unchanged after:

    bucket            in-sample            out-of-sample
    rv20/rv60 > 1.15  +0.216R (n 66)       +0.232R (n 56)   PF 1.92 -> 2.89
    0.85 .. 1.15      +0.078R (n 180)      -0.054R (n 199)
    < 0.85            +0.099R (n 171)      +0.043R (n 177)
    unfiltered        +0.102R (n 444)      +0.023R (n 432)

Expanding volatility was the best bucket in BOTH windows, with almost no decay
(+0.216 -> +0.232) and the ordering preserved. The mechanism is not a mystery: a
Donchian breakout is a bet that a move continues, and a move is more likely to
continue when the market is speeding up than when it is winding down.

The honest limit is 56 out-of-sample trades (t +1.79) and an 85% cut in trade
count. It is offered, not imposed — every strategy that uses this ships with the
filter OFF.

Two things deliberately NOT done:
  * "avoid the middle bucket" is not offered, even though mid is the only
    significantly negative cell out of sample (-0.054R, and -0.162R on the
    tercile version at t -3.56). In-sample mid was +0.078R, so nobody could have
    chosen to avoid it in advance, and a filter chosen with hindsight is not a
    filter.
  * No per-instrument thresholds. 0.85/1.15 applies to everything; tuning them
    per market on this sample is how 56 trades get talked into anything.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from backend.strategies.core.daily_atr import DAY, epoch_seconds

MODE_OFF = "off"
MODE_EXPANDING = "expanding"
MODE_QUIET = "quiet"


def daily_closes(candles: pd.DataFrame) -> np.ndarray:
    """One close per UTC day, from an intraday window."""
    t = epoch_seconds(candles)
    if len(t) < 2:
        return np.empty(0)
    d = t // DAY
    starts = np.r_[0, np.flatnonzero(np.diff(d)) + 1]
    ends = np.r_[starts[1:] - 1, len(t) - 1]
    return candles["close"].to_numpy(dtype=float)[ends]


def vol_ratio(candles: pd.DataFrame, short_days: int = 20,
              long_days: int = 60) -> float | None:
    """short-window realised vol / long-window realised vol, or None.

    Both windows END AT THE PREVIOUS DAY. The day in progress is excluded, so a
    bar cannot help decide its own regime — the same rule the daily ATR follows,
    and for the same reason.

    None when the window does not hold `long_days` + 2 complete days, because a
    ratio computed off a short history is a different number from the one that
    was measured, and silently trading it would be worse than not trading.
    """
    closes = daily_closes(candles)
    if len(closes) < long_days + 3:
        return None
    ret = np.diff(closes) / closes[:-1]
    ret = ret[:-1]                          # drop the day in progress
    if len(ret) < long_days:
        return None
    short_v = float(np.std(ret[-short_days:], ddof=1)) if short_days > 1 else 0.0
    long_v = float(np.std(ret[-long_days:], ddof=1))
    if not np.isfinite(long_v) or long_v <= 0:
        return None
    return short_v / long_v


class VolRegimeGate:
    """Mixin giving a strategy `self.vol_regime_ok(candles)`.

    The strategy's params supply `vol_filter` ("off" / "expanding" / "quiet"),
    `vol_short_days`, `vol_long_days`, `vol_expanding_above` and
    `vol_quiet_below`. A strategy without those fields gets the defaults, which
    leave the filter off.
    """

    def vol_regime_bars_needed(self, bars_per_day: int) -> int:
        """Bars of history the filter needs, or 0 when it is switched off.

        Declared separately so a strategy's WINDOW_BARS only pays for the longer
        window when the filter is actually on — leaving it off costs nothing.
        """
        p = getattr(self, "params", None)
        if str(getattr(p, "vol_filter", MODE_OFF)).lower() == MODE_OFF:
            return 0
        return (int(getattr(p, "vol_long_days", 60)) + 3) * int(bars_per_day)

    def vol_regime_ok(self, candles: pd.DataFrame) -> tuple[bool, str]:
        """(allowed, why). True with an empty reason when the filter is off."""
        p = getattr(self, "params", None)
        mode = str(getattr(p, "vol_filter", MODE_OFF)).lower()
        if mode == MODE_OFF:
            return True, ""
        ratio = vol_ratio(candles,
                          int(getattr(p, "vol_short_days", 20)),
                          int(getattr(p, "vol_long_days", 60)))
        if ratio is None:
            return False, "not enough history to judge the volatility regime"
        if mode == MODE_EXPANDING:
            floor = float(getattr(p, "vol_expanding_above", 1.15))
            return ratio >= floor, (
                f"volatility ratio {ratio:.2f} is below {floor:g} — not expanding")
        ceiling = float(getattr(p, "vol_quiet_below", 0.85))
        return ratio <= ceiling, (
            f"volatility ratio {ratio:.2f} is above {ceiling:g} — not quiet")
