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

from backend.risk.position_sizer import calculate_risk_dollars, get_symbol_info
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
        self.prop_firm_config = config.get("prop_firm", {})

        # Trail methods per TP level (all configurable)
        self.trail_methods = [
            None,                                               # TP1: no trail
            config.get("trail_method_tp2", "ATR_TRAIL"),       # TP2
            config.get("trail_method_tp3", "STRUCTURE_TRAIL"), # TP3
            config.get("trail_method_tp4", "ATR_TRAIL"),       # TP4
            config.get("trail_method_tp5", "STRUCTURE_TRAIL"), # TP5
        ]

        # Diagnostic set by calculate_tp_levels() when a risk-cap overshoot survives
        # scale-down purely because of lot_min flooring (not a bad SL distance).
        # Callers (engine.py) can read this immediately after calling
        # calculate_tp_levels() to produce a clearer rejection reason. Reset on every call.
        self._last_overshoot_reason: str | None = None

    def calculate_tp_levels(
        self,
        entry: float,
        sl: float,
        direction: str,
        total_volume: float,
        symbol: str,
        liquidity_target: float | None = None,
        strategy_id: str = "UNKNOWN",
        max_risk_cap_dollars: float = 0.0,  # 2% of balance; 0 = no cap
        use_live_mt5: bool = True,
    ) -> list[TPLevel]:
        """
        Calculates prices and volumes for up to 5 TP levels.
        Direction accepts both conventions: "BUY"/"SELL" or "BULLISH"/"BEARISH".
        If max_risk_cap_dollars > 0, enforces it by:
          1. Reducing TP count (drops last TPs first)
          2. Then scaling remaining volumes proportionally
        """
        self._last_overshoot_reason = None

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

        # Override TP1 RR for DriftJumpAlpha to be much tighter (0.5R) because crash spikes
        # happen fast and reverse quickly.
        tp1_rr_used = 0.5 if strategy_id == "DriftJumpAlpha" else self.tp1_rr

        rr_multipliers = [tp1_rr_used, self.tp2_rr, self.tp3_rr, self.tp4_rr, self.tp5_rr]
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
                rr_multiplier=tp1_rr_used,
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
        info = get_symbol_info(symbol, use_live_mt5=use_live_mt5)
        lot_step = info.get("volume_step", 0.01)
        lot_min = info.get("volume_min", 0.01)

        # ── Dynamic TP Sizing with Minimum Lot Enforcement ──
        # Ensure every requested sub-position has at least lot_min.
        # This overrides volume collapse, potentially slightly increasing risk for very small accounts
        # but guarantees the requested number of TP levels are entered.
        max_lot_allowed = self.prop_firm_config.get("max_lot_sizes", {}).get(symbol, float('inf'))
        volumes = []
        for i in range(active_count):
            split_pct = splits[i] / total_split
            raw_vol = total_volume * split_pct
            vol = math.floor(raw_vol / lot_step) * lot_step
            # Clamp to minimum lot to prevent collapse
            vol = max(lot_min, round(vol, 4))
            # Clamp to max lot size per position
            vol = min(vol, max_lot_allowed)
            volumes.append(vol)

        # ── Remainder Sweep ──
        # Any volume lost to rounding is swept into TP1 (if it fits the lot_step and max lot size)
        allocated_vol = round(sum(volumes), 4)
        remainder = round(total_volume - allocated_vol, 4)
        if remainder >= lot_step:
            sweep_amount = math.floor(remainder / lot_step) * lot_step
            new_tp1_vol = round(volumes[0] + sweep_amount, 4)
            volumes[0] = min(new_tp1_vol, max_lot_allowed)

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

        # ── 2% Risk Cap Enforcement ──────────────────────────────────────────────────
        # After lot_min enforcement, the actual total volume may exceed what the
        # position sizer intended. Enforce the hard 2% cap here.
        if max_risk_cap_dollars > 0 and levels:
            actual_total_risk = calculate_risk_dollars(
                sum(tp.volume for tp in levels), entry, sl, symbol, use_live_mt5=use_live_mt5
            )
            if actual_total_risk > max_risk_cap_dollars:
                logger.warning(
                    f"[MultiTP] {symbol}: lot_min enforcement inflated risk to "
                    f"${actual_total_risk:.2f} (cap=${max_risk_cap_dollars:.2f}). "
                    f"Reducing TP count first..."
                )

                # Step 1: Reduce TP count (drop last TPs one by one)
                while len(levels) > 1:
                    levels = levels[:-1]  # drop last TP
                    actual_total_risk = calculate_risk_dollars(
                        sum(tp.volume for tp in levels), entry, sl, symbol, use_live_mt5=use_live_mt5
                    )
                    if actual_total_risk <= max_risk_cap_dollars:
                        logger.info(
                            f"[MultiTP] {symbol}: Risk capped by reducing to "
                            f"{len(levels)} TP(s). actual_risk=${actual_total_risk:.2f}"
                        )
                        break

                # Step 2: If still over cap after keeping only TP1, scale volumes down
                actual_total_risk = calculate_risk_dollars(
                    sum(tp.volume for tp in levels), entry, sl, symbol, use_live_mt5=use_live_mt5
                )
                if actual_total_risk > max_risk_cap_dollars:
                    scale_factor = max_risk_cap_dollars / actual_total_risk
                    # Track whether lot_min flooring is the reason we can't hit the cap:
                    # if the mathematically-scaled volume (before flooring) is already
                    # below the broker's minimum lot, there is no volume that both
                    # respects lot_min AND stays within the risk cap — this is a
                    # small-account/SL-distance limitation, not a bad SL distance.
                    floored_below_min = False
                    for tp in levels:
                        scaled = tp.volume * scale_factor
                        if scaled < lot_min:
                            floored_below_min = True
                        scaled = max(lot_min, math.floor(scaled / lot_step) * lot_step)
                        scaled = min(scaled, max_lot_allowed)
                        tp.volume = round(scaled, 4)
                    final_risk = calculate_risk_dollars(
                        sum(tp.volume for tp in levels), entry, sl, symbol, use_live_mt5=use_live_mt5
                    )
                    logger.warning(
                        f"[MultiTP] {symbol}: After TP count reduction still over cap. "
                        f"Scaled volumes by {scale_factor:.3f}. Final risk=${final_risk:.2f}"
                    )
                    # If lot_min flooring is what kept us over cap, surface a distinct,
                    # actionable reason instead of letting this fall through to the
                    # generic "post_split_risk_overshoot" rejection in engine.py, which
                    # otherwise reads as a bad-SL-distance bug rather than an
                    # account-size limitation.
                    if floored_below_min and final_risk > max_risk_cap_dollars:
                        self._last_overshoot_reason = "account_too_small_for_sl_distance"

        logger.debug(
            f"TP levels: {len(levels)} | dir={direction} | entry={entry} | "
            f"risk={abs(entry-sl):.5f} | tp_count={len(levels)}"
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
