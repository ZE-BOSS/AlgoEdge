"""
backend/strategies/core/swing_structure.py

Shared swing structure detection used by APA and any future SMC-style strategies.
Provides two-tier fractal swing detection (minor for pattern, major for BOS filter).
"""

from typing import Any
import pandas as pd
from backend.utils.logger import get_logger

logger = get_logger(__name__)


def calculate_atr(candles: pd.DataFrame, lookback: int = 14) -> float:
    """Calculate ATR over the most recent `lookback` bars."""
    if len(candles) < 2:
        return candles["high"].iloc[-1] - candles["low"].iloc[-1] if len(candles) else 0.0001

    recent = candles.iloc[-(lookback + 1):]
    tr_list = []
    for i in range(1, len(recent)):
        c = recent.iloc[i]
        prev = recent.iloc[i - 1]
        tr = max(c["high"] - c["low"], abs(c["high"] - prev["close"]), abs(c["low"] - prev["close"]))
        tr_list.append(tr)
    return (sum(tr_list) / len(tr_list)) if tr_list else 0.0001


def detect_swings(candles: pd.DataFrame, fractal_m: int = 3) -> list[dict[str, Any]]:
    """
    Detect swing highs and lows using a fractal method.

    A swing HIGH at index i requires: candles.high[i] > candles.high[i±1..i±M]
    A swing LOW  at index i requires: candles.low[i]  < candles.low[i±1..i±M]

    Returns list of dicts: {"type": "HIGH"|"LOW", "price": float, "wick": float, "index": pd.Timestamp}
    The list is ordered chronologically. Only confirmed swings (where i+M candles
    have already closed) are returned.
    """
    swings = []
    n = len(candles)
    m = fractal_m

    for i in range(m, n - m):
        bar = candles.iloc[i]
        window_highs = candles["high"].iloc[i - m: i + m + 1]
        window_lows = candles["low"].iloc[i - m: i + m + 1]

        if bar["high"] == window_highs.max() and (window_highs == bar["high"]).sum() == 1:
            swings.append({
                "type": "HIGH",
                "price": bar["high"],
                "body_high": max(bar["open"], bar["close"]),
                "body_low": min(bar["open"], bar["close"]),
                "index": candles.index[i],
            })
        elif bar["low"] == window_lows.min() and (window_lows == bar["low"]).sum() == 1:
            swings.append({
                "type": "LOW",
                "price": bar["low"],
                "body_high": max(bar["open"], bar["close"]),
                "body_low": min(bar["open"], bar["close"]),
                "index": candles.index[i],
            })

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

    # Need at least 3 highs for Bearish H&S
    if len(highs) >= 3:
        for i in range(len(highs) - 2):
            ls, head, rs = highs[i], highs[i + 1], highs[i + 2]
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

    # Need at least 3 lows for Bullish IH&S
    if len(lows) >= 3:
        for i in range(len(lows) - 2):
            ls, head, rs = lows[i], lows[i + 1], lows[i + 2]
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
