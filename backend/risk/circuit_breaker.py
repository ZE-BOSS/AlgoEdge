"""
backend/risk/circuit_breaker.py

Portfolio-level risk guards: daily/weekly consecutive loss limits, streak, max positions.
Source: RiskManagement_Spec.md Section 6

Refactored: Replaced percentage-based daily/weekly drawdown with trade-count-based
consecutive loss limits per user request.
"""

from typing import Dict, Any, Optional, Tuple
from datetime import datetime, timezone
from backend.utils.logger import get_logger

logger = get_logger(__name__)


class CircuitBreaker:
    """
    Portfolio-level risk guards that pause trading when limits are hit.
    Uses trade-count-based consecutive loss limits (not percentage drawdown).
    Source: RiskManagement_Spec.md Section 6.1–6.5
    """

    def __init__(self, config: Dict[str, Any]):
        # Trade-count-based limits (replaces percentage-based)
        self.max_daily_consecutive_losses = config.get("max_daily_consecutive_losses", 5)
        self.max_weekly_consecutive_losses = config.get("max_weekly_consecutive_losses", 15)
        self.max_concurrent_positions = config.get("max_concurrent_positions", 3)
        self.max_correlated_risk_pct = config.get("max_correlated_risk_pct", 4.0)
        self.max_daily_trades = config.get("max_daily_trades", 5)
        self.target_profit_enabled = config.get("target_profit_enabled", False)
        self.max_daily_profit = config.get("max_daily_profit", 500.0)
        self.max_weekly_profit = config.get("max_weekly_profit", 2000.0)

        # State
        self.daily_consecutive_losses = 0
        self.weekly_consecutive_losses = 0
        self.daily_trades_count = 0
        self.daily_pnl = 0.0   # Still tracked for reporting, not for gating
        self.weekly_pnl = 0.0  # Still tracked for reporting, not for gating
        self.open_positions = 0
        self.open_positions_by_symbol: Dict[str, int] = {}  # symbol → count of active groups
        self.is_paused = False
        self.pause_reason = ""
        self.last_reset_day = datetime.now(timezone.utc).date()
        self.last_reset_week = datetime.now(timezone.utc).isocalendar()[1]
        self.last_trade_closed_m15_time: Optional[int] = None
        
        # Track active grouped trades (signal)
        self.active_groups = {} # group_id -> {"pnl": 0.0, "sub_trades": 0}


    def check_all(self, account_balance: float, current_time: Optional[datetime] = None) -> Tuple[bool, str]:
        """Run all active circuit breaker checks."""
        self._check_daily_reset(current_time)
        self._check_weekly_reset(current_time)

        if self.is_paused:
            return False, self.pause_reason

        # 1. M15 Cooldown Check
        if self.last_trade_closed_m15_time is not None and current_time is not None:
            current_epoch = int(current_time.timestamp()) if hasattr(current_time, 'timestamp') else float(current_time)
            current_m15 = (int(current_epoch) // 900) * 900
            if current_m15 <= self.last_trade_closed_m15_time:
                return False, f"M15 Cooldown Active: Waiting for current M15 candle to close"

        # 2. Max daily trades
        if self.max_daily_trades > 0 and self.daily_trades_count >= self.max_daily_trades:
            self.is_paused = True
            self.pause_reason = f"Daily max trades limit reached ({self.daily_trades_count}/{self.max_daily_trades})"
            return False, self.pause_reason

        # 3. Max open positions (total across symbols)
        total_open = sum(self.open_positions_by_symbol.values())
        if total_open >= self.max_concurrent_positions:
            return False, f"Max open positions reached ({total_open}/{self.max_concurrent_positions})"

        # 4. Daily consecutive losses
        if self.max_daily_consecutive_losses > 0 and self.daily_consecutive_losses >= self.max_daily_consecutive_losses:
            self.is_paused = True
            self.pause_reason = f"Daily consecutive loss limit reached: {self.daily_consecutive_losses}/{self.max_daily_consecutive_losses}"
            return False, self.pause_reason
            
        # 5. Weekly consecutive losses
        if self.max_weekly_consecutive_losses > 0 and self.weekly_consecutive_losses >= self.max_weekly_consecutive_losses:
            self.is_paused = True
            self.pause_reason = f"Weekly consecutive loss limit reached: {self.weekly_consecutive_losses}/{self.max_weekly_consecutive_losses}"
            return False, self.pause_reason

        # 6. Target Profit
        if self.target_profit_enabled:
            if self.daily_pnl >= self.max_daily_profit:
                self.is_paused = True
                self.pause_reason = f"Daily profit target reached: ${self.daily_pnl:.2f} / ${self.max_daily_profit:.2f}"
                return False, self.pause_reason
            
            if self.weekly_pnl >= self.max_weekly_profit:
                self.is_paused = True
                self.pause_reason = f"Weekly profit target reached: ${self.weekly_pnl:.2f} / ${self.max_weekly_profit:.2f}"
                return False, self.pause_reason

        return True, "OK"

    def check_symbol(self, symbol: str) -> Tuple[bool, str]:
        """Check if a new position can be opened on the given symbol.
        Enforces one active signal group per symbol at a time.
        """
        sym_open = self.open_positions_by_symbol.get(symbol, 0)
        if sym_open >= 1:
            return False, f"Already have an open position on {symbol} — waiting for it to close"
        return True, "OK"

    def position_opened(self, group_id: str, sub_trade_count: int, symbol: str = ""):
        """Track a new position opening."""
        self.open_positions += 1
        self.daily_trades_count += 1
        self.active_groups[group_id] = {"pnl": 0.0, "sub_trades": sub_trade_count, "symbol": symbol}
        if symbol:
            self.open_positions_by_symbol[symbol] = self.open_positions_by_symbol.get(symbol, 0) + 1

    def position_closed(self, group_id: str, pnl: float, current_time: Optional[datetime] = None):
        """Track a position closing."""
        if group_id in self.active_groups:
            self.active_groups[group_id]["pnl"] += pnl
            self.active_groups[group_id]["sub_trades"] -= 1
            
            # If all sub-trades for this group are closed
            if self.active_groups[group_id]["sub_trades"] <= 0:
                final_pnl = self.active_groups[group_id]["pnl"]
                self._record_trade_result(final_pnl, final_pnl >= 0)
                
                # Set M15 cooldown
                if current_time is not None:
                    current_epoch = int(current_time.timestamp()) if hasattr(current_time, 'timestamp') else float(current_time)
                    self.last_trade_closed_m15_time = (int(current_epoch) // 900) * 900
                
                sym = self.active_groups[group_id].get("symbol", "")
                del self.active_groups[group_id]
                self.open_positions = max(0, self.open_positions - 1)
                if sym and sym in self.open_positions_by_symbol:
                    self.open_positions_by_symbol[sym] = max(0, self.open_positions_by_symbol[sym] - 1)
                    if self.open_positions_by_symbol[sym] == 0:
                        del self.open_positions_by_symbol[sym]

    def _record_trade_result(self, pnl: float, is_win: bool):
        """Update state after a grouped trade fully closes."""
        self.daily_pnl += pnl
        self.weekly_pnl += pnl

        if is_win:
            self.daily_consecutive_losses = 0
            self.weekly_consecutive_losses = 0
        else:
            self.daily_consecutive_losses += 1
            self.weekly_consecutive_losses += 1

    def manual_resume(self):
        """User manually re-enables trading after a streak pause."""
        self.is_paused = False
        self.pause_reason = ""
        self.daily_consecutive_losses = 0
        self.weekly_consecutive_losses = 0
        self.last_trade_closed_m15_time = None
        logger.info("Circuit breaker manually resumed by user")

    def _parse_timestamp_date(self, ts) -> Tuple[datetime.date, int]:
        """Safely parse either seconds or milliseconds epoch into date and week."""
        try:
            val = float(ts)
            # If greater than year 5138, assume it's in milliseconds
            if val > 1e11:
                val = val / 1000.0
            dt = datetime.fromtimestamp(val, timezone.utc)
            return dt.date(), dt.isocalendar()[1]
        except Exception:
            dt = datetime.now(timezone.utc)
            return dt.date(), dt.isocalendar()[1]

    def _check_daily_reset(self, current_time: Optional[datetime] = None):
        """Reset daily counters at midnight."""
        now = current_time if current_time is not None else datetime.now(timezone.utc)
        if hasattr(now, "date"):
            today = now.date()
        else:
            today, _ = self._parse_timestamp_date(now)
                
        if today != self.last_reset_day:
            self.daily_pnl = 0.0
            self.daily_consecutive_losses = 0
            self.daily_trades_count = 0
            self.last_reset_day = today
            # If paused due to daily limits, auto-resume on new day
            if self.is_paused and "daily" in self.pause_reason.lower():
                self.manual_resume()
            # Un-pause if it was a daily limit pause
            if "Daily" in self.pause_reason:
                self.is_paused = False
                self.pause_reason = ""

    def _check_weekly_reset(self, current_time: Optional[datetime] = None):
        """Reset weekly counters on Monday."""
        now = current_time if current_time is not None else datetime.now(timezone.utc)
        if hasattr(now, "isocalendar"):
            current_week = now.isocalendar()[1]
        else:
            _, current_week = self._parse_timestamp_date(now)
                
        if current_week != self.last_reset_week:
            self.weekly_pnl = 0.0
            self.weekly_consecutive_losses = 0
            self.last_reset_week = current_week
            if "Weekly" in self.pause_reason:
                self.is_paused = False
                self.pause_reason = ""
