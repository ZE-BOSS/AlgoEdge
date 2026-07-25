"""
backend/risk/multi_tp.py

Multi-position TP1/TP2/TP3/TP4/TP5 orchestration.
Source: RiskManagement_Spec.md Section 2

All TP positions open at entry. No deferred stacking.
When TP1 hits, all remaining positions move to break-even.
User configures tp_count (1-5) and RR per level.
"""

import math
from dataclasses import dataclass
from typing import Any

from backend.risk.position_sizer import get_symbol_info
from backend.utils.logger import get_logger

logger = get_logger(__name__)

# Direction normalization: callers may use "BUY"/"SELL" or "BULLISH"/"BEARISH"
_BUY_DIRECTIONS = {"BUY", "BULLISH"}
_SELL_DIRECTIONS = {"SELL", "BEARISH"}


def _is_buy(direction: str) -> bool:
    return direction.upper() in _BUY_DIRECTIONS


def _is_sell(direction: str) -> bool:
    return direction.upper() in _SELL_DIRECTIONS


@dataclass
class TPLevel:
    level: int          # 1, 2, 3, 4, or 5
    rr_multiplier: float
    volume_pct: float   # percentage of total volume (0.0–1.0)
    tp_price: float
    volume: float
    trail_method: str | None  # None for TP1, ATR_TRAIL for TP2, etc.
    deferred: bool = False       # Always False — all TPs open at entry


class MultiTPManager:
    """Calculates TP levels and splits volume across sub-positions."""

    def __init__(self, config: dict[str, Any]):
        self.tp1_rr = config.get("tp1_rr", 1.0)
        self.tp2_rr = config.get("tp2_rr", 3.0)
        self.tp3_rr = config.get("tp3_rr", 5.0)
        self.tp4_rr = config.get("tp4_rr", 10.0)
        self.tp5_rr = config.get("tp5_rr", 15.0)
        raw_splits = config.get("tp_splits", [40, 30, 20, 5, 5])
        if isinstance(raw_splits, str):
            try:
                self.tp_splits = [float(x.strip()) for x in raw_splits.split(",") if x.strip()]
            except ValueError:
                self.tp_splits = [40, 30, 20, 5, 5]
        elif isinstance(raw_splits, list):
            self.tp_splits = [float(x) for x in raw_splits]
        else:
            self.tp_splits = [40, 30, 20, 5, 5]
        self.tp_count = config.get("tp_count", 3)  # User-configurable: how many TPs (1–5)
        self.min_rr = config.get("min_rr", 3.0)
        self.multi_position_mode = config.get("multi_position_mode", True)

        # Trail methods per TP level (all configurable)
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
        symbol: str,
        liquidity_target: float | None = None,
    ) -> list[TPLevel]:
        """
        Calculate TP prices and volume splits for up to 5 levels.
        ALL TPs open at entry (deferred=False always).

        Direction accepts both conventions: "BUY"/"SELL" or "BULLISH"/"BEARISH".
        """
        risk = abs(entry - sl)
        if risk == 0:
            logger.warning("Risk is zero (entry == SL) — cannot calculate TP levels")
            return []

        # Determine direction sign: +1 for BUY/BULLISH, -1 for SELL/BEARISH
        if _is_buy(direction):
            sign = 1
        elif _is_sell(direction):
            sign = -1
        else:
            logger.error(f"Unknown direction '{direction}' — cannot calculate TPs")
            return []

        rr_multipliers = [self.tp1_rr, self.tp2_rr, self.tp3_rr, self.tp4_rr, self.tp5_rr]
        tp_prices = [entry + (risk * rr * sign) for rr in rr_multipliers]

        # TP5 can optionally anchor to next liquidity pool
        if liquidity_target is not None:
            tp_prices[4] = liquidity_target

        # How many TPs the user wants (clamped 1–5)
        active_count = max(1, min(self.tp_count, 5))

        if not self.multi_position_mode:
            # Single position mode — use only TP1
            tp = TPLevel(
                level=1,
                rr_multiplier=self.tp1_rr,
                volume_pct=1.0,
                tp_price=tp_prices[0],
                volume=total_volume,
                trail_method=None,
                deferred=False,
            )
            if not self._validate_tp(tp, entry, direction):
                return []
            return [tp]

        # Build TP levels with volume splits — ALL immediate
        levels = []

        # Normalize splits to match active_count
        splits = self.tp_splits[:active_count]
        total_split = sum(splits)
        if total_split == 0:
            splits = [100 // active_count] * active_count
            total_split = sum(splits)

        # Get exact lot constraints for rounding
        info = get_symbol_info(symbol)
        lot_step = info.get("volume_step", 0.01)
        lot_min = info.get("volume_min", 0.01)

        # ── Dynamic TP Collapse ──
        # If any sub-trade volume is < lot_min, drop the lowest priority TP and recalculate
        volumes = []
        while active_count > 0:
            splits = self.tp_splits[:active_count]
            total_split = sum(splits)
            if total_split == 0:
                splits = [100 // active_count] * active_count
                total_split = sum(splits)
            
            valid = True
            volumes = []
            for i in range(active_count):
                split_pct = splits[i] / total_split
                raw_vol = total_volume * split_pct
                vol = math.floor(raw_vol / lot_step) * lot_step
                vol = round(vol, 4)
                volumes.append(vol)
                if vol < lot_min:
                    valid = False
                    break
            
            if valid:
                break
            
            active_count -= 1

        # If all collapsed (total_volume was too small to split, or even for TP1), enforce Smart Clamping
        if active_count == 0:
            active_count = 1
            splits = [100]
            total_split = 100
            clamped_vol = max(lot_min, math.floor(total_volume / lot_step) * lot_step)
            volumes = [round(clamped_vol, 4)]

        # ── Remainder Sweep ──
        # Any volume lost to rounding is swept into TP1 (if it fits the lot_step)
        allocated_vol = round(sum(volumes), 4)
        remainder = round(total_volume - allocated_vol, 4)
        if remainder >= lot_step:
            sweep_amount = math.floor(remainder / lot_step) * lot_step
            volumes[0] = round(volumes[0] + sweep_amount, 4)

        levels = []
        for i in range(active_count):
            split_pct = splits[i] / total_split
            trail = self.trail_methods[i] if i < len(self.trail_methods) else None
            levels.append(TPLevel(
                level=i + 1,
                rr_multiplier=rr_multipliers[i],
                volume_pct=split_pct,
                tp_price=tp_prices[i],
                volume=volumes[i],
                trail_method=trail,
                deferred=False,  # ALL TPs open at entry
            ))

        # Sanity validation — catch direction bugs at the source
        for tp in levels:
            if not self._validate_tp(tp, entry, direction):
                return []

        logger.debug(
            f"TP levels: {len(levels)} | dir={direction} | entry={entry} | "
            f"risk={risk:.5f} | tp_count={active_count}"
        )

        return levels

    def _validate_tp(self, tp: TPLevel, entry: float, direction: str) -> bool:
        """
        Post-calculation sanity check: TP must be on the correct side of entry.
        Returns False and logs ERROR if a TP is placed on the wrong side.
        """
        if _is_buy(direction) and tp.tp_price <= entry:
            logger.error(
                f"CRITICAL: BUY TP{tp.level} ({tp.tp_price:.5f}) is at or below "
                f"entry ({entry:.5f}). This would guarantee a loss."
            )
            return False
        if _is_sell(direction) and tp.tp_price >= entry:
            logger.error(
                f"CRITICAL: SELL TP{tp.level} ({tp.tp_price:.5f}) is at or above "
                f"entry ({entry:.5f}). This would guarantee a loss."
            )
            return False
        return True
