"""
backend/risk/prop_firm_validator.py

Validates trades against Prop Firm (e.g. BloomFunded) rules.
When account_mode = 'prop_firm', drawdown breaches are HARD circuit breakers:
- should_block_trading() returns True when is_breached, preventing new signals.
- reset_breach() allows the user to manually clear a breach from the UI.
When account_mode = 'personal', all checks are informational (soft monitor) only.
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
        config: PropFirmParams or dict
        """
        get_val = lambda key, default: config.get(key, default) if isinstance(config, dict) else getattr(config, key, default)

        self.enabled = get_val("account_mode", "personal") == "prop_firm"
        self.challenge_type = get_val("challenge_type", "none")
        self.account_size = get_val("account_size", 10000.0)
        self.initial_balance = get_val("initial_balance", 10000.0)
        self.max_lot_sizes = get_val("max_lot_sizes", {})
        # [3.10/E7] Real, user-editable settings replacing hardcoded constants
        # in validate_trade() below (999.0 default lot cap, 5/13 position
        # limits) and the trading-day rule in update_equity_balance() (0.005).
        self.default_max_lot = get_val("default_max_lot", None)
        self.max_positions_per_symbol = get_val("max_positions_per_symbol", 5)
        self.max_total_positions = get_val("max_total_positions", 13)
        self.trading_day_rule = get_val("trading_day_rule", "PROFIT_PCT")
        self.trading_day_profit_pct = get_val("trading_day_profit_pct", 0.5)
        # Per-trading-day flags for ANY_TRADE / ANY_CLOSED rules, reset at EOD rollover.
        self._traded_today = False
        self._closed_today = False

        # Drawdown limits (user-configurable, resettable from the UI)
        default_daily = 4.0 if self.challenge_type in ["1-step", "flex"] else 5.0
        default_max = 8.0 if self.challenge_type in ["1-step", "flex"] else 10.0
        self.max_daily_loss_pct = get_val("max_daily_loss_pct", default_daily)
        self.max_total_drawdown_pct = get_val("max_total_drawdown_pct", default_max)
        # When True, breach checks use floating equity (balance + unrealized P&L)
        # When False, only closed-trade balance is used
        self.drawdown_uses_equity = get_val("drawdown_uses_equity", True)

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

        self.is_backtesting = get_val("is_backtesting", False)

        # Alert dedup: track which alerts were already sent this session
        self._alerts_sent: set = set()
        # Breach logging dedup: ensures the breach is only logged once per engine run
        self._breach_logged: bool = False

        if self.enabled and not self.is_backtesting:
            self.load_state()

    # ── Telegram alerting ─────────────────────────────────────────────────────

    def _telegram_alert(self, message: str, alert_key: str = None):
        """
        Fire-and-forget Telegram alert. alert_key deduplicates repeated alerts
        within the same session so the same breach does not spam every tick.
        """
        if self.is_backtesting:
            return

        if alert_key:
            if alert_key in self._alerts_sent:
                return
            self._alerts_sent.add(alert_key)

        try:
            from backend.services.telegram import telegram_service
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(telegram_service.send_message(message))
            except RuntimeError:
                # No running event loop in this thread, safe to use asyncio.run
                asyncio.run(telegram_service.send_message(message))
        except Exception as e:
            logger.error(f"[PropFirm] Failed to send Telegram alert: {e}")

    # ── Persistence ───────────────────────────────────────────────────────────

    def save_state(self):
        if self.is_backtesting:
            return
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
            # We crossed into a new trading day — snapshot CLOSED-TRADE balance only.
            # Using max(balance, equity) previously inflated the baseline by unrealized
            # floating gains, creating a falsely strict daily drawdown floor the next day.
            self.eod_baseline = balance
            self.last_eod_date = current_trading_date

            # [3.11/E8] Check minimum trading day requirement per trading_day_rule
            # (was hardcoded to PROFIT_PCT @ 0.5%). ANY_TRADE/ANY_CLOSED count the
            # day the moment a trade opens/closes, regardless of profit.
            if self.trading_day_rule == "ANY_TRADE":
                day_counts = self._traded_today
            elif self.trading_day_rule == "ANY_CLOSED":
                day_counts = self._closed_today
            else:  # PROFIT_PCT — original hardcoded behaviour, now editable
                day_counts = self.daily_profit >= (self.initial_balance * (self.trading_day_profit_pct / 100.0))
            if day_counts:
                self.active_trading_days += 1
            self._traded_today = False
            self._closed_today = False
            self.daily_profit = 0.0
            changed = True
            logger.info(f"[PropFirm] New Trading Day {current_trading_date}. EOD Baseline set to {self.eod_baseline}")

        if changed:
            self.save_state()

        self.check_drawdown_breaches(equity)

    def _max_dd_floor(self) -> tuple[float, float]:
        """
        Returns (max_dd_allowance, max_dd_floor) for the max/total drawdown check.

        For trailing-drawdown challenge types ("1-step"/"flex"), the floor trails the
        tracked high-water-mark (self.high_water_mark), not the static initial balance —
        matching how these prop-firm challenges actually enforce trailing drawdown (the
        floor rises as the account grows above its starting balance). 2-step/static
        challenges keep the static initial_balance floor.
        """
        max_dd_allowance = self.initial_balance * (self.max_total_drawdown_pct / 100.0)
        if self.challenge_type in ["1-step", "flex"]:
            max_dd_floor = self.high_water_mark - max_dd_allowance
        else:
            max_dd_floor = self.initial_balance - max_dd_allowance
        return max_dd_allowance, max_dd_floor

    def check_drawdown_breaches(self, equity: float):
        if not self.enabled:
            return

        # A. Auto-recovery: if is_breached (from a previous breach), check if equity recovered
        if self.is_breached:
            daily_dd_allowance = self.initial_balance * (self.max_daily_loss_pct / 100.0)
            daily_floor = self.eod_baseline - daily_dd_allowance
            max_dd_allowance, max_dd_floor = self._max_dd_floor()
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
        # B. Max Drawdown Check — trailing (high-water-mark anchored) for 1-step/flex
        # challenge types, static (initial_balance anchored) for 2-step/other types.
        max_dd_allowance, max_dd_floor = self._max_dd_floor()
        is_trailing = self.challenge_type in ["1-step", "flex"]

        if equity < max_dd_floor:
            self.is_breached = True
            anchor_label = "High-Water Mark" if is_trailing else "Initial Balance"
            anchor_value = self.high_water_mark if is_trailing else self.initial_balance
            self.breach_reason = (
                f"Max {'Trailing' if is_trailing else 'Static'} Drawdown Breach! Equity {equity:.2f} fell below "
                f"{anchor_value:.2f} - {max_dd_allowance:.2f}"
            )
            self.save_state()
            logger.error(f"[PROP FIRM] {self.breach_reason}")
            self._telegram_alert(
                f"🔴 *Prop Firm Alert — Max {'Trailing' if is_trailing else 'Static'} Drawdown Breached*\n"
                f"Equity: ${equity:.2f}\n"
                f"{anchor_label}: ${anchor_value:.2f}\n"
                f"Max DD Allowance: ${max_dd_allowance:.2f}\n"
                f"Floor: ${max_dd_floor:.2f}\n"
                f"⚠️ Trading continues (monitor only — no signals blocked)",
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

    # ── Hard block gate — called by RiskEngine before approving signals ────────

    def should_block_trading(self) -> tuple[bool, str]:
        """
        Returns (is_blocked, reason). Called by RiskEngine.evaluate_signal().
        Only active when account_mode = 'prop_firm' (self.enabled = True).
        """
        if not self.enabled:
            return False, ""
        # Never block trades in backtesting, we just want to flag/monitor breaches.
        if getattr(self, "is_backtesting", False):
            return False, ""
            
        if self.is_breached:
            return True, f"Prop Firm drawdown breached: {self.breach_reason}"
        return False, ""

    def reset_breach(self):
        """
        Manually clear a drawdown breach from the UI.
        Allows the user to resume trading after reviewing the breach.
        Sends a Telegram notification that trading has been manually resumed.
        """
        if not self.is_breached:
            return
        logger.info(f"[PropFirm] Breach manually reset. Previous reason: {self.breach_reason}")
        self._telegram_alert(
            f"\u26a0\ufe0f *Prop Firm — Breach Manually Reset*\n"
            f"Previous breach: {self.breach_reason}\n"
            f"Trading manually resumed by user. Monitor closely.",
            alert_key=f"manual_reset_{self.last_eod_date}"
        )
        self.is_breached = False
        self.breach_reason = ""
        self._breach_logged = False
        self.save_state()

    # ── Trade validation (SOFT MONITOR — never blocks) ────────────────────────

    def validate_trade(self, symbol: str, requested_lots: float) -> tuple:
        """
        Approves the trade unless it would breach the aggregate max-lot-size cap for the
        symbol, in which case it is hard-rejected (returns False, reason, 0.0). All other
        checks here (position count, breach state) remain informational/monitor-only.
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
        if sym_open >= self.max_positions_per_symbol:
            logger.warning(f"Max {self.max_positions_per_symbol} positions reached for {symbol} (Trade allowed per user request)")

        if self.open_positions_count >= self.max_total_positions:
            logger.warning(f"Max {self.max_total_positions} total open positions reached (Trade allowed per user request)")

        max_lot_allowed = self.max_lot_sizes.get(
            symbol, self.default_max_lot if self.default_max_lot is not None else float("inf")
        )
        current_lots = self.open_lots_by_symbol.get(symbol, 0.0)
        if (current_lots + requested_lots) > max_lot_allowed:
            reason = (
                f"Max lot size limit breached for {symbol}. "
                f"Limit: {max_lot_allowed}, Current open: {current_lots}, Requested: {requested_lots}"
            )
            logger.error(f"[PROP FIRM] {reason}")
            self._telegram_alert(
                f"\U0001f534 *Prop Firm Alert \u2014 Aggregate Lot Cap Breach*\n"
                f"Symbol: {symbol}\n"
                f"{reason}",
                alert_key=f"lot_cap_{symbol}"
            )
            return False, reason, 0.0

        return True, "OK", requested_lots

    # ── Position tracking ─────────────────────────────────────────────────────

    def record_trade_opened(self, symbol: str, lots: float):
        if not self.enabled:
            return
        self._traded_today = True
        self.open_positions_count += 1
        self.open_positions_by_symbol[symbol] = self.open_positions_by_symbol.get(symbol, 0) + 1
        self.open_lots_by_symbol[symbol] = self.open_lots_by_symbol.get(symbol, 0.0) + lots

    def record_trade_closed(self, symbol: str, lots: float, pnl: float):
        if not self.enabled:
            return
        self._closed_today = True
        self.open_positions_count = max(0, self.open_positions_count - 1)
        if symbol in self.open_positions_by_symbol:
            self.open_positions_by_symbol[symbol] = max(0, self.open_positions_by_symbol[symbol] - 1)
        if symbol in self.open_lots_by_symbol:
            self.open_lots_by_symbol[symbol] = max(0.0, self.open_lots_by_symbol[symbol] - lots)

        # Profit is computed strictly from balance in update_equity_balance
        self.save_state()