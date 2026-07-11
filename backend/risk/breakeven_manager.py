"""
backend/risk/breakeven_manager.py

Break-even system with configurable trigger and buffer.
Source: RiskManagement_Spec.md Section 3.2
"""

from typing import Optional, Dict, Any
from backend.utils.logger import get_logger

logger = get_logger(__name__)


class BreakevenManager:
    """Manages break-even SL adjustments for open positions."""

    def __init__(self, config: Dict[str, Any]):
        self.be_trigger_rr = config.get("be_trigger_rr", 1.0)
        # be_buffer_pips is repurposed as atr multiplier for safety buffer (backward compatible)
        self.be_atr_multiplier = config.get("be_buffer_atr_mult", config.get("be_buffer_pips", 0.1))
        self.be_on_tp1_hit = config.get("be_on_tp1_hit", True)

    def check_breakeven(
        self,
        entry_price: float,
        current_price: float,
        current_sl: float,
        stop_loss: float,
        direction: str,
        pip_value: float,
        live_spread: float,
        atr: float,
        be_already_applied: bool = False,
        tp1_hit: bool = False,
    ) -> Optional[float]:
        """
        Returns the new SL price if break-even should be triggered, else None.
        SL only moves in the profitable direction — never backward.
        Source: RiskManagement_Spec.md Section 3.2
        """
        if be_already_applied:
            return None

        risk = abs(entry_price - stop_loss)
        if risk == 0:
            return None

        # Calculate current R-multiple in profit
        if direction == "BUY":
            unrealized_r = (current_price - entry_price) / risk
        else:
            unrealized_r = (entry_price - current_price) / risk

        # Check trigger conditions
        should_trigger = False

        # Trigger 1: Reached be_trigger_rr in profit
        if unrealized_r >= self.be_trigger_rr:
            should_trigger = True

        # Trigger 2: TP1 was hit (if be_on_tp1_hit is enabled)
        if tp1_hit and self.be_on_tp1_hit:
            should_trigger = True

        if not should_trigger:
            return None

        # Calculate new SL at entry + max(live_spread, atr_buffer)
        atr_buffer = self.be_atr_multiplier * atr
        
        if live_spread >= atr_buffer:
            buffer = live_spread
            winner = "live_spread"
        else:
            buffer = atr_buffer
            winner = "atr_buffer"
            
        logger.debug(f"[BREAKEVEN] Buffer logic | live_spread: {live_spread:.5f} | atr_buffer: {atr_buffer:.5f} | Winner: {winner}")
        
        if direction == "BUY":
            new_sl = entry_price + buffer
            # Only move SL in favorable direction
            if new_sl <= current_sl:
                return None
        else:
            new_sl = entry_price - buffer
            if new_sl >= current_sl:
                return None

        logger.info(f"Break-even triggered: SL {current_sl:.5f} -> {new_sl:.5f}")
        return new_sl
