"""
backend/strategies/smc/ipdm.py

Institutional Price Delivery Model (IPDM) phase detection.
Identifies Accumulation, Manipulation, and Expansion phases.
Source: SMC_Strategy.md Section 8

Enhanced: Now includes liquidity sweep detection and wick rejection
for accurate Manipulation phase identification (not just ATR).
"""

import pandas as pd
import numpy as np
from typing import Dict, Any, List, Optional


class IPDMDetector:
    """
    Tracks PO3 (Power of 3) cycle phases:
      ACCUMULATION → tight range, low volatility (SM building positions)
      MANIPULATION → liquidity sweep + wick rejection (SM trapping retail)
      EXPANSION    → strong directional move after confirmed structure break
    """

    def __init__(
        self,
        accum_atr_ratio: float = 0.7,
        exp_atr_ratio: float = 1.2,
        accum_range_ratio: float = 0.5,
        wick_rejection_ratio: float = 0.6,
    ):
        self.accum_atr_ratio = accum_atr_ratio
        self.exp_atr_ratio = exp_atr_ratio
        self.accum_range_ratio = accum_range_ratio
        self.wick_rejection_ratio = wick_rejection_ratio
        self.current_phase = "UNKNOWN"
        self.manipulation_completed = False
        self._last_manipulation_bar = None

    def update(
        self,
        candles: pd.DataFrame,
        swing_highs: Optional[List[Dict]] = None,
        swing_lows: Optional[List[Dict]] = None,
    ) -> Dict[str, Any]:
        """
        Determine current IPDM phase based on:
          1. ATR compression/expansion
          2. Liquidity sweep detection (wick through swing level)
          3. Wick rejection (candle body closes back inside range)
        """
        if len(candles) < 65:
            return {"phase": self.current_phase, "manipulation_completed": self.manipulation_completed}

        high = candles['high']
        low = candles['low']
        close = candles['close']
        open_ = candles['open']

        # True Range → ATR
        tr1 = high - low
        tr2 = (high - close.shift(1)).abs()
        tr3 = (low - close.shift(1)).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

        atr = tr.rolling(window=14).mean()
        baseline_atr = atr.rolling(window=50).mean().iloc[-1]
        current_atr = atr.iloc[-1]

        if pd.isna(baseline_atr) or pd.isna(current_atr) or baseline_atr == 0:
            return {"phase": self.current_phase, "manipulation_completed": self.manipulation_completed}

        ratio = current_atr / baseline_atr

        # Recent range vs average range
        recent_range = high.iloc[-20:].max() - low.iloc[-20:].min()
        avg_range = (high.rolling(50).max() - low.rolling(50).min()).mean()
        range_ratio = recent_range / avg_range if avg_range > 0 else 1.0

        last_candle_high = float(high.iloc[-1])
        last_candle_low = float(low.iloc[-1])
        last_candle_close = float(close.iloc[-1])
        last_candle_open = float(open_.iloc[-1])

        # Check for Manipulation: liquidity sweep + wick rejection
        manipulation_detected = False
        if swing_highs or swing_lows:
            manipulation_detected = self._check_manipulation(
                last_candle_high, last_candle_low,
                last_candle_close, last_candle_open,
                swing_highs or [], swing_lows or [],
            )

        if manipulation_detected:
            self.current_phase = "MANIPULATION"
            self.manipulation_completed = True
            self._last_manipulation_bar = len(candles) - 1
        elif ratio < self.accum_atr_ratio and range_ratio < self.accum_range_ratio:
            self.current_phase = "ACCUMULATION"
            self.manipulation_completed = False
        elif ratio > self.exp_atr_ratio:
            self.current_phase = "EXPANSION"
        else:
            # Between accumulation and expansion thresholds
            if self.manipulation_completed:
                # Just came out of manipulation → treat as early expansion
                self.current_phase = "EXPANSION"
            else:
                self.current_phase = "MANIPULATION"

        return {
            "phase": self.current_phase,
            "manipulation_completed": self.manipulation_completed,
            "atr_ratio": round(ratio, 3),
            "range_ratio": round(range_ratio, 3),
        }

    def _check_manipulation(
        self,
        candle_high: float,
        candle_low: float,
        candle_close: float,
        candle_open: float,
        swing_highs: List[Dict],
        swing_lows: List[Dict],
    ) -> bool:
        """
        Detect manipulation: wick through liquidity pool + close back inside.
        This is the 'fake breakout' / 'stop hunt' pattern.
        """
        body_top = max(candle_close, candle_open)
        body_bottom = min(candle_close, candle_open)
        candle_range = candle_high - candle_low
        if candle_range == 0:
            return False

        upper_wick = candle_high - body_top
        lower_wick = body_bottom - candle_low

        # Check BSL sweep: wick above swing high but close below it
        for sh in swing_highs[-3:]:
            level = sh.get("price", 0)
            if level == 0:
                continue
            if candle_high > level and candle_close < level:
                # Wick went above but body closed below → manipulation
                if upper_wick / candle_range >= self.wick_rejection_ratio:
                    return True

        # Check SSL sweep: wick below swing low but close above it
        for sl in swing_lows[-3:]:
            level = sl.get("price", 0)
            if level == 0:
                continue
            if candle_low < level and candle_close > level:
                if lower_wick / candle_range >= self.wick_rejection_ratio:
                    return True

        return False
