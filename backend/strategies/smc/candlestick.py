"""
backend/strategies/smc/candlestick.py

SMC Candlestick Confirmation Bible — Pattern Detection Engine
============================================================
Implements all Tier 1, Tier 2, and Tier 3 candlestick patterns
used as the final confirmation gate before trade execution.

Source: SMC_Strategy.md Section 9 — The Candlestick Confirmation Bible
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional
import pandas as pd
import numpy as np


# ── Data Structures ──────────────────────────────────────────────────────────

class PatternTier(Enum):
    TIER_1 = 1   # Highest confidence: Engulfing, Hammer/Pin Bar
    TIER_2 = 2   # High confidence: Doji variants, Morning/Evening Star
    TIER_3 = 3   # Secondary: Rejection wick, Inside bar, Displacement


class PatternDirection(Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"


@dataclass
class CandlePattern:
    name: str
    tier: PatternTier
    direction: PatternDirection
    confidence: float       # 0.0–1.0 confidence multiplier
    entry_candle_idx: int   # Index of the candle to enter on
    description: str


# ── Candle Utility Functions ──────────────────────────────────────────────────

def body_size(candle: pd.Series) -> float:
    """Absolute size of the candle body (open to close)."""
    return abs(candle["close"] - candle["open"])


def upper_wick(candle: pd.Series) -> float:
    """Upper wick length."""
    return candle["high"] - max(candle["open"], candle["close"])


def lower_wick(candle: pd.Series) -> float:
    """Lower wick length."""
    return min(candle["open"], candle["close"]) - candle["low"]


def total_range(candle: pd.Series) -> float:
    """Total candle range high to low."""
    return candle["high"] - candle["low"]


def is_bullish(candle: pd.Series) -> bool:
    return candle["close"] > candle["open"]


def is_bearish(candle: pd.Series) -> bool:
    return candle["close"] < candle["open"]


def avg_body(candles: pd.DataFrame, lookback: int = 20) -> float:
    """Average body size over the last N candles."""
    bodies = abs(candles["close"] - candles["open"]).tail(lookback)
    return bodies.mean()


# ── Tier 1 Patterns ───────────────────────────────────────────────────────────

def detect_bullish_engulfing(
    candles: pd.DataFrame,
    idx: int,
    min_size_ratio: float = 1.0,
) -> Optional[CandlePattern]:
    """
    Bullish Engulfing: Large bullish body completely engulfs previous bearish body.
    Must appear at or inside a bullish OB/FVG zone.
    """
    if idx < 1:
        return None

    curr = candles.iloc[idx]
    prev = candles.iloc[idx - 1]

    if not (is_bullish(curr) and is_bearish(prev)):
        return None

    # Body must engulf previous body
    if curr.open > prev.close or curr.close < prev.open:
        return None

    # Size filter: current body >= previous body
    if body_size(curr) < body_size(prev) * min_size_ratio:
        return None

    return CandlePattern(
        name="Bullish Engulfing",
        tier=PatternTier.TIER_1,
        direction=PatternDirection.BULLISH,
        confidence=0.90,
        entry_candle_idx=idx,
        description="Large bullish body engulfs previous bearish body — institutional buying confirmed",
    )


def detect_bearish_engulfing(
    candles: pd.DataFrame,
    idx: int,
    min_size_ratio: float = 1.0,
) -> Optional[CandlePattern]:
    """
    Bearish Engulfing: Large bearish body completely engulfs previous bullish body.
    """
    if idx < 1:
        return None

    curr = candles.iloc[idx]
    prev = candles.iloc[idx - 1]

    if not (is_bearish(curr) and is_bullish(prev)):
        return None

    if curr.open < prev.close or curr.close > prev.open:
        return None

    if body_size(curr) < body_size(prev) * min_size_ratio:
        return None

    return CandlePattern(
        name="Bearish Engulfing",
        tier=PatternTier.TIER_1,
        direction=PatternDirection.BEARISH,
        confidence=0.90,
        entry_candle_idx=idx,
        description="Large bearish body engulfs previous bullish body — institutional selling confirmed",
    )


def detect_hammer(
    candles: pd.DataFrame,
    idx: int,
    min_wick_ratio: float = 2.0,
    max_upper_wick_ratio: float = 0.5,
) -> Optional[CandlePattern]:
    """
    Hammer / Bullish Pin Bar:
    - Long lower wick >= 2× body size
    - Minimal upper wick (<= 0.5× body)
    - Color irrelevant
    - Indicates rejection of lows / SSL sweep
    """
    candle = candles.iloc[idx]
    body = body_size(candle)

    if body == 0:
        return None

    lw = lower_wick(candle)
    uw = upper_wick(candle)

    if lw < body * min_wick_ratio:
        return None

    if uw > body * max_upper_wick_ratio:
        return None

    return CandlePattern(
        name="Hammer (Bullish Pin Bar)",
        tier=PatternTier.TIER_1,
        direction=PatternDirection.BULLISH,
        confidence=0.88,
        entry_candle_idx=idx,
        description="Long lower wick rejects SSL sweep — buyers overwhelmed sellers",
    )


def detect_shooting_star(
    candles: pd.DataFrame,
    idx: int,
    min_wick_ratio: float = 2.0,
    max_lower_wick_ratio: float = 0.5,
) -> Optional[CandlePattern]:
    """
    Shooting Star / Bearish Pin Bar:
    - Long upper wick >= 2× body size
    - Minimal lower wick (<= 0.5× body)
    - Indicates rejection of highs / BSL sweep
    """
    candle = candles.iloc[idx]
    body = body_size(candle)

    if body == 0:
        return None

    uw = upper_wick(candle)
    lw = lower_wick(candle)

    if uw < body * min_wick_ratio:
        return None

    if lw > body * max_lower_wick_ratio:
        return None

    return CandlePattern(
        name="Shooting Star (Bearish Pin Bar)",
        tier=PatternTier.TIER_1,
        direction=PatternDirection.BEARISH,
        confidence=0.88,
        entry_candle_idx=idx,
        description="Long upper wick rejects BSL sweep — sellers overwhelmed buyers",
    )


# ── Tier 2 Patterns ───────────────────────────────────────────────────────────

def detect_dragonfly_doji(
    candles: pd.DataFrame,
    idx: int,
    max_body_pct: float = 0.10,
    min_lower_wick_pct: float = 0.60,
) -> Optional[CandlePattern]:
    """
    Dragonfly Doji (Bullish):
    - Body < 10% of total range
    - Lower wick > 60% of total range
    - Near-zero upper wick
    """
    candle = candles.iloc[idx]
    rng = total_range(candle)

    if rng == 0:
        return None

    body_pct = body_size(candle) / rng
    lw_pct = lower_wick(candle) / rng
    uw_pct = upper_wick(candle) / rng

    if body_pct > max_body_pct:
        return None
    if lw_pct < min_lower_wick_pct:
        return None
    if uw_pct > 0.10:
        return None

    return CandlePattern(
        name="Dragonfly Doji",
        tier=PatternTier.TIER_2,
        direction=PatternDirection.BULLISH,
        confidence=0.80,
        entry_candle_idx=idx,
        description="Indecision resolved bullishly — full seller rejection from lows",
    )


def detect_gravestone_doji(
    candles: pd.DataFrame,
    idx: int,
    max_body_pct: float = 0.10,
    min_upper_wick_pct: float = 0.60,
) -> Optional[CandlePattern]:
    """
    Gravestone Doji (Bearish):
    - Body < 10% of total range
    - Upper wick > 60% of total range
    - Near-zero lower wick
    """
    candle = candles.iloc[idx]
    rng = total_range(candle)

    if rng == 0:
        return None

    body_pct = body_size(candle) / rng
    uw_pct = upper_wick(candle) / rng
    lw_pct = lower_wick(candle) / rng

    if body_pct > max_body_pct:
        return None
    if uw_pct < min_upper_wick_pct:
        return None
    if lw_pct > 0.10:
        return None

    return CandlePattern(
        name="Gravestone Doji",
        tier=PatternTier.TIER_2,
        direction=PatternDirection.BEARISH,
        confidence=0.80,
        entry_candle_idx=idx,
        description="Indecision resolved bearishly — full buyer rejection from highs",
    )


def detect_morning_star(
    candles: pd.DataFrame,
    idx: int,
    min_body_ratio: float = 1.2,
    min_close_pct: float = 0.50,
) -> Optional[CandlePattern]:
    """
    Morning Star (3-candle bullish reversal):
    Candle[-2]: Large bearish candle
    Candle[-1]: Small body / Doji (the "star")
    Candle[0]:  Large bullish closing above 50% of candle[-2]
    """
    if idx < 2:
        return None

    c1 = candles.iloc[idx - 2]   # large bearish
    c2 = candles.iloc[idx - 1]   # small star
    c3 = candles.iloc[idx]       # large bullish

    avg_b = avg_body(candles.iloc[:idx], 20)

    if not is_bearish(c1):
        return None
    if body_size(c1) < avg_b * min_body_ratio:
        return None
    if body_size(c2) > avg_b * 0.50:   # star must be small
        return None
    if not is_bullish(c3):
        return None

    # c3 must close above 50% of c1's range
    c1_midpoint = (c1.open + c1.close) / 2
    if c3.close < c1_midpoint:
        return None

    return CandlePattern(
        name="Morning Star",
        tier=PatternTier.TIER_2,
        direction=PatternDirection.BULLISH,
        confidence=0.82,
        entry_candle_idx=idx,
        description="3-candle reversal: sell exhaustion → indecision → bullish takeover",
    )


def detect_evening_star(
    candles: pd.DataFrame,
    idx: int,
    min_body_ratio: float = 1.2,
) -> Optional[CandlePattern]:
    """
    Evening Star (3-candle bearish reversal):
    Candle[-2]: Large bullish
    Candle[-1]: Small body / Doji
    Candle[0]:  Large bearish closing below 50% of candle[-2]
    """
    if idx < 2:
        return None

    c1 = candles.iloc[idx - 2]
    c2 = candles.iloc[idx - 1]
    c3 = candles.iloc[idx]

    avg_b = avg_body(candles.iloc[:idx], 20)

    if not is_bullish(c1):
        return None
    if body_size(c1) < avg_b * min_body_ratio:
        return None
    if body_size(c2) > avg_b * 0.50:
        return None
    if not is_bearish(c3):
        return None

    c1_midpoint = (c1.open + c1.close) / 2
    if c3.close > c1_midpoint:
        return None

    return CandlePattern(
        name="Evening Star",
        tier=PatternTier.TIER_2,
        direction=PatternDirection.BEARISH,
        confidence=0.82,
        entry_candle_idx=idx,
        description="3-candle reversal: buy exhaustion → indecision → bearish takeover",
    )


# ── Tier 3 Patterns ───────────────────────────────────────────────────────────

def detect_inside_bar(
    candles: pd.DataFrame,
    idx: int,
    bias: str,
) -> Optional[CandlePattern]:
    """
    Inside Bar: Current candle's high and low are both within the previous candle's range.
    Used as continuation signal when at POI — breakout in bias direction = entry.
    """
    if idx < 1:
        return None

    curr = candles.iloc[idx]
    prev = candles.iloc[idx - 1]

    if curr.high >= prev.high or curr.low <= prev.low:
        return None

    direction = PatternDirection.BULLISH if bias == "BULLISH" else PatternDirection.BEARISH

    return CandlePattern(
        name="Inside Bar",
        tier=PatternTier.TIER_3,
        direction=direction,
        confidence=0.65,
        entry_candle_idx=idx,
        description=f"Consolidation at POI — enter on breakout in {bias} direction",
    )


def detect_rejection_wick(
    candles: pd.DataFrame,
    idx: int,
    bias: str,
    min_wick_ratio: float = 2.0,
) -> Optional[CandlePattern]:
    """
    Rejection Wick: Any candle with a significant wick at a key SMC level.
    Bullish: long lower wick at OB/FVG zone
    Bearish: long upper wick at OB/FVG zone
    """
    candle = candles.iloc[idx]
    body = body_size(candle)

    if body == 0:
        body = total_range(candle) * 0.05  # avoid div-by-zero for doji

    if bias == "BULLISH":
        lw = lower_wick(candle)
        if lw >= body * min_wick_ratio:
            return CandlePattern(
                name="Bullish Rejection Wick",
                tier=PatternTier.TIER_3,
                direction=PatternDirection.BULLISH,
                confidence=0.65,
                entry_candle_idx=idx,
                description="Lower wick rejection at demand zone",
            )
    else:
        uw = upper_wick(candle)
        if uw >= body * min_wick_ratio:
            return CandlePattern(
                name="Bearish Rejection Wick",
                tier=PatternTier.TIER_3,
                direction=PatternDirection.BEARISH,
                confidence=0.65,
                entry_candle_idx=idx,
                description="Upper wick rejection at supply zone",
            )

    return None


def detect_displacement(
    candles: pd.DataFrame,
    idx: int,
    bias: str,
    min_body_ratio: float = 1.5,
) -> Optional[CandlePattern]:
    """
    Displacement Candle: Large strong candle (body > 1.5× average) signaling
    the start of ChoCH expansion. This creates an OB/FVG behind it.
    Used to confirm that expansion phase has begun.
    """
    candle = candles.iloc[idx]
    avg_b = avg_body(candles.iloc[:idx], 20)

    if body_size(candle) < avg_b * min_body_ratio:
        return None

    if bias == "BULLISH" and not is_bullish(candle):
        return None
    if bias == "BEARISH" and not is_bearish(candle):
        return None

    direction = PatternDirection.BULLISH if bias == "BULLISH" else PatternDirection.BEARISH

    return CandlePattern(
        name="Displacement Candle",
        tier=PatternTier.TIER_3,
        direction=direction,
        confidence=0.70,
        entry_candle_idx=idx,
        description=f"Strong {bias.lower()} displacement — expansion phase confirmed, OB/FVG left behind",
    )


# ── Master Detection Function ─────────────────────────────────────────────────

def detect_confirmation_pattern(
    candles: pd.DataFrame,
    bias: str,                  # "BULLISH" or "BEARISH"
    lookback: int = 3,          # how many recent candles to check
) -> Optional[CandlePattern]:
    """
    Master function. Scans the last `lookback` candles for any valid
    SMC confirmation pattern aligned with the given bias.

    Returns the highest-tier, highest-confidence pattern found.
    Returns None if no valid pattern detected.

    Usage:
        pattern = detect_confirmation_pattern(m5_candles, bias="BULLISH")
        if pattern:
            signal.confidence += pattern.confidence * SCORE_WEIGHT
    """
    if len(candles) < 3:
        return None

    found_patterns = []
    n = len(candles)

    for offset in range(lookback):
        idx = n - 1 - offset

        if idx < 2:
            break

        # ── Tier 1: Check first (highest priority) ────────────────────
        if bias == "BULLISH":
            for detector in [detect_bullish_engulfing, detect_hammer]:
                p = detector(candles, idx)
                if p and p.direction == PatternDirection.BULLISH:
                    found_patterns.append(p)

        elif bias == "BEARISH":
            for detector in [detect_bearish_engulfing, detect_shooting_star]:
                p = detector(candles, idx)
                if p and p.direction == PatternDirection.BEARISH:
                    found_patterns.append(p)

        # ── Tier 2 ────────────────────────────────────────────────────
        if bias == "BULLISH":
            for detector in [detect_dragonfly_doji, detect_morning_star]:
                p = detector(candles, idx)
                if p and p.direction == PatternDirection.BULLISH:
                    found_patterns.append(p)

        elif bias == "BEARISH":
            for detector in [detect_gravestone_doji, detect_evening_star]:
                p = detector(candles, idx)
                if p and p.direction == PatternDirection.BEARISH:
                    found_patterns.append(p)

        # ── Tier 3 ────────────────────────────────────────────────────
        for detector in [
            lambda c, i: detect_rejection_wick(c, i, bias),
            lambda c, i: detect_inside_bar(c, i, bias),
            lambda c, i: detect_displacement(c, i, bias),
        ]:
            p = detector(candles, idx)
            if p:
                found_patterns.append(p)

    if not found_patterns:
        return None

    # Return the best pattern: prioritize by tier, then confidence
    return min(
        found_patterns,
        key=lambda p: (p.tier.value, -p.confidence)
    )


# ── Scoring Contribution ──────────────────────────────────────────────────────

CANDLESTICK_SCORES = {
    PatternTier.TIER_1: 15,
    PatternTier.TIER_2: 10,
    PatternTier.TIER_3: 5,
}

def get_candlestick_score(pattern: Optional[CandlePattern]) -> int:
    """
    Returns the confluence score points to add for this pattern.
    Used by the confluence scoring system in confluence.py.
    """
    if pattern is None:
        return 0
    return CANDLESTICK_SCORES.get(pattern.tier, 0)
