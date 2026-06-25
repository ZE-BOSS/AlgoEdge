"""
backend/risk/engine.py

Main Risk Engine Orchestrator.
Uses all sub-components to validate, size, and manage trades.
Identical logic used in both live trading and backtesting.
Source: RiskManagement_Spec.md
"""

from typing import Dict, Any, List, Tuple, Optional
from backend.risk.position_sizer import calculate_lot_size, calculate_lot_from_dollars, get_pip_size
from backend.risk.multi_tp import MultiTPManager, TPLevel
from backend.risk.breakeven_manager import BreakevenManager
from backend.risk.trailing_manager import TrailingManager
from backend.risk.circuit_breaker import CircuitBreaker
from backend.utils.logger import get_logger

logger = get_logger(__name__)


class RiskEngine:
    """
    Orchestrates all risk logic for live and backtesting.
    What you backtest = what runs live.
    Source: RiskManagement_Spec.md Section 7
    """

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.multi_tp = MultiTPManager(config)
        self.breakeven = BreakevenManager(config)
        self.trailing = TrailingManager(config)
        self.circuit = CircuitBreaker(config)

        # Risk params
        self.risk_pct = config.get("risk_per_trade_pct", 1.0)
        self.min_rr = config.get("min_rr", 3.0)
        self.sl_buffer_pips = config.get("sl_buffer_pips", 5.0)
        self.compounding_enabled = config.get("compounding_enabled", False)

    def evaluate_signal(
        self,
        signal_data: Dict[str, Any],
        account_balance: float,
        compounding_risk_dollars: float = 0.0,
    ) -> Tuple[bool, str, List[TPLevel]]:
        """
        Evaluate if a signal is safe to trade, and if so, calculate sizes and TPs.
        Returns (is_approved, reason, tp_levels).
        """
        symbol = signal_data.get("symbol", "")
        direction = signal_data.get("direction", "")
        entry = signal_data.get("entry_price", 0.0)
        sl = signal_data.get("stop_loss", 0.0)

        # 1. Circuit Breaker Check
        can_trade, reason = self.circuit.check_all(account_balance)
        if not can_trade:
            logger.warning(f"[RISK] Circuit breaker blocked: {reason}")
            return False, reason, []

        # 2. Minimum RR Check
        risk = abs(entry - sl)
        if risk == 0:
            logger.warning(f"[RISK] Rejected: zero risk (entry={entry:.5f}, SL={sl:.5f})")
            return False, "Risk is zero (entry == SL)", []

        # 3. Position Sizing
        if self.compounding_enabled and compounding_risk_dollars > 0:
            total_lots = calculate_lot_from_dollars(
                compounding_risk_dollars, entry, sl, symbol
            )
        else:
            total_lots = calculate_lot_size(
                account_balance, self.risk_pct, entry, sl, symbol
            )

        if total_lots < 0.01:
            logger.warning(f"[RISK] Rejected: lot size {total_lots:.4f} below 0.01 min (balance=${account_balance:.2f}, risk_pct={self.risk_pct}%, risk={risk:.5f})")
            return False, "Lot size too small for broker minimum", []

        # 4. Multi-Position Splits (TP1/TP2/TP3)
        liquidity_target = signal_data.get("liquidity_target")
        tp_levels = self.multi_tp.calculate_tp_levels(
            entry, sl, direction, total_lots, symbol, liquidity_target
        )

        if not tp_levels:
            return False, "No valid TP levels calculated", []

        # 5. Validate minimum RR on TP1
        tp1_price = tp_levels[0].tp_price
        tp1_reward = abs(tp1_price - entry)
        tp1_rr = tp1_reward / risk
        if tp1_rr < self.min_rr:
            logger.warning(f"[RISK] Rejected: TP1 RR {tp1_rr:.1f} < min_rr {self.min_rr} (entry={entry:.5f}, SL={sl:.5f}, TP1={tp1_price:.5f})")
            return False, f"TP1 RR {tp1_rr:.1f} below minimum {self.min_rr}", []

        self.circuit.position_opened()
        logger.info(f"[RISK] ✅ Trade approved: {direction} {symbol} @ {entry:.5f} | SL={sl:.5f} | risk={risk:.5f} | TP1_RR={tp1_rr:.1f} | {len(tp_levels)} TPs | Lots={total_lots:.4f}")
        return True, "APPROVED", tp_levels

    def manage_open_position(
        self,
        position: Dict[str, Any],
        current_price: float,
        atr_value: float = 0.0,
        swing_points: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Evaluate BE and trailing stops for an open position.
        Returns list of required actions.
        Source: RiskManagement_Spec.md Section 3.4 — Trailing Stop State Machine
        """
        actions = []
        direction = position.get("direction", "BUY")
        entry = position.get("entry_price", 0.0)
        current_sl = position.get("stop_loss", 0.0)
        original_sl = position.get("original_sl", current_sl)
        pip_value = get_pip_size(position.get("symbol", ""))
        tp_level = position.get("tp_level", 1)
        be_applied = position.get("be_applied", False)
        tp1_hit = position.get("tp1_hit", False)
        trail_method = position.get("trail_method")
        highest = position.get("highest_price", current_price)
        lowest = position.get("lowest_price", current_price)

        # Update highest/lowest price tracking
        if direction == "BUY":
            if current_price > highest:
                actions.append({"action": "UPDATE_HIGHEST", "price": current_price})
        else:
            if current_price < lowest:
                actions.append({"action": "UPDATE_LOWEST", "price": current_price})

        # Step 1: Check Break-Even (if not already applied)
        if not be_applied:
            new_sl = self.breakeven.check_breakeven(
                entry_price=entry,
                current_price=current_price,
                current_sl=current_sl,
                stop_loss=original_sl,
                direction=direction,
                pip_value=pip_value,
                be_already_applied=be_applied,
                tp1_hit=tp1_hit,
            )
            if new_sl is not None:
                actions.append({"action": "MODIFY_SL", "new_sl": new_sl, "reason": "BREAKEVEN"})
                return actions  # BE takes priority on this tick

        # Step 2: Check Trailing (only if trail method is assigned)
        if trail_method and be_applied:
            new_sl = self.trailing.calculate_trailing_sl(
                method=trail_method,
                direction=direction,
                current_price=current_price,
                current_sl=current_sl,
                pip_value=pip_value,
                highest_price=highest,
                lowest_price=lowest,
                atr_value=atr_value,
                swing_points=swing_points,
            )
            if new_sl is not None:
                actions.append({"action": "MODIFY_SL", "new_sl": new_sl, "reason": "TRAIL"})

        return actions

    def on_position_closed(self, pnl: float, is_win: bool):
        """Update circuit breaker state after a position closes."""
        self.circuit.record_trade_result(pnl, is_win)
        self.circuit.position_closed()
