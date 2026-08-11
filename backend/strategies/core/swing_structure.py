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
    indices = candles.index
    
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

    # Need at least 3 highs for Bearish H&S
    if len(highs) >= 3:
        for i in range(len(highs) - 3, -1, -1):
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
        for i in range(len(lows) - 3, -1, -1):
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
