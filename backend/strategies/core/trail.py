"""
backend/strategies/core/trail.py

The chandelier trailing stop, as a strategy-owned exit.

WHY THE STRATEGY OWNS IT AND NOT `trailing_manager`
--------------------------------------------------
RiskParams' ATR_TRAIL trails a multiple of the STRATEGY TIMEFRAME's ATR from the
extreme PRICE since entry. The trend research trails a multiple of the DAILY ATR
from the extreme CLOSE. On an M15 strategy those are two different stops — the
first is ~15 minutes of range, the second a day and a half of it — and only the
second is the one that was measured. So the rule travels with the strategy, via
`on_position_bar` -> `TradeAction(action="MODIFY_SL")`, which the single-symbol
backtester, the portfolio backtester and live (position_manager) all apply.

WHY THE LOOKBACK IS BOUNDED
---------------------------
The classic Chandelier Exit is `highest high of the last N bars - k x ATR`, and
the research's "highest close since entry" is the same thing with N unbounded.
Only a bounded N can be recomputed identically on both paths: the backtester
hands `on_position_bar` a fixed-length slice and live hands it a fixed-length
fetch, so an unbounded rule would silently become "since entry" in one place and
"as far back as the window reaches" in the other. Measured on 10 markets over
five years, N = 100, N = 300 and unbounded agree to within 0.001R per trade
(scripts/run_app_form_check.py --sweep), because the level ratchets: once it has
risen it never comes back down, so how far back the anchor can see stops
mattering the moment a new extreme prints.

RATCHETING IS NOT DONE HERE
---------------------------
This returns the raw level. Both backtesters and `position_manager` already
refuse a `MODIFY_SL` that would LOOSEN a stop, which is exactly the ratchet, and
also what keeps the initial stop as a floor. Doing it twice would mean carrying
state that live cannot carry.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from backend.strategies.core.daily_atr import epoch_seconds


def to_epoch(value: Any) -> int | None:
    """A position's `entry_time` as UTC epoch seconds, whatever shape it arrives in."""
    if value is None:
        return None
    if isinstance(value, (int, float, np.integer, np.floating)):
        return int(value)
    try:
        return int(pd.Timestamp(value).timestamp())   # naive values are UTC
    except (TypeError, ValueError):
        return None


def chandelier_level(candles: pd.DataFrame, entry_time: Any, direction: str,
                     atr: float, multiple: float, lookback_bars: int) -> float | None:
    """`extreme close - multiple x atr` (BUY) or `extreme close + ...` (SELL).

    The extreme is taken over the last `lookback_bars` closed bars, never
    reaching back before the entry bar — an anchor from before the trade would
    put the stop where the position never was.
    """
    if candles is None or len(candles) < 2 or not atr or atr <= 0 or multiple <= 0:
        return None
    close = candles["close"].to_numpy(dtype=float)
    start = max(0, len(close) - int(lookback_bars)) if lookback_bars > 0 else 0

    entry = to_epoch(entry_time)
    if entry is not None:
        t = epoch_seconds(candles)
        step = int(np.diff(t[-6:]).min()) if len(t) > 6 else 0
        # a live fill lands seconds into its bar, so snap the entry to its bar
        entry_bar = entry - entry % step if step > 0 else entry
        first = int(np.searchsorted(t, entry_bar, side="left"))
        if first >= len(close):
            return None
        start = max(start, first)

    window = close[start:]
    if not len(window):
        return None
    if str(direction).upper().startswith("B"):
        return float(window.max()) - multiple * atr
    return float(window.min()) + multiple * atr
