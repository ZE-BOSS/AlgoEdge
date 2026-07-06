"""
backend/risk/engine.py

Main Risk Engine Orchestrator.
Uses all sub-components to validate, size, and manage trades.
Identical logic used in both live trading and backtesting.
Source: RiskManagement_Spec.md
"""

from typing import Dict, Any, List, Tuple, Optional
from datetime import datetime
from backend.risk.position_sizer import calculate_lot_size, calculate_lot_from_dollars, get_pip_size, calculate_risk_dollars
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
        current_time: Optional[datetime] = None,
    ) -> Tuple[bool, str, List[Dict[str, Any]]]:
        """
        Evaluate if a signal is safe to trade, and if so, calculate sizes and TPs.
        Returns (is_approved, reason, tp_levels).
        """
        symbol = signal_data.get("symbol", "")
        direction = signal_data.get("direction", "")
        entry = signal_data.get("entry_price", 0.0)
        sl = signal_data.get("stop_loss", 0.0)

        # 1. Circuit Breaker Check
        can_trade, reason = self.circuit.check_all(account_balance, current_time)
        if not can_trade:
            logger.warning(f"[RISK] Circuit breaker blocked: {reason}")
            return False, reason, []

        # 2. Minimum RR Check and Direction Validation
        from backend.risk.multi_tp import _is_buy
        is_buy = _is_buy(direction)
        
        if is_buy and sl >= entry:
            logger.warning(f"[RISK] Rejected: BUY SL ({sl:.5f}) >= entry ({entry:.5f})")
            return False, "BUY Stop Loss must be below entry", []
            
        if not is_buy and sl <= entry:
            logger.warning(f"[RISK] Rejected: SELL SL ({sl:.5f}) <= entry ({entry:.5f})")
            return False, "SELL Stop Loss must be above entry", []
            
        risk = abs(entry - sl)
        if risk == 0:
            logger.warning(f"[RISK] Rejected: zero risk (entry={entry:.5f}, SL={sl:.5f})")
            return False, "Risk is zero (entry == SL)", []

        # 3. Position Sizing
        if self.compounding_enabled and compounding_risk_dollars > 0:
            requested_risk_dollars = compounding_risk_dollars
            total_lots = calculate_lot_from_dollars(
                requested_risk_dollars, entry, sl, symbol
            )
        else:
            requested_risk_dollars = account_balance * (self.risk_pct / 100.0)
            total_lots = calculate_lot_size(
                account_balance, self.risk_pct, entry, sl, symbol
            )

        # Calculate actual dollar risk taken (after any Smart Clamping in the position sizer)
        actual_risk_dollars = calculate_risk_dollars(total_lots, entry, sl, symbol)
        
        # Strict Risk Enforcement
        # If the minimum broker lot size forces us to risk more than what was requested, REJECT outright.
        # Adding a tiny 1% leniency buffer to account for MT5 precision floating point rounding.
        if actual_risk_dollars > (requested_risk_dollars * 1.01):
            logger.warning(
                f"[RISK] Rejected: Broker minimum lot forces risk of ${actual_risk_dollars:.2f} "
                f"which exceeds the requested risk of ${requested_risk_dollars:.2f}."
            )
            return False, f"Proposed risk (${requested_risk_dollars:.2f}) does not meet the broker minimum requirement (${actual_risk_dollars:.2f}) based on your capital. Please increase your risk percentage or capital to trade this setup.", []

        if total_lots == 0.0:
            logger.warning(f"[RISK] Rejected: lot size 0 (balance=${account_balance:.2f})")
            return False, "Lot size calculation returned 0", []

        # 4. Multi-Position Splits (TP1/TP2/TP3)
        liquidity_target = signal_data.get("liquidity_target")
        tp_levels = self.multi_tp.calculate_tp_levels(
            entry, sl, direction, total_lots, symbol, liquidity_target
        )

        if not tp_levels:
            return False, "No valid TP levels calculated", []

        # 5. Validate minimum RR on last TP
        last_tp_price = tp_levels[-1].tp_price
        last_tp_reward = abs(last_tp_price - entry)
        last_tp_rr = last_tp_reward / risk
        if last_tp_rr < self.min_rr:
            logger.warning(f"[RISK] Rejected: Last TP RR {last_tp_rr:.1f} < min_rr {self.min_rr} (entry={entry:.5f}, SL={sl:.5f}, TP={last_tp_price:.5f})")
            return False, f"Last TP RR {last_tp_rr:.1f} below minimum {self.min_rr}", []

        group_id = signal_data.get("group_id", "unknown")
        self.circuit.position_opened(group_id, len(tp_levels), symbol=symbol)
        logger.info(f"[RISK] ✅ Trade approved: {direction} {symbol} @ {entry:.5f} | SL={sl:.5f} | risk={risk:.5f} | Last_TP_RR={last_tp_rr:.1f} | {len(tp_levels)} TPs | Lots={total_lots:.4f}")
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
                live_spread=position.get("live_spread", pip_value),
                atr=atr_value if atr_value > 0 else abs(entry - original_sl) * 0.1,
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

    def on_position_closed(self, group_id: str, pnl: float, current_time: Optional[datetime] = None):
        """Update circuit breaker state after a position closes."""
        self.circuit.position_closed(group_id, pnl, current_time)
