"""
backend/strategies/core/swing_structure.py

Shared swing structure detection used by APA and any future SMC-style strategies.
Provides two-tier fractal swing detection (minor for pattern, major for BOS filter).
"""

from typing import Any
import numpy as np
import pandas as pd
from backend.utils.logger import get_logger

logger = get_logger(__name__)


def calculate_atr(candles: pd.DataFrame, lookback: int = 14) -> float:
    """
    Average True Range over the most recent `lookback` bars.

    Vectorised with numpy. The previous implementation looped in Python and did
    two `recent.iloc[i]` row lookups per iteration — roughly 30 pandas row
    constructions per call, each building a Series with dtype resolution.

    That was measurably the single most expensive thing in the whole backtest.
    Profiling the APA signal loop on XAUUSD M5/M15 (400 bars, 534 on_bar calls):

        calculate_atr   90.1s cumulative of 105.7s wall  — 85% of total runtime
        pandas .iloc    16,820 calls                     — 78s inside those
        loop throughput 3.8 bars/second

    ATR is called at least once per on_bar by nearly every strategy, so this one
    function set the speed of every backtest in the system. The numpy form below
    does the same arithmetic with three array subtractions and no per-row
    objects.

    Behaviour is unchanged, deliberately including the two quirks the original
    had: it is a SIMPLE mean of true range (not Wilder's smoothing), and it
    returns 0.0001 rather than 0.0 when there is nothing to measure, because
    callers divide by it.
    """
    n = len(candles)
    if n < 2:
        return float(candles["high"].iloc[-1] - candles["low"].iloc[-1]) if n else 0.0001

    # lookback+1 bars gives `lookback` true-range values (each needs a previous close).
    take = min(lookback + 1, n)
    high = candles["high"].to_numpy(dtype=float)[-take:]
    low = candles["low"].to_numpy(dtype=float)[-take:]
    close = candles["close"].to_numpy(dtype=float)[-take:]

    prev_close = close[:-1]
    tr = np.maximum(
        high[1:] - low[1:],
        np.maximum(np.abs(high[1:] - prev_close), np.abs(low[1:] - prev_close)),
    )
    if tr.size == 0:
        return 0.0001
    atr = float(tr.mean())
    # NaN can reach here from a gap in the feed; callers divide by ATR, so a
    # NaN would silently poison every downstream distance.
    return atr if atr == atr else 0.0001


def detect_swings(candles: pd.DataFrame, fractal_m: int = 3) -> list[dict[str, Any]]:
    """
    Detect swing highs and lows using a fractal method.
    Vectorized implementation.
    """
    import numpy as np
    
    n = len(candles)
    if n < 2 * fractal_m + 1:
        return []
        
    highs = candles["high"].values
    lows = candles["low"].values
    opens = candles["open"].values
    closes = candles["close"].values
    # `.to_numpy()` rather than the DatetimeIndex itself.
    #
    # Indexing a DatetimeIndex boxes each element into a pandas Timestamp, and
    # this function does that once per detected swing. Profiling APA on XAUUSD
    # (scripts/profile_signal_loop.py, 1,200 bars) showed 37,236 calls through
    # `datetimelike.__getitem__` and 34,836 through `_box_func` — 7.2s of a 26s
    # run, entirely from building Timestamps nobody needed as Timestamps.
    #
    # A numpy datetime64 array indexes without boxing. Consumers are unaffected:
    # comparison and sorting between datetime64 and Timestamp both work, and
    # `.loc[]` / `in index` accept datetime64 (the two APA call sites that do
    # this are covered by tests/test_markings.py).
    indices = candles.index.to_numpy()
    
    is_high = np.ones(n, dtype=bool)
    is_low = np.ones(n, dtype=bool)
    
    for shift in range(1, fractal_m + 1):
        left_high = np.full(n, np.inf)
        left_high[shift:] = highs[:-shift]
        is_high &= (highs > left_high)
        
        right_high = np.full(n, np.inf)
        right_high[:-shift] = highs[shift:]
        is_high &= (highs > right_high)
        
        left_low = np.full(n, -np.inf)
        left_low[shift:] = lows[:-shift]
        is_low &= (lows < left_low)
        
        right_low = np.full(n, -np.inf)
        right_low[:-shift] = lows[shift:]
        is_low &= (lows < right_low)
        
    is_high[:fractal_m] = False
    is_high[-fractal_m:] = False
    is_low[:fractal_m] = False
    is_low[-fractal_m:] = False
    
    high_idx = np.where(is_high)[0]
    low_idx = np.where(is_low)[0]
    
    swings = []
    for i in high_idx:
        swings.append({
            "type": "HIGH",
            "price": highs[i],
            "body_high": max(opens[i], closes[i]),
            "body_low": min(opens[i], closes[i]),
            "index": indices[i],
        })
    for i in low_idx:
        swings.append({
            "type": "LOW",
            "price": lows[i],
            "body_high": max(opens[i], closes[i]),
            "body_low": min(opens[i], closes[i]),
            "index": indices[i],
        })
        
    swings.sort(key=lambda x: x["index"])
    return swings


def detect_hs_pattern(
    minor_swings: list[dict[str, Any]],
    atr: float,
    symmetry_tolerance_atr: float = 0.3,
) -> dict[str, Any] | None:
    """
    Scan the most recent minor swings for a Head & Shoulders (or Inverted H&S) pattern.

    Bearish H&S (distribution top → expect SELL):
      Left Shoulder HIGH → Head HIGH (higher) → Right Shoulder HIGH (similar to Left)
      Neckline = LOW between Left Shoulder and Head, and LOW between Head and Right Shoulder

    Bullish IH&S (accumulation bottom → expect BUY):
      Left Shoulder LOW → Head LOW (lower) → Right Shoulder LOW (similar to Left)
      Neckline = HIGH between Left Shoulder and Head, and HIGH between Head and Right Shoulder

    Returns dict with keys: type (BEARISH/BULLISH), left_shoulder, head, right_shoulder,
    neckline_price, or None if no valid pattern found.
    """
    tolerance = symmetry_tolerance_atr * atr
    highs = [s for s in minor_swings if s["type"] == "HIGH"]
    lows  = [s for s in minor_swings if s["type"] == "LOW"]

    # Need at least 3 highs for Bearish H&S. Only evaluate the single most-recent
    # triplet — walking backward through older triplets on symmetry-tolerance
    # failure can lock onto a stale Right Shoulder that isn't the most recent
    # swing, producing a pattern that no longer reflects current price structure.
    if len(highs) >= 3:
        ls, head, rs = highs[-3], highs[-2], highs[-1]
        if head["price"] > ls["price"] and head["price"] > rs["price"]:
            if abs(ls["price"] - rs["price"]) <= tolerance:
                # Find neckline: lowest LOW between ls-head and head-rs
                lows_between = [
                    sw for sw in lows
                    if ls["index"] < sw["index"] < rs["index"]
                ]
                if lows_between:
                    neckline = min(lows_between, key=lambda x: x["price"])
                    return {
                        "type": "BEARISH",
                        "left_shoulder": ls,
                        "head": head,
                        "right_shoulder": rs,
                        "neckline": neckline,
                        "neckline_price": neckline["price"],
                    }

    # Need at least 3 lows for Bullish IH&S. Same most-recent-triplet-only rule
    # as above applies here.
    if len(lows) >= 3:
        ls, head, rs = lows[-3], lows[-2], lows[-1]
        if head["price"] < ls["price"] and head["price"] < rs["price"]:
            if abs(ls["price"] - rs["price"]) <= tolerance:
                highs_between = [
                    sw for sw in highs
                    if ls["index"] < sw["index"] < rs["index"]
                ]
                if highs_between:
                    neckline = max(highs_between, key=lambda x: x["price"])
                    return {
                        "type": "BULLISH",
                        "left_shoulder": ls,
                        "head": head,
                        "right_shoulder": rs,
                        "neckline": neckline,
                        "neckline_price": neckline["price"],
                    }

    return None
