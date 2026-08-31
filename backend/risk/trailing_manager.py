"""
backend/risk/trailing_manager.py

All 4 trailing stop methods.
Source: RiskManagement_Spec.md Section 3.3
"""

from typing import Any

from backend.utils.logger import get_logger

logger = get_logger(__name__)


class TrailingManager:
    """Manages all 4 trailing stop methods with ratchet logic (never moves back)."""

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.trail_pips = config.get("trail_pips", 15.0)
        self.atr_trail_multiplier = config.get("atr_trail_multiplier", 1.5)
        # [4.3/D3] `trail_pct` is stored/edited everywhere else (RiskParams,
        # position_manager.py's live PCT_TRAIL) as a PERCENT (0.5 means 0.5%).
        # This class used to read it as an ALREADY-a-fraction value with its
        # own divergent default (0.005) — silently correct only when the key
        # was absent (falling to this default) and catastrophically wrong
        # (150% trail distance for `trail_pct=1.5`) whenever a caller actually
        # passed the real RiskParams-style percent value through. Converted
        # ONCE here, at the point of consumption, using the same "percent /
        # 100" convention position_manager.py already used correctly.
        self.trail_pct = float(config.get("trail_pct", 0.5) or 0.0) / 100.0
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
        swing_points: list[dict[str, Any]] | None = None,
        tp_level: int = 1,
    ) -> float | None:
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

        from backend.risk.multi_tp import _is_buy
        is_buy = _is_buy(direction)

        # Ratchet + step: only move SL in the profitable direction by at least `step`
        if is_buy:
            if new_sl > current_sl + step:
                logger.debug(f"Trail [{method}] BUY TP{tp_level}: SL {current_sl:.5f} -> {new_sl:.5f}")
                return new_sl
        else:
            # [17.5] `current_sl == 0` means the SELL has NO protective stop yet.
            # This branch required `current_sl > 0`, so an unprotected short was
            # never given a trailing stop at all — while live
            # (position_manager._calculate_trailing_sl) uses
            # `current_sl == 0.0 or ...` and does protect it. Differential
            # testing found this as the only genuine behavioural difference
            # between the two implementations; aligning to the live (safer)
            # behaviour, since declining to protect an unprotected position is
            # never the better choice.
            if (current_sl == 0 or new_sl < current_sl - step):
                logger.debug(f"Trail [{method}] SELL TP{tp_level}: SL {current_sl:.5f} -> {new_sl:.5f}")
                return new_sl

        return None

    def _fixed_pips_trail(self, direction: str, current_price: float, pip_value: float) -> float:
        """
        Method 1: Trail by fixed number of pips behind current price.
        Source: RiskManagement_Spec.md Section 3.3 — Method 1
        """
        from backend.risk.multi_tp import _is_buy
        trail_distance = self.trail_pips * pip_value
        if _is_buy(direction):
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
        from backend.risk.multi_tp import _is_buy
        if _is_buy(direction):
            return highest - trail_distance
        else:
            return lowest + trail_distance

    def _structure_trail(self, direction: str, swing_points: list[dict[str, Any]] | None, atr: float) -> float | None:
        """
        Method 3: Trail to each confirmed swing point (SMC-native).
        SL moves to below confirmed Higher Low (BUY) or above Lower High (SELL) plus an ATR safety buffer.
        Source: RiskManagement_Spec.md Section 3.3 — Method 3
        """
        if not swing_points:
            # Fallback: Do not move SL if no structure exists
            return None
            
        atr_buffer = atr * 0.5  # Standard 0.5 ATR buffer behind structural liquidity

        from backend.risk.multi_tp import _is_buy
        if _is_buy(direction):
            # Trail to below the highest confirmed Higher Low.
            # Sort ascending so lows[-1] is the highest low — most recent structural support.
            lows = sorted(
                [s["price"] for s in swing_points if s["type"] == "LOW"]
            )
            if lows:
                return lows[-1] - atr_buffer  # Highest confirmed low - buffer
        else:
            # Trail to above the lowest confirmed Lower High.
            # Sort descending so highs[-1] is the lowest high — most recent structural resistance.
            highs = sorted(
                [s["price"] for s in swing_points if s["type"] == "HIGH"],
                reverse=True
            )
            if highs:
                return highs[-1] + atr_buffer  # Lowest confirmed high + buffer

        return None

    def _pct_trail(self, direction: str, current_price: float) -> float:
        """
        Method 4: Trail by percentage of current price.
        Source: RiskManagement_Spec.md Section 3.3 — Method 4
        """
        trail_distance = current_price * self.trail_pct
        from backend.risk.multi_tp import _is_buy
        if _is_buy(direction):
            return current_price - trail_distance
        else:
            return current_price + trail_distance
