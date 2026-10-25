"""
backend/strategies/strategy_roboquant/common.py

Shared pieces for the five strategies replicated from roboquant.dev templates
(2026-10-19). Everything here is a pure function of the bars it is given:
none of the five engines keeps memory between calls, so the live feed and a
backtest compute exactly the same thing from the same window.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ET = ZoneInfo("America/New_York")


def epochs(candles: pd.DataFrame) -> np.ndarray:
    """Bar OPEN times, epoch seconds (a naive index is UTC)."""
    if "time" in candles.columns:
        return candles["time"].to_numpy(dtype="int64")
    return pd.DatetimeIndex(candles.index).as_unit("s").asi8


def et_parts(t: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(ET calendar day as YYYYMMDD, ET wall clock as HHMM) per bar time."""
    idx = pd.DatetimeIndex(pd.to_datetime(t, unit="s", utc=True)).tz_convert(ET)
    day = idx.year * 10000 + idx.month * 100 + idx.day
    hhmm = idx.hour * 100 + idx.minute
    return np.asarray(day, dtype="int64"), np.asarray(hhmm, dtype="int64")


def et_hhmm_one(t: int) -> int:
    d = datetime.fromtimestamp(int(t), ET)
    return d.hour * 100 + d.minute


def volume(candles: pd.DataFrame) -> np.ndarray:
    for col in ("volume", "tick_volume", "real_volume"):
        if col in candles.columns:
            return candles[col].to_numpy(dtype=float)
    return np.zeros(len(candles))


def atr_series(high: np.ndarray, low: np.ndarray, close: np.ndarray, n: int) -> np.ndarray:
    """Wilder ATR (RMA of true range), seeded with the SMA of the first n
    true ranges - the usual definition; NaN until n bars exist."""
    high, low, close = (np.asarray(x, dtype=float) for x in (high, low, close))
    out = np.full(len(close), np.nan)
    if len(close) <= n:
        return out
    prev = np.r_[close[0], close[:-1]]
    tr = np.maximum(high - low, np.maximum(np.abs(high - prev), np.abs(low - prev)))
    a = tr[1:n + 1].mean()
    out[n] = a
    for i in range(n + 1, len(close)):
        a = (a * (n - 1) + tr[i]) / n
        out[i] = a
    return out


def ema(x: np.ndarray, length: int) -> np.ndarray:
    """EMA with alpha 2/(n+1), seeded with the first value (roboquant's form)."""
    x = np.asarray(x, dtype=float)
    # copy: callers blank the warm-up bars in place, and under pandas 3
    # copy-on-write `to_numpy()` can be a read-only view
    return pd.Series(x).ewm(alpha=2.0 / (length + 1.0), adjust=False).mean().to_numpy(copy=True)


def simulate_exit(high: np.ndarray, low: np.ndarray, start: int, end: int,
                  is_long: bool, sl: float, tp: float) -> int | None:
    """First bar index in [start, end) where SL or TP is touched (SL first on a
    bar touching both), or None if still open at `end`. Used to replay the
    day's earlier trades so a stateless engine knows a position was open."""
    for j in range(start, end):
        if is_long:
            if low[j] <= sl or high[j] >= tp:
                return j
        else:
            if high[j] >= sl or low[j] <= tp:
                return j
    return None
