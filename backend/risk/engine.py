"""
backend/risk/engine.py

Main Risk Engine Orchestrator.
Uses all sub-components to validate, size, and manage trades.
Identical logic used in both live trading and backtesting.
Source: RiskManagement_Spec.md
"""

import json
from datetime import datetime
from typing import Any

from backend.risk.breakeven_manager import BreakevenManager
from backend.risk.circuit_breaker import CircuitBreaker
from backend.risk.multi_tp import MultiTPManager
from backend.risk.position_sizer import (
    calculate_lot_size,
    calculate_risk_dollars,
    get_pip_size,
)
from backend.risk.trailing_manager import TrailingManager
from backend.utils.logger import get_logger

logger = get_logger(__name__)


class RiskEngine:
    """
    Orchestrates all risk logic for live and backtesting.
    What you backtest = what runs live.
    Source: RiskManagement_Spec.md Section 7
    """

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.multi_tp = MultiTPManager(config)
        self.breakeven = BreakevenManager(config)
        self.trailing = TrailingManager(config)
        self.is_backtesting = config.get("is_backtest", False)
        self.circuit = CircuitBreaker(config, is_backtest=self.is_backtesting)

        # Risk params
        self.risk_pct = config.get("risk_per_trade_pct", 1.0)
        self.min_rr = config.get("min_rr", 3.0)
        self.sl_buffer_pips = config.get("sl_buffer_pips", 5.0)
        self.compounding_enabled = config.get("compounding_enabled", False)
        # is_backtesting is kept for informational purposes / future guards.
        # Both live and backtest modes use MT5 data when available, with
        # InstrumentProfile as fallback — so use_live_mt5 is always True.
        # self.is_backtesting is now populated earlier

    def evaluate_signal(
        self,
        signal_data: dict[str, Any],
        account_balance: float,
        current_time: datetime | None = None,
        initial_balance: float | None = None,
    ) -> tuple[bool, str, list[Any]]:
        """
        Evaluate if a signal is safe to trade, and if so, calculate sizes and TPs.
        Returns (is_approved, reason, tp_levels).
        """
        symbol = signal_data.get("symbol", "")
        direction = signal_data.get("direction", "")
        entry = signal_data.get("entry_price", 0.0)
        sl = signal_data.get("stop_loss", 0.0)

        base_balance = initial_balance if initial_balance is not None else account_balance

        # Extract timeframe from signal metadata if available, else default to M15.
        timeframe = signal_data.get("metadata", {}).get("timeframe", "M15")

        cb_ok, cb_reason = self.circuit.check_symbol(symbol, timeframe, current_time)
        if not cb_ok:
            logger.warning(json.dumps({
                "event": "risk_rejected",
                "reason": "circuit_breaker_symbol",
                "details": cb_reason
            }))
            return False, cb_reason, []
            
        can_trade, reason = self.circuit.check_all(base_balance, current_time)
        if not can_trade:
            logger.warning(json.dumps({
                "event": "risk_rejected",
                "reason": "circuit_breaker_blocked",
                "details": reason
            }))
            return False, reason, []

        # 1b. Prop Firm drawdown hard block (only when account_mode = 'prop_firm')
        if hasattr(self, "prop_firm_validator") and self.prop_firm_validator and self.prop_firm_validator.enabled:
            pf_blocked, pf_reason = self.prop_firm_validator.should_block_trading()
            if pf_blocked:
                logger.warning(json.dumps({
                    "event": "risk_rejected",
                    "reason": "prop_firm_drawdown_block",
                    "details": pf_reason
                }))
                return False, pf_reason, []

        # 2. Minimum RR Check and Direction Validation
        from backend.risk.multi_tp import _is_buy
        is_buy = _is_buy(direction)
        
        if is_buy and sl >= entry:
            logger.warning(json.dumps({
                "event": "risk_rejected",
                "reason": "invalid_sl",
                "direction": "BUY",
                "entry": entry,
                "sl": sl
            }))
            return False, "BUY Stop Loss must be below entry", []
            
        if not is_buy and sl <= entry:
            logger.warning(json.dumps({
                "event": "risk_rejected",
                "reason": "invalid_sl",
                "direction": "SELL",
                "entry": entry,
                "sl": sl
            }))
            return False, "SELL Stop Loss must be above entry", []
            
        risk = abs(entry - sl)
        if risk == 0:
            logger.warning(json.dumps({
                "event": "risk_rejected",
                "reason": "zero_risk",
                "entry": entry,
                "sl": sl
            }))
            return False, "Risk is zero (entry == SL)", []

        # 3. Position Sizing
        # IMPORTANT: We have completely removed compounding per user request.
        # Position sizing MUST always use the static `initial_balance` (e.g. $25,000)
        # so that risk size does not balloon as the account grows.
        size_modifier = signal_data.get("metadata", {}).get("size_modifier", 1.0)
        
        # Both live and backtest use MT5 data when available → InstrumentProfile fallback.
        # This matches how _calc_pnl() works (MT5 first via get_symbol_info).

        requested_risk_dollars = base_balance * (self.risk_pct / 100.0) * size_modifier

        # Predictive Drawdown Guard - Dynamic Scaling
        max_daily_dd = self.circuit.max_daily_drawdown_pct
        max_weekly_dd = self.circuit.max_weekly_drawdown_pct
        
        if max_daily_dd > 0 and base_balance > 0:
            open_risk = getattr(self.circuit, "get_open_risk", lambda: 0.0)()
            max_loss_dollars = base_balance * (max_daily_dd / 100.0)
            already_lost_dollars = -self.circuit.daily_pnl + open_risk
            remaining_daily_risk = max_loss_dollars - already_lost_dollars
            
            if remaining_daily_risk <= 0:
                logger.warning(json.dumps({"event": "risk_rejected", "reason": "daily_drawdown_exhausted"}))
                return False, f"Daily drawdown limit of {max_daily_dd}% is fully exhausted.", []
            
            if remaining_daily_risk < requested_risk_dollars:
                logger.info(f"Scaling down risk from ${requested_risk_dollars:.2f} to ${remaining_daily_risk:.2f} to honor {max_daily_dd}% daily drawdown.")
                requested_risk_dollars = remaining_daily_risk
                
        if max_weekly_dd > 0 and base_balance > 0:
            open_risk = getattr(self.circuit, "get_open_risk", lambda: 0.0)()
            max_weekly_loss_dollars = base_balance * (max_weekly_dd / 100.0)
            already_lost_weekly = -self.circuit.weekly_pnl + open_risk
            remaining_weekly_risk = max_weekly_loss_dollars - already_lost_weekly
            
            if remaining_weekly_risk <= 0:
                logger.warning(json.dumps({"event": "risk_rejected", "reason": "weekly_drawdown_exhausted"}))
                return False, f"Weekly drawdown limit of {max_weekly_dd}% is fully exhausted.", []
                
            if remaining_weekly_risk < requested_risk_dollars:
                logger.info(f"Scaling down risk from ${requested_risk_dollars:.2f} to ${remaining_weekly_risk:.2f} to honor {max_weekly_dd}% weekly drawdown.")
                requested_risk_dollars = remaining_weekly_risk

        # Calculate lot size based on (potentially scaled down) requested_risk_dollars
        max_risk_hard_cap_pct_val = self.config.get("max_risk_hard_cap_pct", 3.0)
        
        # Determine the effective risk percentage for the sizing function
        effective_risk_pct = (requested_risk_dollars / base_balance) * 100.0
        
        total_lots = calculate_lot_size(
            base_balance, effective_risk_pct, entry, sl, symbol,
            max_risk_hard_cap_pct=max_risk_hard_cap_pct_val,
        )

        # Calculate actual dollar risk from the sizer output (before TP splits)
        pre_split_risk_dollars = calculate_risk_dollars(total_lots, entry, sl, symbol)

        if total_lots == 0.0:
            logger.warning(json.dumps({
                "event": "risk_rejected",
                "reason": "zero_lot_size",
                "balance": account_balance
            }))
            return False, "Lot size calculation returned 0", []

        # 3.5 Prop Firm lot validation (informational — logs if max_lots per symbol exceeded)
        if hasattr(self, "prop_firm_validator") and self.prop_firm_validator and self.prop_firm_validator.enabled:
            self.prop_firm_validator.validate_trade(symbol, total_lots)  # logs warnings only

        # 4. Multi-Position Splits (TP1/TP2/TP3)
        max_risk_cap_dollars = requested_risk_dollars * 1.05
        liquidity_target = signal_data.get("liquidity_target")
        strategy_id = signal_data.get("metadata", {}).get("strategy_id", "UNKNOWN")
        tp_levels = self.multi_tp.calculate_tp_levels(
            entry, sl, direction, total_lots, symbol, liquidity_target, strategy_id,
            max_risk_cap_dollars=max_risk_cap_dollars,
        )

        if not tp_levels:
            return False, "No valid TP levels calculated", []

        actual_total_lots = sum(tp.volume for tp in tp_levels)
        actual_risk_dollars = calculate_risk_dollars(actual_total_lots, entry, sl, symbol)

        # Soft warn if there is any residual overshoot vs. the pre-split calculation.
        # (Should be near-zero after multi_tp's cap enforcement, but log it for full auditability.)
        if actual_risk_dollars > (requested_risk_dollars * 1.01):
            logger.warning(json.dumps({
                "event": "risk_warning_post_split_overshoot",
                "requested_risk_dollars": round(requested_risk_dollars, 2),
                "actual_risk_dollars": round(actual_risk_dollars, 2),
                "actual_total_lots": actual_total_lots,
                "balance": account_balance,
                "note": "Residual overshoot after TP scaling — within tolerance"
            }))

        # 5. Validate minimum RR on last TP
        last_tp_price = tp_levels[-1].tp_price
        last_tp_reward = abs(last_tp_price - entry)
        last_tp_rr = last_tp_reward / risk
        if last_tp_rr < self.min_rr:
            logger.warning(json.dumps({
                "event": "risk_rejected",
                "reason": "insufficient_rr",
                "min_rr": self.min_rr,
                "actual_rr": round(last_tp_rr, 2),
                "entry": entry,
                "sl": sl,
                "last_tp": last_tp_price
            }))
            return False, f"Last TP RR {last_tp_rr:.1f} below minimum {self.min_rr}", []

        group_id = signal_data.get("group_id", "unknown")
        # Removed state modification: self.circuit.position_opened(group_id, len(tp_levels), symbol=symbol)
        # State tracking is now handled directly by the executing engine (live trading) to avoid ghost trades.
        
        if hasattr(self, "prop_firm_validator") and self.prop_firm_validator:
            self.prop_firm_validator.record_trade_opened(symbol, total_lots)
        
        logger.info(json.dumps({
            "event": "trade_approved",
            "direction": direction,
            "symbol": symbol,
            "entry": entry,
            "sl": sl,
            "risk_dollars": actual_risk_dollars,
            "lots": total_lots,
            "tp_count": len(tp_levels),
            "last_tp_rr": round(last_tp_rr, 2)
        }))
        return True, "APPROVED", tp_levels

    def manage_open_position(
        self,
        position: dict[str, Any],
        current_price: float,
        atr_value: float = 0.0,
        swing_points: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
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
        
        from backend.risk.multi_tp import _is_buy
        is_buy = _is_buy(direction)

        # Update highest/lowest price tracking
        if is_buy:
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
            # §2.3 fix: Check trail_activation_rr — trailing only activates after
            # price reaches this many R in profit. Matches position_manager live behavior.
            risk_distance = abs(entry - original_sl)
            if risk_distance > 0:
                if is_buy:
                    unrealized_r = (current_price - entry) / risk_distance
                else:
                    unrealized_r = (entry - current_price) / risk_distance
                
                trail_activation = self.config.get("trail_activation_rr", 1.0)
                if unrealized_r < trail_activation:
                    return actions  # Trailing not yet activated

            # §2.2 fix: pass tp_level for per-TP multiplier resolution
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
                tp_level=tp_level,
            )
            if new_sl is not None:
                actions.append({"action": "MODIFY_SL", "new_sl": new_sl, "reason": "TRAIL"})

        return actions

    def on_position_opened(self, group_id: str, sub_trade_count: int, symbol: str = ""):
        """Track a new position opening (unused in backtest)."""
        self.circuit.position_opened(group_id, sub_trade_count, symbol)

    def on_position_closed(self, group_id: str, pnl: float, current_time: datetime | None = None):
        """Update circuit breaker state after a position closes (unused in backtest)."""
        self.circuit.position_closed(group_id, pnl, current_time)

    def on_backtest_position_closed(self, group_id: str, pnl: float, current_time: datetime | None = None, symbol: str = "", lots: float = 0.0):
        """Feed closed trade PnL directly into Circuit Breaker during backtesting."""
        if hasattr(self.circuit, "record_backtest_close"):
            self.circuit.record_backtest_close(group_id, pnl, current_time)
            
        if hasattr(self, "prop_firm_validator") and self.prop_firm_validator:
            self.prop_firm_validator.record_trade_closed(symbol, lots, pnl)
