"""
backend/risk/prop_firm_validator.py

Validates trades against strict Prop Firm (BloomFunded) rules without compounding.
Prop Firm is a SOFT MONITOR only — it never blocks or modifies trades.
It logs warnings and sends Telegram alerts for informational awareness only.
"""
import asyncio
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
        
        # Drawdown limits (customizable, fallback to defaults)
        default_daily = 4.0 if self.challenge_type in ["1-step", "flex"] else 5.0
        default_max = 8.0 if self.challenge_type in ["1-step", "flex"] else 10.0
        self.max_daily_loss_pct = getattr(config, "max_daily_loss_pct", default_daily)
        self.max_total_drawdown_pct = getattr(config, "max_total_drawdown_pct", default_max)

        # State
        self.high_water_mark = self.initial_balance
        self.eod_baseline = self.initial_balance
        self.last_eod_date = None
        self.daily_profit = 0.0
        self.total_profit = 0.0
        self.active_trading_days = 0
        self.is_breached = False
        self.breach_reason = ""
        self.net_deposits = 0.0

        # Positions state
        self.open_positions_count = 0
        self.open_positions_by_symbol = {}
        self.open_lots_by_symbol = {}

        # Alert dedup: track which alerts were already sent this session
        self._alerts_sent: set = set()

        if self.enabled:
            self.load_state()

    # ── Telegram alerting ─────────────────────────────────────────────────────

    def _telegram_alert(self, message: str, alert_key: str = None):
        """
        Fire-and-forget Telegram alert. alert_key deduplicates repeated alerts
        within the same session so the same breach does not spam every tick.
        """
        if alert_key:
            if alert_key in self._alerts_sent:
                return
            self._alerts_sent.add(alert_key)

        try:
            from backend.services.telegram import telegram_service
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(telegram_service.send_message(message))
            else:
                asyncio.run(telegram_service.send_message(message))
        except Exception as e:
            logger.error(f"[PropFirm] Failed to send Telegram alert: {e}")

    # ── Persistence ───────────────────────────────────────────────────────────

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
                    "is_breached": self.is_breached,
                    "breach_reason": self.breach_reason,
                    "net_deposits": self.net_deposits,
                    "initial_balance": self.initial_balance
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
                    self.is_breached = data.get("is_breached", False)
                    self.breach_reason = data.get("breach_reason", "")
                    self.net_deposits = data.get("net_deposits", 0.0)
                    # FIX: previously referenced an undefined `config` name
                    # (load_state() has no such variable/attribute — that
                    # branch just happened to never execute because
                    # hasattr(self, 'config') is always False). It also
                    # discarded the value __init__ already set correctly
                    # from the caller's config. Fall back to the
                    # already-set self.initial_balance instead.
                    self.initial_balance = data.get("initial_balance", self.initial_balance)
            except Exception as e:
                logger.error(f"Failed to load prop firm state: {e}")

    # ── Equity / EOD updates ──────────────────────────────────────────────────

    def update_equity_balance(self, equity: float, balance: float, current_time: datetime, net_deposits: float = None):
        if not self.enabled:
            return

        changed = False

        if net_deposits is not None:
            if self.net_deposits == 0.0:
                self.net_deposits = net_deposits
                changed = True
            elif net_deposits < self.net_deposits:
                withdrawal_amount = self.net_deposits - net_deposits
                logger.info(f"[PropFirm] Withdrawal detected: {withdrawal_amount}. Adjusting baselines.")
                self.eod_baseline = max(0.0, self.eod_baseline - withdrawal_amount)
                self.high_water_mark = max(0.0, self.high_water_mark - withdrawal_amount)
                self.initial_balance = max(0.0, self.initial_balance - withdrawal_amount)
                self.net_deposits = net_deposits
                changed = True
            elif net_deposits > self.net_deposits:
                deposit_amount = net_deposits - self.net_deposits
                logger.info(f"[PropFirm] Deposit detected: {deposit_amount}. Adjusting baselines.")
                self.initial_balance += deposit_amount
                self.eod_baseline += deposit_amount
                self.high_water_mark += deposit_amount
                self.net_deposits = net_deposits
                changed = True

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
            # We crossed into a new trading day — snapshot the higher of Balance or Equity
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
        if not self.enabled:
            return

        # A. Auto-recovery: if is_breached (from a previous breach), check if equity recovered
        if self.is_breached:
            daily_dd_allowance = self.initial_balance * (self.max_daily_loss_pct / 100.0)
            daily_floor = self.eod_baseline - daily_dd_allowance
            max_dd_allowance = self.initial_balance * (self.max_total_drawdown_pct / 100.0)
            max_dd_floor = (self.high_water_mark - max_dd_allowance) if self.challenge_type in ["1-step", "flex"] else (self.initial_balance - max_dd_allowance)
            # Only auto-recover if equity is above both floors
            if equity >= daily_floor and equity >= max_dd_floor:
                logger.info(f"[PropFirm] Equity recovered to {equity:.2f}. Clearing breach state.")
                self.is_breached = False
                self.breach_reason = ""
                self.save_state()
                self._telegram_alert(
                    f"✅ *Prop Firm — Drawdown Recovered*\n"
                    f"Equity: ${equity:.2f} has recovered above all drawdown floors.\n"
                    f"Monitoring resumed.",
                    alert_key=f"recovery_{self.last_eod_date}"
                )
            return  # Either still paused or just recovered — no need to re-check breach below

        # A. Daily Drawdown Check
        daily_dd_allowance = self.initial_balance * (self.max_daily_loss_pct / 100.0)
        daily_floor = self.eod_baseline - daily_dd_allowance
        if equity < daily_floor:
            self.is_breached = True
            self.breach_reason = (
                f"Daily Drawdown Breach! Equity {equity:.2f} fell below daily floor "
                f"{daily_floor:.2f} (baseline {self.eod_baseline:.2f} - {daily_dd_allowance:.2f})"
            )
            self.save_state()
            logger.error(f"[PROP FIRM] {self.breach_reason}")
            self._telegram_alert(
                f"\U0001f534 *Prop Firm Alert \u2014 Daily Drawdown Breached*\n"
                f"Equity: ${equity:.2f}\n"
                f"Daily Floor: ${daily_floor:.2f}\n"
                f"EOD Baseline: ${self.eod_baseline:.2f}\n"
                f"Allowance: ${daily_dd_allowance:.2f}\n"
                f"\u26a0\ufe0f Trading continues (monitor only \u2014 no signals blocked)",
                alert_key=f"daily_dd_{self.last_eod_date}"
            )
            return

        # B. Max Drawdown Check
        if self.challenge_type in ["1-step", "flex"]:
            max_dd_allowance = self.initial_balance * (self.max_total_drawdown_pct / 100.0)
            max_dd_floor = self.high_water_mark - max_dd_allowance
            if equity < max_dd_floor:
                self.is_breached = True
                self.breach_reason = (
                    f"Max Trailing Drawdown Breach! Equity {equity:.2f} fell below "
                    f"HWM {self.high_water_mark:.2f} - {max_dd_allowance:.2f}"
                )
                self.save_state()
                logger.error(f"[PROP FIRM] {self.breach_reason}")
                self._telegram_alert(
                    f"\U0001f534 *Prop Firm Alert \u2014 Max Trailing Drawdown Breached*\n"
                    f"Equity: ${equity:.2f}\n"
                    f"HWM: ${self.high_water_mark:.2f}\n"
                    f"Max DD Allowance: ${max_dd_allowance:.2f}\n"
                    f"Floor: ${max_dd_floor:.2f}\n"
                    f"\u26a0\ufe0f Trading continues (monitor only \u2014 no signals blocked)",
                    alert_key="max_trailing_dd"
                )
                return

        elif self.challenge_type == "2-step":
            max_dd_allowance = self.initial_balance * (self.max_total_drawdown_pct / 100.0)
            max_dd_floor = self.initial_balance - max_dd_allowance
            if equity < max_dd_floor:
                self.is_breached = True
                self.breach_reason = (
                    f"Max Static Drawdown Breach! Equity {equity:.2f} fell below "
                    f"{self.initial_balance:.2f} - {max_dd_allowance:.2f}"
                )
                self.save_state()
                logger.error(f"[PROP FIRM] {self.breach_reason}")
                self._telegram_alert(
                    f"\U0001f534 *Prop Firm Alert \u2014 Max Static Drawdown Breached*\n"
                    f"Equity: ${equity:.2f}\n"
                    f"Initial Balance: ${self.initial_balance:.2f}\n"
                    f"Max DD Allowance: ${max_dd_allowance:.2f}\n"
                    f"Floor: ${max_dd_floor:.2f}\n"
                    f"\u26a0\ufe0f Trading continues (monitor only \u2014 no signals blocked)",
                    alert_key="max_static_dd"
                )
                return

        # C. Yellow Warning — 50% of daily DD used
        daily_used = self.eod_baseline - equity
        if daily_used > 0 and daily_used >= (daily_dd_allowance * 0.5):
            pct_used = (daily_used / daily_dd_allowance) * 100
            self._telegram_alert(
                f"\U0001f7e1 *Prop Firm Warning \u2014 Daily Drawdown at {pct_used:.1f}%*\n"
                f"Equity: ${equity:.2f}\n"
                f"Daily Loss So Far: ${daily_used:.2f} of ${daily_dd_allowance:.2f} allowed\n"
                f"Remaining Buffer: ${daily_dd_allowance - daily_used:.2f}",
                alert_key=f"daily_dd_warn_{self.last_eod_date}"
            )

    # ── Trade validation (SOFT MONITOR \u2014 never blocks) ────────────────────────

    def validate_trade(self, symbol: str, requested_lots: float) -> tuple:
        """
        Always approves the trade. Logs and alerts on limit breaches (informational only).
        """
        if not self.enabled:
            return True, "OK", requested_lots

        if self.is_breached:
            msg = (
                f"\u26a0\ufe0f *Prop Firm Monitor \u2014 Drawdown breach active, trade proceeding*\n"
                f"Symbol: {symbol}\n"
                f"Reason: {self.breach_reason}"
            )
            logger.warning(f"Prop Firm rules breached: {self.breach_reason} (Trade allowed per user request)")
            self._telegram_alert(msg, alert_key=f"trade_breach_{symbol}_{self.breach_reason[:30]}")

        sym_open = self.open_positions_by_symbol.get(symbol, 0)
        if sym_open >= 5:
            logger.warning(f"Max 5 positions reached for {symbol} (Trade allowed per user request)")

        if self.open_positions_count >= 13:
            logger.warning("Max 13 total open positions reached (Trade allowed per user request)")

        max_lot_allowed = self.max_lot_sizes.get(symbol, 999.0)
        current_lots = self.open_lots_by_symbol.get(symbol, 0.0)
        if (current_lots + requested_lots) > max_lot_allowed:
            logger.warning(
                f"Max lot size limit reached for {symbol}. "
                f"Limit: {max_lot_allowed}, Current: {current_lots} (Trade allowed per user request)"
            )

        return True, "OK", requested_lots

    # ── Position tracking ─────────────────────────────────────────────────────

    def record_trade_opened(self, symbol: str, lots: float):
        if not self.enabled:
            return
        self.open_positions_count += 1
        self.open_positions_by_symbol[symbol] = self.open_positions_by_symbol.get(symbol, 0) + 1
        self.open_lots_by_symbol[symbol] = self.open_lots_by_symbol.get(symbol, 0.0) + lots

    def record_trade_closed(self, symbol: str, lots: float, pnl: float):
        if not self.enabled:
            return
        self.open_positions_count = max(0, self.open_positions_count - 1)
        if symbol in self.open_positions_by_symbol:
            self.open_positions_by_symbol[symbol] = max(0, self.open_positions_by_symbol[symbol] - 1)
        if symbol in self.open_lots_by_symbol:
            self.open_lots_by_symbol[symbol] = max(0.0, self.open_lots_by_symbol[symbol] - lots)

        # Profit is computed strictly from balance in update_equity_balance
        self.save_state()