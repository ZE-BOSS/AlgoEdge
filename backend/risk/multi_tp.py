"""
backend/risk/multi_tp.py

Multi-position TP1/TP2/TP3/TP4/TP5 orchestration.
Source: RiskManagement_Spec.md Section 2
"""

from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from backend.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class TPLevel:
    level: int          # 1, 2, 3, 4, or 5
    rr_multiplier: float
    volume_pct: float   # default splits: 30%, 25%, 20%, 15%, 10%
    tp_price: float
    volume: float
    trail_method: Optional[str]  # None for TP1, ATR_TRAIL for TP2, etc.


class MultiTPManager:
    """Calculates TP levels and splits volume across sub-positions."""

    def __init__(self, config: Dict[str, Any]):
        self.tp1_rr = config.get("tp1_rr", 3.0)
        self.tp2_rr = config.get("tp2_rr", 5.0)
        self.tp3_rr = config.get("tp3_rr", 7.0)
        self.tp4_rr = config.get("tp4_rr", 10.0)
        self.tp5_rr = config.get("tp5_rr", 15.0)
        self.tp_splits = config.get("tp_splits", [30, 25, 20, 15, 10])
        self.tp_levels_count = config.get("tp_levels", 5)
        self.min_rr = config.get("min_rr", 3.0)
        self.multi_position_mode = config.get("multi_position_mode", True)

        # Trail methods per TP level
        self.trail_methods = [
            None,                                               # TP1: no trail
            config.get("trail_method_tp2", "ATR_TRAIL"),       # TP2
            config.get("trail_method_tp3", "STRUCTURE_TRAIL"), # TP3
            config.get("trail_method_tp4", "ATR_TRAIL"),       # TP4
            config.get("trail_method_tp5", "STRUCTURE_TRAIL"), # TP5
        ]

    def calculate_tp_levels(
        self,
        entry: float,
        sl: float,
        direction: str,
        total_volume: float,
        liquidity_target: Optional[float] = None,
    ) -> List[TPLevel]:
        """
        Calculate TP prices and volume splits for up to 5 levels.
        Source: RiskManagement_Spec.md Section 2.2 and 2.3
        """
        risk = abs(entry - sl)
        if risk == 0:
            return []

        sign = 1 if direction == "BULLISH" else -1

        rr_multipliers = [self.tp1_rr, self.tp2_rr, self.tp3_rr, self.tp4_rr, self.tp5_rr]
        tp_prices = [entry + (risk * rr * sign) for rr in rr_multipliers]

        # TP5 can optionally anchor to next liquidity pool
        if liquidity_target is not None:
            tp_prices[4] = liquidity_target

        if not self.multi_position_mode:
            # Single position mode — use only TP1
            return [TPLevel(
                level=1,
                rr_multiplier=self.tp1_rr,
                volume_pct=1.0,
                tp_price=tp_prices[0],
                volume=total_volume,
                trail_method=None,
            )]

        # Build TP levels with volume splits (up to tp_levels_count)
        levels = []
        active_count = min(self.tp_levels_count, 5)

        for i in range(active_count):
            # Get split percentage, pad with 0 if tp_splits is shorter
            if i < len(self.tp_splits):
                split_pct = self.tp_splits[i] / 100.0
            else:
                split_pct = 0.0

            vol = round(total_volume * split_pct, 2)

            # Skip TP levels with calculated RR below minimum (except TP1)
            if i > 0 and rr_multipliers[i] < self.min_rr:
                continue

            # Skip if volume is too small
            if vol < 0.01:
                continue

            trail = self.trail_methods[i] if i < len(self.trail_methods) else None

            levels.append(TPLevel(
                level=i + 1,
                rr_multiplier=rr_multipliers[i],
                volume_pct=split_pct,
                tp_price=tp_prices[i],
                volume=vol,
                trail_method=trail,
            ))

        # If only TP1 is viable, put all volume there
        if len(levels) == 1:
            levels[0].volume = total_volume
            levels[0].volume_pct = 1.0

        return levels
