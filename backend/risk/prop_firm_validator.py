"""
backend/risk/prop_firm_validator.py

Validates trades against strict Prop Firm (BloomFunded) rules without compounding.
"""
import json
import os
from datetime import datetime, timedelta
from typing import Any

from backend.utils.logger import get_logger

logger = get_logger(__name__)

STATE_FILE = "backend/data/prop_firm_state.json"

class PropFirmValidator:
    def __init__(self, config: Any):
        """
        config: PropFirmParams
        """
        self.enabled = getattr(config, "account_mode", "personal") == "prop_firm"
        self.challenge_type = getattr(config, "challenge_type", "none")
        self.account_size = getattr(config, "account_size", 10000.0)
        self.initial_balance = getattr(config, "initial_balance", 10000.0)
        self.max_lot_sizes = getattr(config, "max_lot_sizes", {})
        
        # State
        self.high_water_mark = self.initial_balance
        self.eod_baseline = self.initial_balance
        self.last_eod_date = None
        self.daily_profit = 0.0
        self.total_profit = 0.0
        self.active_trading_days = 0
        self.is_paused = False
        self.pause_reason = ""
        
        # Positions state
        self.open_positions_count = 0
        self.open_positions_by_symbol = {}
        self.open_lots_by_symbol = {}
        
        if self.enabled:
            self.load_state()

    def save_state(self):
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        try:
            with open(STATE_FILE, "w") as f:
                json.dump({
                    "high_water_mark": self.high_water_mark,
                    "eod_baseline": self.eod_baseline,
                    "last_eod_date": self.last_eod_date,
                    "daily_profit": self.daily_profit,
                    "total_profit": self.total_profit,
                    "active_trading_days": self.active_trading_days,
                    "is_paused": self.is_paused,
                    "pause_reason": self.pause_reason
                }, f)
        except Exception as e:
            logger.error(f"Failed to save prop firm state: {e}")

    def load_state(self):
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, "r") as f:
                    data = json.load(f)
                    self.high_water_mark = data.get("high_water_mark", self.initial_balance)
                    self.eod_baseline = data.get("eod_baseline", self.initial_balance)
                    self.last_eod_date = data.get("last_eod_date")
                    self.daily_profit = data.get("daily_profit", 0.0)
                    self.total_profit = data.get("total_profit", 0.0)
                    self.active_trading_days = data.get("active_trading_days", 0)
                    self.is_paused = data.get("is_paused", False)
                    self.pause_reason = data.get("pause_reason", "")
            except Exception as e:
                logger.error(f"Failed to load prop firm state: {e}")

    def update_equity_balance(self, equity: float, balance: float, current_time: datetime):
        if not self.enabled:
            return

        changed = False

        # 1. Trailing High-Water Mark (for 1-Step Flex)
        if equity > self.high_water_mark:
            self.high_water_mark = equity
            changed = True

        # Sync total_profit and daily_profit strictly from balance
        true_total = balance - self.initial_balance
        if abs(self.total_profit - true_total) > 0.01:
            self.total_profit = true_total
            changed = True
            
        true_daily = balance - self.eod_baseline
        if abs(self.daily_profit - true_daily) > 0.01:
            self.daily_profit = true_daily
            changed = True

        # 2. Check 22:00 UTC (17:00 EST) for EOD Snapshot
        # A trading day goes from 22:00 UTC to 21:59:59 UTC the next day.
        # Shift time by +2 hours so that midnight aligns exactly with 22:00 UTC.
        shifted_time = current_time + timedelta(hours=2)
        current_trading_date = shifted_time.date().isoformat()
        
        if self.last_eod_date != current_trading_date:
            # We crossed into a new trading day! Snapshot the higher of Balance or Equity
            self.eod_baseline = max(balance, equity)
            self.last_eod_date = current_trading_date
            
            # Check minimum trading day requirement
            if self.daily_profit >= (self.initial_balance * 0.005):
                self.active_trading_days += 1
            self.daily_profit = 0.0
            changed = True
            logger.info(f"[PropFirm] New Trading Day {current_trading_date}. EOD Baseline set to {self.eod_baseline}")

        if changed:
            self.save_state()

        self.check_drawdown_breaches(equity)

    def check_drawdown_breaches(self, equity: float):
        if not self.enabled or self.is_paused:
            return

        # A. Daily Drawdown Check (4% of initial balance below EOD baseline)
        daily_dd_allowance = self.initial_balance * 0.04
        if equity < (self.eod_baseline - daily_dd_allowance):
            self.is_paused = True
            self.pause_reason = f"Daily Drawdown Breach! Equity {equity} fell below baseline {self.eod_baseline} - {daily_dd_allowance}"
            self.save_state()
            logger.error(self.pause_reason)

        # B. Max Drawdown Check
        if self.challenge_type in ["1-step", "flex"]:
            # 8% Trailing based on initial balance
            max_dd_allowance = self.initial_balance * 0.08
            if equity < (self.high_water_mark - max_dd_allowance):
                self.is_paused = True
                self.pause_reason = f"Max Trailing Drawdown Breach! Equity {equity} fell below HWM {self.high_water_mark} - {max_dd_allowance}"
                self.save_state()
                logger.error(self.pause_reason)
        elif self.challenge_type == "2-step":
            # 6% Static based on initial balance
            max_dd_allowance = self.initial_balance * 0.06
            if equity < (self.initial_balance - max_dd_allowance):
                self.is_paused = True
                self.pause_reason = f"Max Static Drawdown Breach! Equity {equity} fell below {self.initial_balance} - {max_dd_allowance}"
                self.save_state()
                logger.error(self.pause_reason)

    def validate_trade(self, symbol: str, requested_lots: float) -> tuple[bool, str, float]:
        """
        Returns (is_valid, reason, allowed_lots)
        """
        if not self.enabled:
            return True, "OK", requested_lots

        if self.is_paused:
            return False, f"Prop Firm rules breached: {self.pause_reason}", 0.0

        # Position Limits
        sym_open = self.open_positions_by_symbol.get(symbol, 0)
        if sym_open >= 5:
            return False, f"Max 5 positions reached for {symbol}", 0.0
        
        if self.open_positions_count >= 13:
            return False, "Max 13 total open positions reached", 0.0

        # Max Lot Size Limit
        max_lot_allowed = self.max_lot_sizes.get(symbol, 999.0)
        current_lots = self.open_lots_by_symbol.get(symbol, 0.0)
        
        if (current_lots + requested_lots) > max_lot_allowed:
            allowed = max_lot_allowed - current_lots
            if allowed <= 0.001:
                return False, f"Max lot size limit reached for {symbol}. Allowed: {max_lot_allowed}, Current: {current_lots}", 0.0
            else:
                return True, f"Downsized to fit max lot limit for {symbol}", allowed

        return True, "OK", requested_lots



    def record_trade_opened(self, symbol: str, lots: float):
        if not self.enabled: return
        self.open_positions_count += 1
        self.open_positions_by_symbol[symbol] = self.open_positions_by_symbol.get(symbol, 0) + 1
        self.open_lots_by_symbol[symbol] = self.open_lots_by_symbol.get(symbol, 0.0) + lots

    def record_trade_closed(self, symbol: str, lots: float, pnl: float):
        if not self.enabled: return
        self.open_positions_count = max(0, self.open_positions_count - 1)
        if symbol in self.open_positions_by_symbol:
            self.open_positions_by_symbol[symbol] = max(0, self.open_positions_by_symbol[symbol] - 1)
        if symbol in self.open_lots_by_symbol:
            self.open_lots_by_symbol[symbol] = max(0.0, self.open_lots_by_symbol[symbol] - lots)
            
        # We no longer increment profit manually here; it is strictly computed from balance in update_equity_balance
        self.save_state()
