"""
backend/risk/circuit_breaker.py

Portfolio-level risk guards: daily/weekly consecutive loss limits, streak, max positions.
Source: RiskManagement_Spec.md Section 6

Refactored: Replaced percentage-based daily/weekly drawdown with trade-count-based
consecutive loss limits per user request.
"""

import json
import os
from datetime import datetime, timezone
from typing import Any

from backend.utils.logger import get_logger

logger = get_logger(__name__)

CB_STATE_FILE = "backend/data/cb_state.json"


class CircuitBreaker:
    """
    Portfolio-level risk guards that pause trading when limits are hit.
    Uses trade-count-based consecutive loss limits (not percentage drawdown).
    Source: RiskManagement_Spec.md Section 6.1–6.5
    """

    def __init__(self, config: dict[str, Any]):
        # Drawdown percentage limits (replaces consecutive loss limits)
        self.max_daily_drawdown_pct = config.get("max_daily_drawdown_pct", 3.0)
        self.max_weekly_drawdown_pct = config.get("max_weekly_drawdown_pct", 6.0)
        self.max_concurrent_positions = config.get("max_concurrent_positions", 3)
        self.max_positions_per_symbol = config.get("max_positions_per_symbol", 1)
        self.max_correlated_risk_pct = config.get("max_correlated_risk_pct", 4.0)
        self.max_daily_trades = config.get("max_daily_trades", 5)
        self.target_profit_enabled = config.get("target_profit_enabled", False)
        self.max_daily_profit = config.get("max_daily_profit", 500.0)
        self.max_weekly_profit = config.get("max_weekly_profit", 2000.0)

        # State
        self.daily_trades_count = 0
        self.daily_pnl = 0.0
        self.weekly_pnl = 0.0
        self.open_positions_by_symbol: dict[str, int] = {}
        self.is_paused = False
        self.pause_reason = ""
        self.last_reset_day = datetime.now(timezone.utc).date()
        self.last_reset_week = datetime.now(timezone.utc).isocalendar()[1]
        self.last_trade_closed_m15_time: int | None = None

        # Track active grouped trades (signal)
        self.active_groups = {}  # group_id -> {"pnl": 0.0, "sub_trades": 0}

        # Load persisted state (restores counters after bot restart)
        self.load_state()


    def check_all(self, account_balance: float, current_time: datetime | None = None) -> tuple[bool, str]:
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
                return False, "M15 Cooldown Active: Waiting for current M15 candle to close"

        # 2. Max daily trades
        if self.max_daily_trades > 0 and self.daily_trades_count >= self.max_daily_trades:
            self.is_paused = True
            self.pause_reason = f"Daily max trades limit reached ({self.daily_trades_count}/{self.max_daily_trades})"
            return False, self.pause_reason

        # 3. Max open positions (total across symbols)
        total_open = sum(self.open_positions_by_symbol.values())
        if total_open >= self.max_concurrent_positions:
            return False, f"Max open positions reached ({total_open}/{self.max_concurrent_positions})"

        # 4. Daily Drawdown Percentage
        start_of_day_balance = account_balance - self.daily_pnl
        if self.max_daily_drawdown_pct > 0 and self.daily_pnl < 0 and start_of_day_balance > 0:
            daily_dd_pct = (-self.daily_pnl / start_of_day_balance) * 100
            if daily_dd_pct >= self.max_daily_drawdown_pct:
                self.is_paused = True
                self.pause_reason = f"Daily drawdown limit reached: {daily_dd_pct:.2f}% >= {self.max_daily_drawdown_pct}%"
                return False, self.pause_reason
            
        # 5. Weekly Drawdown Percentage
        start_of_week_balance = account_balance - self.weekly_pnl
        if self.max_weekly_drawdown_pct > 0 and self.weekly_pnl < 0 and start_of_week_balance > 0:
            weekly_dd_pct = (-self.weekly_pnl / start_of_week_balance) * 100
            if weekly_dd_pct >= self.max_weekly_drawdown_pct:
                self.is_paused = True
                self.pause_reason = f"Weekly drawdown limit reached: {weekly_dd_pct:.2f}% >= {self.max_weekly_drawdown_pct}%"
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

    def check_symbol(self, symbol: str) -> tuple[bool, str]:
        """Check if a new position can be opened on the given symbol.
        Enforces one active signal group per symbol at a time.
        """
        sym_open = self.open_positions_by_symbol.get(symbol, 0)
        if sym_open >= self.max_positions_per_symbol:
            return False, f"Max positions reached for {symbol} ({sym_open}/{self.max_positions_per_symbol})"
        return True, "OK"

    def position_opened(self, group_id: str, sub_trade_count: int, symbol: str = "", initial_risk_dollars: float = 0.0):
        """Track a new position opening."""
        self.daily_trades_count += 1
        self.active_groups[group_id] = {
            "pnl": 0.0, 
            "sub_trades": sub_trade_count, 
            "symbol": symbol,
            "initial_risk": initial_risk_dollars
        }
        if symbol:
            self.open_positions_by_symbol[symbol] = self.open_positions_by_symbol.get(symbol, 0) + 1
        self.save_state()

    def get_open_risk(self) -> float:
        """Sum the initial risk of all currently active groups."""
        return sum(group.get("initial_risk", 0.0) for group in self.active_groups.values())

    def position_closed(self, group_id: str, pnl: float, current_time: datetime | None = None):
        """Track a position closing."""
        if group_id in self.active_groups:
            self.active_groups[group_id]["pnl"] += pnl
            self.active_groups[group_id]["sub_trades"] -= 1
            
            # If all sub-trades for this group are closed, record the group outcome.
            # DO NOT call _record_trade_result per leg — a TP1 win should not reset
            # the consecutive-loss streak if TP2/TP3 subsequently stop out at BE.
            # The circuit breaker tracks SIGNAL (group) outcomes, not individual leg outcomes.
            if self.active_groups[group_id]["sub_trades"] <= 0:
                group_pnl = self.active_groups[group_id]["pnl"]
                self._record_trade_result(group_pnl, group_pnl >= 0)
                
                # Set M15 cooldown
                if current_time is not None:
                    current_epoch = int(current_time.timestamp()) if hasattr(current_time, 'timestamp') else float(current_time)
                    self.last_trade_closed_m15_time = (int(current_epoch) // 900) * 900
                
                sym = self.active_groups[group_id].get("symbol", "")
                del self.active_groups[group_id]
                if sym and sym in self.open_positions_by_symbol:
                    self.open_positions_by_symbol[sym] = max(0, self.open_positions_by_symbol[sym] - 1)
                    if self.open_positions_by_symbol[sym] == 0:
                        del self.open_positions_by_symbol[sym]

    def rollback_position(self, group_id: str):
        """Rollback an opened position if MT5 execution failed entirely."""
        if group_id in self.active_groups:
            sym = self.active_groups[group_id].get("symbol", "")
            del self.active_groups[group_id]
            self.daily_trades_count = max(0, self.daily_trades_count - 1)
            if sym and sym in self.open_positions_by_symbol:
                self.open_positions_by_symbol[sym] = max(0, self.open_positions_by_symbol[sym] - 1)
                if self.open_positions_by_symbol[sym] == 0:
                    del self.open_positions_by_symbol[sym]
        self.save_state()

    def record_external_close(self, symbol: str, pnl: float, current_time: datetime | None = None):
        """
        Record a trade close from the MT5 sync loop WITHOUT requiring an active_groups entry.
        Fixes the bug where position_closed() was a no-op after a bot restart because
        active_groups was wiped. Called from BotService._trade_sync_loop.
        """
        self._record_trade_result(pnl, pnl >= 0)
        # Decrement symbol count if tracked
        if symbol in self.open_positions_by_symbol:
            self.open_positions_by_symbol[symbol] = max(0, self.open_positions_by_symbol[symbol] - 1)
            if self.open_positions_by_symbol[symbol] == 0:
                del self.open_positions_by_symbol[symbol]
        # Set M15 cooldown
        if current_time is not None:
            current_epoch = int(current_time.timestamp()) if hasattr(current_time, 'timestamp') else float(current_time)
            self.last_trade_closed_m15_time = (int(current_epoch) // 900) * 900
        self.save_state()

    def record_backtest_close(self, group_id: str, pnl: float, current_time: datetime | None = None):
        """
        Feed closed trade PnL directly into Circuit Breaker during backtesting.
        This avoids the complexity of tracking 'active_groups' and 'sub_trades' which
        can get out of sync during backtesting simulation and freeze the bot.
        """
        self.daily_pnl += pnl
        self.weekly_pnl += pnl
        
        # Free up open risk tracking for the closed group
        if group_id in self.active_groups:
            del self.active_groups[group_id]
        
        # We don't save state to disk here because backtester runs in a tight loop in-memory,
        # but we could update M15 cooldown if we wanted to enforce it during backtests.
        if current_time is not None:
            current_epoch = int(current_time.timestamp()) if hasattr(current_time, 'timestamp') else float(current_time)
            self.last_trade_closed_m15_time = (int(current_epoch) // 900) * 900

    def save_state(self):
        """Persist CB state to disk so it survives bot restarts."""
        try:
            os.makedirs(os.path.dirname(CB_STATE_FILE), exist_ok=True)
            state = {
                "daily_trades_count": self.daily_trades_count,
                "daily_pnl": self.daily_pnl,
                "weekly_pnl": self.weekly_pnl,
                "open_positions_by_symbol": self.open_positions_by_symbol,
                "is_paused": self.is_paused,
                "pause_reason": self.pause_reason,
                "last_reset_day": str(self.last_reset_day),
                "last_reset_week": self.last_reset_week,
            }
            with open(CB_STATE_FILE, "w") as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            logger.error(f"[CB] Failed to save state: {e}")

    def load_state(self):
        """
        Load persisted state from disk after a bot restart.
        Auto-resets daily counters if it is a new calendar day.
        """
        try:
            if not os.path.exists(CB_STATE_FILE):
                return
            with open(CB_STATE_FILE) as f:
                data = json.load(f)
            from datetime import date
            saved_day_str = data.get("last_reset_day", "")
            today = datetime.now(timezone.utc).date()
            is_new_day = str(today) != saved_day_str

            # Always restore weekly state (survives day boundaries)
            self.weekly_pnl = data.get("weekly_pnl", 0.0)
            self.last_reset_week = data.get("last_reset_week", today.isocalendar()[1])

            if is_new_day:
                # New day — reset daily counters (do not carry over daily streak or daily trades)
                logger.info("[CB] New day detected on load — daily counters reset.")
            else:
                self.daily_trades_count = data.get("daily_trades_count", 0)
                self.daily_pnl = data.get("daily_pnl", 0.0)
                self.open_positions_by_symbol = data.get("open_positions_by_symbol", {})
                # Only restore paused state for weekly-level pauses (daily pauses clear each day)
                if not is_new_day:
                    saved_pause = data.get("is_paused", False)
                    saved_reason = data.get("pause_reason", "")
                    if saved_pause and "weekly" in saved_reason.lower():
                        self.is_paused = saved_pause
                        self.pause_reason = saved_reason
            self.last_reset_day = today
            logger.info(f"[CB] State loaded from disk. open={self.open_positions_by_symbol}")
        except Exception as e:
            logger.error(f"[CB] Failed to load state: {e}")


    def _record_trade_result(self, pnl: float, is_win: bool):
        """Update state after a grouped trade fully closes."""
        self.daily_pnl += pnl
        self.weekly_pnl += pnl
        self.save_state()

    def manual_resume(self):
        """User manually re-enables trading after a streak pause. Resets ALL state."""
        self.is_paused = False
        self.pause_reason = ""
        self.last_trade_closed_m15_time = None
        self.save_state()
        logger.info("Circuit breaker manually resumed by user")


    def _daily_resume(self):
        """
        Auto-resume at start of a new trading day.
        Only resets DAILY state — does NOT touch weekly_consecutive_losses.
        Calling manual_resume() here was a bug: it cleared the weekly streak counter
        at midnight every day, making the weekly limit completely ineffective.
        """
        self.is_paused = False
        self.pause_reason = ""
        self.last_trade_closed_m15_time = None
        logger.info("Circuit breaker auto-resumed for new trading day (weekly streak preserved)")

    def _parse_timestamp_date(self, ts) -> tuple[datetime.date, int]:
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

    def _check_daily_reset(self, current_time: datetime | None = None):
        """Reset daily counters at midnight."""
        now = current_time if current_time is not None else datetime.now(timezone.utc)
        if hasattr(now, "date"):
            today = now.date()
        else:
            today, _ = self._parse_timestamp_date(now)
                
        if today != self.last_reset_day:
            self.daily_pnl = 0.0
            self.daily_trades_count = 0
            self.last_reset_day = today
            # Auto-resume daily pauses only — weekly streak is preserved intentionally.
            # (manual_resume() was previously called here, which wiped weekly_consecutive_losses.)
            if self.is_paused and "daily" in self.pause_reason.lower():
                self._daily_resume()

    def _check_weekly_reset(self, current_time: datetime | None = None):
        """Reset weekly counters on Monday."""
        now = current_time if current_time is not None else datetime.now(timezone.utc)
        if hasattr(now, "isocalendar"):
            current_week = now.isocalendar()[1]
        else:
            _, current_week = self._parse_timestamp_date(now)
                
        if current_week != self.last_reset_week:
            self.weekly_pnl = 0.0
            self.last_reset_week = current_week
            if "Weekly" in self.pause_reason:
                self.is_paused = False
                self.pause_reason = ""
