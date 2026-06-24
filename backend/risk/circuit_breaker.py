"""
backend/risk/circuit_breaker.py

Portfolio-level risk guards: daily/weekly consecutive loss limits, streak, max positions.
Source: RiskManagement_Spec.md Section 6

Refactored: Replaced percentage-based daily/weekly drawdown with trade-count-based
consecutive loss limits per user request.
"""

from typing import Dict, Any, Optional
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
        self.max_daily_consecutive_losses = config.get("max_daily_consecutive_losses", 3)
        self.max_weekly_consecutive_losses = config.get("max_weekly_consecutive_losses", 5)
        self.max_consecutive_losses = config.get("max_consecutive_losses", 5)
        self.max_concurrent_positions = config.get("max_concurrent_positions", 3)
        self.max_correlated_risk_pct = config.get("max_correlated_risk_pct", 4.0)

        # State
        self.daily_consecutive_losses = 0
        self.weekly_consecutive_losses = 0
        self.consecutive_losses = 0
        self.daily_pnl = 0.0   # Still tracked for reporting, not for gating
        self.weekly_pnl = 0.0  # Still tracked for reporting, not for gating
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

        # Guard 1: Daily consecutive losses
        if self.daily_consecutive_losses >= self.max_daily_consecutive_losses:
            self.is_paused = True
            self.pause_reason = (
                f"Daily consecutive loss limit reached: "
                f"{self.daily_consecutive_losses}/{self.max_daily_consecutive_losses}"
            )
            return False, self.pause_reason

        # Guard 2: Weekly consecutive losses
        if self.weekly_consecutive_losses >= self.max_weekly_consecutive_losses:
            self.is_paused = True
            self.pause_reason = (
                f"Weekly consecutive loss limit reached: "
                f"{self.weekly_consecutive_losses}/{self.max_weekly_consecutive_losses}"
            )
            return False, self.pause_reason

        # Guard 3: Overall consecutive loss streak
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
            self.daily_consecutive_losses = 0
            self.weekly_consecutive_losses = 0
        else:
            self.consecutive_losses += 1
            self.daily_consecutive_losses += 1
            self.weekly_consecutive_losses += 1

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
        self.daily_consecutive_losses = 0
        self.weekly_consecutive_losses = 0
        logger.info("Circuit breaker manually resumed by user")

    def _check_daily_reset(self):
        """Reset daily counters at midnight UTC."""
        today = datetime.now(timezone.utc).date()
        if today != self.last_reset_day:
            self.daily_pnl = 0.0
            self.daily_consecutive_losses = 0
            self.last_reset_day = today
            # Un-pause if it was a daily limit pause
            if "Daily" in self.pause_reason:
                self.is_paused = False
                self.pause_reason = ""

    def _check_weekly_reset(self):
        """Reset weekly counters on Monday."""
        current_week = datetime.now(timezone.utc).isocalendar()[1]
        if current_week != self.last_reset_week:
            self.weekly_pnl = 0.0
            self.weekly_consecutive_losses = 0
            self.last_reset_week = current_week
            if "Weekly" in self.pause_reason:
                self.is_paused = False
                self.pause_reason = ""
