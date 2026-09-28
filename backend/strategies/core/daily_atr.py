"""
backend/strategies/core/daily_atr.py

ATR on the DAILY scale, read off an intraday window.

WHY IT HAS TO BE THE DAILY ONE
------------------------------
The first version of the 2026-09-25 trend research put a 2x M5 ATR stop under a
multi-day hold. That is twice the average range of a FIVE MINUTE bar: it is hit
within minutes, so the measurement showed a 5-11% win rate with a huge
expectancy — a handful of survivors running for days behind a stop nobody could
have survived. `scripts/run_edge_screen2.atr_daily` was written to fix it, and
this module is the live-engine half of that same function, so a strategy's stop
and trail are the ones that were measured.

The rule, matching `atr_daily` exactly: group the window's bars into UTC days,
take each day's true range against the previous day's close, and average the
`days` COMPLETE days BEFORE the current one. The day in progress never
contributes to its own ATR, so there is no look-ahead, and every bar of a day
reads the same value.

CACHING
-------
The value is constant for a whole day, so it is cached per (symbol, day). That
matters because `on_position_bar` is called once per bar per open position and
would otherwise regroup ~5,000 bars every five minutes; live it is called once
per cycle on a fresh engine, where the cache is simply never hit twice. The
cache key is the window's own extent -- its first day, its last day and its
length -- so two windows that would group into different days never share an
entry. Keying on the last day alone is NOT enough: within one day the window
slides by up to a day's worth of bars, which can drop a whole day group off the
back and change the average.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

DAY = 86400


def epoch_seconds(candles: pd.DataFrame) -> np.ndarray:
    """Bar times as UTC epoch seconds, from a `time` column or the index.

    as_unit("s") rather than `asi8 // 10**9`: `asi8` is in the index's own unit,
    which is nanoseconds on pandas 2 but microseconds or seconds on pandas 3.
    """
    if "time" in candles.columns:
        return candles["time"].to_numpy(dtype=np.int64)
    return pd.DatetimeIndex(candles.index).as_unit("s").asi8


def last_epoch(candles: pd.DataFrame) -> int:
    """The LAST bar's time only.

    Worth its own function: `on_bar` runs on every bar of a run and almost always
    needs nothing but this, while `epoch_seconds` converts the whole window —
    several thousand values discarded, hundreds of thousands of times.
    """
    return _edge_epoch(candles, -1)


def edge_epochs(candles: pd.DataFrame) -> tuple[int, int]:
    """(first bar's time, last bar's time), without converting the window."""
    return _edge_epoch(candles, 0), _edge_epoch(candles, -1)


def _edge_epoch(candles: pd.DataFrame, pos: int) -> int:
    if "time" in candles.columns:
        return int(candles["time"].iloc[pos])
    return int(pd.Timestamp(candles.index[pos]).timestamp())   # a naive index is UTC


def day_groups(t: np.ndarray) -> np.ndarray:
    """Index of the first bar of each UTC day in `t` (which must be ascending)."""
    if not len(t):
        return np.empty(0, dtype=np.int64)
    d = t // DAY
    return np.r_[0, np.flatnonzero(np.diff(d)) + 1]


def daily_atr(t: np.ndarray, high: np.ndarray, low: np.ndarray, close: np.ndarray,
              days: int = 14) -> float | None:
    """ATR(`days`) on daily bars built from these intraday bars, or None.

    None means the window does not hold enough complete days — the caller must
    then decline to trade rather than fall back to a shorter average, because a
    shorter average is a different stop from the measured one.

    The window's FIRST day group is dropped: in a rolling window it is almost
    always a part-day, whose true range is understated, and including it would
    bias every stop tighter than the research's.
    """
    starts = day_groups(t)
    if len(starts) < days + 2:
        return None
    hi = np.maximum.reduceat(high, starts)
    lo = np.minimum.reduceat(low, starts)
    cl = close[np.r_[starts[1:] - 1, len(close) - 1]]
    prev_close = np.r_[cl[0], cl[:-1]]
    tr = np.maximum(hi - lo, np.maximum(np.abs(hi - prev_close), np.abs(lo - prev_close)))
    complete = tr[1:-1]                      # drop the part-day and the day in progress
    if len(complete) < days:
        return None
    value = float(complete[-days:].mean())
    return value if np.isfinite(value) and value > 0 else None


class DailyATRCache:
    """Mixin giving a strategy `self.daily_atr(symbol, candles)`.

    Mix it in before BaseStrategy so `__init__` chains: the cache is created
    lazily, so a subclass that forgets to call super().__init__() still works.
    """

    def daily_atr(self, symbol: str, candles: pd.DataFrame,
                  days: int | None = None) -> float | None:
        cache = getattr(self, "_datr_cache", None)
        if cache is None:
            cache = self._datr_cache = {}
        if candles is None or len(candles) < 2:
            return None
        n = int(days if days is not None else getattr(self.params, "atr_days", 14) or 14)
        first, last = edge_epochs(candles)
        key = (symbol, first // DAY, last // DAY, n, len(candles))
        if key in cache:
            return cache[key]
        value = daily_atr(epoch_seconds(candles),
                          candles["high"].to_numpy(dtype=float),
                          candles["low"].to_numpy(dtype=float),
                          candles["close"].to_numpy(dtype=float), n)
        if len(cache) > 64:                  # one live cycle touches a handful of keys
            cache.clear()
        cache[key] = value
        return value
