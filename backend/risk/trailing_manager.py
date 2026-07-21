"""
backend/risk/trailing_manager.py

All 4 trailing stop methods.
Source: RiskManagement_Spec.md Section 3.3
"""

from typing import Optional, Dict, Any, List
import pandas as pd
from backend.utils.logger import get_logger

logger = get_logger(__name__)


class TrailingManager:
    """Manages all 4 trailing stop methods with ratchet logic (never moves back)."""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.trail_pips = config.get("trail_pips", 15.0)
        self.atr_trail_multiplier = config.get("atr_trail_multiplier", 1.5)
        self.trail_pct = config.get("trail_pct", 0.005)
        self.trail_timeframe = config.get("trail_timeframe", "M15")
        # §2.4 fix: read both key names for structure-trail swing length
        self.swing_length = config.get("swing_length", config.get("trail_structure_bars", 3))
        # §2.5 fix: step debounce for live/backtest parity
        self.trail_step_pips = config.get("trail_step_pips", 5.0)

    def _get_atr_multiplier(self, tp_level: int = 1) -> float:
        """§2.2 fix: Resolve per-TP ATR multiplier, falling back to global."""
        return self.config.get(
            f"atr_trail_multiplier_tp{tp_level}",
            self.atr_trail_multiplier
        )

    def calculate_trailing_sl(
        self,
        method: str,
        direction: str,
        current_price: float,
        current_sl: float,
        pip_value: float,
        highest_price: float = 0.0,
        lowest_price: float = 0.0,
        atr_value: float = 0.0,
        swing_points: Optional[List[Dict[str, Any]]] = None,
        tp_level: int = 1,
    ) -> Optional[float]:
        """
        Calculate new trailing SL. Returns None if no adjustment needed.
        RATCHET: SL only moves in the profitable direction — never backward.
        """
        new_sl = None

        if method == "FIXED_PIPS":
            new_sl = self._fixed_pips_trail(direction, current_price, pip_value)
        elif method == "ATR_TRAIL":
            new_sl = self._atr_trail(direction, highest_price, lowest_price, atr_value, tp_level)
        elif method == "STRUCTURE_TRAIL":
            new_sl = self._structure_trail(direction, swing_points, atr_value)
        elif method == "PCT_TRAIL":
            new_sl = self._pct_trail(direction, current_price)
        else:
            return None

        if new_sl is None:
            return None

        # §2.5 fix: Step-based debounce — only move SL if improvement >= trail_step
        step = self.trail_step_pips * pip_value
        if step <= 0:
            step = pip_value

        # Ratchet + step: only move SL in the profitable direction by at least `step`
        if direction == "BUY":
            if new_sl > current_sl + step:
                logger.debug(f"Trail [{method}] BUY TP{tp_level}: SL {current_sl:.5f} -> {new_sl:.5f}")
                return new_sl
        else:
            if current_sl > 0 and new_sl < current_sl - step:
                logger.debug(f"Trail [{method}] SELL TP{tp_level}: SL {current_sl:.5f} -> {new_sl:.5f}")
                return new_sl

        return None

    def _fixed_pips_trail(self, direction: str, current_price: float, pip_value: float) -> float:
        """
        Method 1: Trail by fixed number of pips behind current price.
        Source: RiskManagement_Spec.md Section 3.3 — Method 1
        """
        trail_distance = self.trail_pips * pip_value
        if direction == "BUY":
            return current_price - trail_distance
        else:
            return current_price + trail_distance

    def _atr_trail(self, direction: str, highest: float, lowest: float, atr: float, tp_level: int = 1) -> float:
        """
        Method 2: Trail by ATR × multiplier from highest/lowest price since entry.
        §2.2 fix: Uses per-TP multiplier when available.
        Source: RiskManagement_Spec.md Section 3.3 — Method 2
        """
        multiplier = self._get_atr_multiplier(tp_level)
        trail_distance = atr * multiplier
        if direction == "BUY":
            return highest - trail_distance
        else:
            return lowest + trail_distance

    def _structure_trail(self, direction: str, swing_points: Optional[List[Dict[str, Any]]], atr: float) -> Optional[float]:
        """
        Method 3: Trail to each confirmed swing point (SMC-native).
        SL moves to below confirmed Higher Low (BUY) or above Lower High (SELL) plus an ATR safety buffer.
        Source: RiskManagement_Spec.md Section 3.3 — Method 3
        """
        if not swing_points:
            # Fallback: Do not move SL if no structure exists
            return None
            
        atr_buffer = atr * 0.5  # Standard 0.5 ATR buffer behind structural liquidity

        if direction == "BUY":
            # Trail to below each confirmed Higher Low
            lows = [s["price"] for s in swing_points if s["type"] == "LOW"]
            if lows:
                return lows[-1] - atr_buffer  # Most recent swing low - buffer
        else:
            # Trail to above each confirmed Lower High
            highs = [s["price"] for s in swing_points if s["type"] == "HIGH"]
            if highs:
                return highs[-1] + atr_buffer  # Most recent swing high + buffer

        return None

    def _pct_trail(self, direction: str, current_price: float) -> float:
        """
        Method 4: Trail by percentage of current price.
        Source: RiskManagement_Spec.md Section 3.3 — Method 4
        """
        trail_distance = current_price * self.trail_pct
        if direction == "BUY":
            return current_price - trail_distance
        else:
            return current_price + trail_distance
