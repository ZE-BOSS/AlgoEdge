"""
backend/risk/circuit_breaker.py

Portfolio-level risk guards: daily loss, weekly loss, streak, max positions.
Source: RiskManagement_Spec.md Section 6
"""

from typing import Dict, Any, Optional
from datetime import datetime, timezone
from backend.utils.logger import get_logger

logger = get_logger(__name__)


class CircuitBreaker:
    """
    Portfolio-level risk guards that pause trading when limits are hit.
    Source: RiskManagement_Spec.md Section 6.1–6.5
    """

    def __init__(self, config: Dict[str, Any]):
        self.max_daily_loss_pct = config.get("max_daily_loss_pct", 5.0)
        self.max_weekly_loss_pct = config.get("max_weekly_loss_pct", 10.0)
        self.max_consecutive_losses = config.get("max_consecutive_losses", 5)
        self.max_concurrent_positions = config.get("max_concurrent_positions", 3)
        self.max_correlated_risk_pct = config.get("max_correlated_risk_pct", 4.0)

        # State
        self.daily_pnl = 0.0
        self.weekly_pnl = 0.0
        self.consecutive_losses = 0
        self.open_positions = 0
        self.is_paused = False
        self.pause_reason = ""
        self.last_reset_day = datetime.now(timezone.utc).date()
        self.last_reset_week = datetime.now(timezone.utc).isocalendar()[1]

    def check_all(self, account_balance: float) -> tuple[bool, str]:
        """
        Run all circuit breaker checks.
        Returns (can_trade, reason).
        """
        self._check_daily_reset()
        self._check_weekly_reset()

        # Guard 1: Daily loss limit
        if account_balance > 0:
            daily_loss_limit = account_balance * (self.max_daily_loss_pct / 100)
            if self.daily_pnl <= -daily_loss_limit:
                self.is_paused = True
                self.pause_reason = f"Daily loss limit reached: ${abs(self.daily_pnl):.2f} (limit ${daily_loss_limit:.2f})"
                return False, self.pause_reason

        # Guard 2: Weekly drawdown
        if account_balance > 0:
            weekly_loss_limit = account_balance * (self.max_weekly_loss_pct / 100)
            if self.weekly_pnl <= -weekly_loss_limit:
                self.is_paused = True
                self.pause_reason = f"Weekly drawdown limit reached: ${abs(self.weekly_pnl):.2f}"
                return False, self.pause_reason

        # Guard 3: Consecutive loss streak
        if self.consecutive_losses >= self.max_consecutive_losses:
            self.is_paused = True
            self.pause_reason = f"{self.consecutive_losses} consecutive losses — manual re-enable required"
            return False, self.pause_reason

        # Guard 4: Max concurrent positions
        if self.open_positions >= self.max_concurrent_positions:
            return False, f"Max positions reached: {self.open_positions}/{self.max_concurrent_positions}"

        return True, "OK"

    def record_trade_result(self, pnl: float, is_win: bool):
        """Update state after a trade closes."""
        self.daily_pnl += pnl
        self.weekly_pnl += pnl

        if is_win:
            self.consecutive_losses = 0
        else:
            self.consecutive_losses += 1

    def position_opened(self):
        """Track a new position opening."""
        self.open_positions += 1

    def position_closed(self):
        """Track a position closing."""
        self.open_positions = max(0, self.open_positions - 1)

    def manual_resume(self):
        """User manually re-enables trading after a streak pause."""
        self.is_paused = False
        self.pause_reason = ""
        self.consecutive_losses = 0
        logger.info("Circuit breaker manually resumed by user")

    def _check_daily_reset(self):
        """Reset daily P&L counter at midnight UTC."""
        today = datetime.now(timezone.utc).date()
        if today != self.last_reset_day:
            self.daily_pnl = 0.0
            self.last_reset_day = today
            # Un-pause if it was a daily limit pause
            if "Daily" in self.pause_reason:
                self.is_paused = False
                self.pause_reason = ""

    def _check_weekly_reset(self):
        """Reset weekly P&L counter on Monday."""
        current_week = datetime.now(timezone.utc).isocalendar()[1]
        if current_week != self.last_reset_week:
            self.weekly_pnl = 0.0
            self.last_reset_week = current_week
            if "Weekly" in self.pause_reason:
                self.is_paused = False
                self.pause_reason = ""
