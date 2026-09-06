"""
backend/risk/circuit_breaker.py

Portfolio-level risk guards: daily/weekly percentage drawdown limits, max positions,
max daily trades, and target-profit halts.
Source: RiskManagement_Spec.md Section 6

NOTE: Daily/weekly risk is controlled via percentage drawdown limits
(max_daily_drawdown_pct / max_weekly_drawdown_pct), not trade-count-based consecutive
loss limits. An earlier version of this docstring claimed the opposite (that
percentage-based drawdown had been "replaced" by consecutive-loss limits) — that was
never actually implemented; this file has only ever enforced % drawdown. Corrected here
per user confirmation that percentage-based drawdown is the intended design (see
full_codebase_audit_and_fix_plan.md §2.7).
"""

import json
import os
from datetime import datetime, timezone
from typing import Any

from backend.utils.logger import get_logger

logger = get_logger(__name__)

CB_STATE_FILE = "backend/data/cb_state.json"


def _utc_now_str() -> str:
    """ISO timestamp string for the current UTC time. Used to tag group open times."""
    return datetime.now(timezone.utc).isoformat()

def _timeframe_to_seconds(tf: str) -> int:
    """Convert timeframe string like 'M5' to seconds."""
    tf = tf.upper()
    if tf == "M1": return 60
    if tf == "M5": return 300
    if tf == "M15": return 900
    if tf == "M30": return 1800
    if tf == "H1": return 3600
    if tf == "H4": return 14400
    if tf == "D1": return 86400
    return 900 # default to M15 if unknown


class CircuitBreaker:
    """
    Portfolio-level risk guards that pause trading when limits are hit.
    Uses percentage-based daily/weekly drawdown limits (max_daily_drawdown_pct /
    max_weekly_drawdown_pct) — not trade-count-based consecutive loss limits.
    Source: RiskManagement_Spec.md Section 6.1–6.5
    """

    def __init__(self, config: dict[str, Any], is_backtest: bool = False):
        self.is_backtest = is_backtest
        # Drawdown percentage limits (the actual mechanism controlling daily/weekly risk)
        self.max_daily_drawdown_pct = config.get("max_daily_drawdown_pct", 3.0)
        self.max_weekly_drawdown_pct = config.get("max_weekly_drawdown_pct", 6.0)
        self.max_concurrent_positions = config.get("max_concurrent_positions", 3)
        self.max_positions_per_symbol = config.get("max_positions_per_symbol", 1)
        self.max_daily_trades = config.get("max_daily_trades", 5)
        self.target_profit_enabled = config.get("target_profit_enabled", False)
        self.max_daily_profit = config.get("max_daily_profit", 500.0)
        self.max_weekly_profit = config.get("max_weekly_profit", 2000.0)
        # [2.18/2.19 / P2-R3/R4] Per-strategy overrides, keyed by strategy_id.
        # A strategy not present here falls back to the global limit above —
        # empty dicts (the default) reproduce today's global-only behaviour
        # exactly. Lets a high-frequency engine (e.g. VWAP) get a tighter
        # concurrent-position/daily-trade budget than a low-frequency one
        # (e.g. APA) without lowering the global cap for everyone.
        self.max_daily_trades_by_strategy: dict[str, int] = config.get("max_daily_trades_by_strategy", {}) or {}
        self.max_concurrent_positions_by_strategy: dict[str, int] = config.get("max_concurrent_positions_by_strategy", {}) or {}

        # State
        self.daily_trades_count = 0
        self.daily_trades_count_by_strategy: dict[str, int] = {}
        self.open_positions_by_strategy: dict[str, int] = {}
        # [12.5/Part14] Per-SLOT open-position count and daily-loss count.
        # Distinct from open_positions_by_symbol: once the same symbol can run
        # under two different strategy slots (InstrumentSlot), a single
        # symbol-wide counter would incorrectly block slot B's entry just
        # because slot A already has a position open on the same symbol.
        # Empty by default — a caller that never passes slot_id (every
        # existing call site, until 12.7/12.8 wire slot dispatch through)
        # reproduces today's symbol-wide-only behaviour exactly.
        self.open_positions_by_slot: dict[str, int] = {}
        self.losses_today_by_slot: dict[str, int] = {}  # [12.6]
        self.daily_pnl = 0.0
        self.weekly_pnl = 0.0
        # [9.7] Per-strategy realised P&L — feeds RiskParams.strategy_risk_budget_pct
        # so a high-frequency engine can't consume a low-frequency one's share of
        # the daily/weekly drawdown budget. Open (unrealised) risk stays globally
        # shared — see evaluate_signal's docstring note on this boundary.
        self.strategy_daily_pnl: dict[str, float] = {}
        self.strategy_weekly_pnl: dict[str, float] = {}
        self.open_positions_by_symbol: dict[str, int] = {}
        self.is_paused = False
        self.pause_reason = ""
        # [2.24] How many bars/checks this run has spent paused, and why — lets
        # a drawdown-latched week be visibly distinct from "no setups fired",
        # instead of both looking identical in the report (companion to [I4]).
        self.paused_bars = 0
        self.last_pause_reason = ""
        self.last_trade_closed_time: dict[str, int] = {}

        # Which MT5 login this state belongs to. Persisted state that was
        # written for a DIFFERENT account is discarded on load — see
        # load_state(). Without this, switching to a new account inherited the
        # old account's daily_pnl / weekly_pnl / open-position counts, and a
        # fresh $700 account could start out already "in drawdown" from a
        # $10,000 account's losses.
        self.account_id: int | None = config.get("mt5_account") or None

        # Last balance we were shown. Used to tell a *balance reset* (new
        # account, deposit, withdrawal, broker correction) apart from a trading
        # loss: a balance change that our own realised P&L cannot explain is
        # not drawdown, and must re-baseline the drawdown denominators rather
        # than count against them. See note_account_balance().
        self._last_known_balance: float = 0.0

        # Realised P&L this process has booked, cumulative and never reset by a
        # day or week rollover. note_account_balance() needs "how much of the
        # balance change did our own trading cause"; daily_pnl cannot answer
        # that across a midnight boundary, because it goes back to zero while
        # the balance does not.
        self._cumulative_pnl: float = 0.0
        self._cumulative_pnl_at_last_balance: float = 0.0

        # Bug 4: Track the balance at day-start for correct daily drawdown % denominator.
        self._day_start_balance: float = 0.0
        # §2.6 fix: same anchoring for the weekly check — without this, the weekly %
        # was computed against the live (self-referential) account_balance, which
        # balloons non-linearly as losses accumulate instead of using a fixed
        # week-start denominator.
        self._week_start_balance: float = 0.0

        # Bug 2: In backtest mode, initialize to None so the first bar's date
        # drives the reset — not today's real-world date. Without this, every
        # historical bar's date != today, resetting daily_trades_count to 0
        # on every single candle and making max_daily_trades completely ineffective.
        if is_backtest:
            self.last_reset_day = None
            self.last_reset_week = None
        else:
            self.last_reset_day = datetime.now(timezone.utc).date()
            self.last_reset_week = datetime.now(timezone.utc).isocalendar()[1]

        # Track active grouped trades (signal)
        self.active_groups = {}  # group_id -> {"pnl": 0.0, "sub_trades": 0}

        # Load persisted state (restores counters after bot restart)
        if not self.is_backtest:
            self.load_state()


    def check_all(self, account_balance: float, current_time: datetime | None = None, strategy_id: str | None = None) -> tuple[bool, str]:
        """Run all active circuit breaker checks."""
        self._check_daily_reset(current_time)
        self._check_weekly_reset(current_time)

        result = self._check_all_inner(account_balance, current_time, strategy_id)
        ok, reason = result
        # [2.24] Track paused bars/checks for the report — a latched breaker
        # should be visibly distinct from "the strategy found no setups".
        if not ok:
            self.paused_bars += 1
            self.last_pause_reason = reason
        return result

    def _check_all_inner(self, account_balance: float, current_time: datetime | None, strategy_id: str | None) -> tuple[bool, str]:
        if self.is_paused:
            return False, self.pause_reason

        # 1. Max daily trades (global, then per-strategy if configured)
        if self.max_daily_trades > 0 and self.daily_trades_count >= self.max_daily_trades:
            self.is_paused = True
            self.pause_reason = f"Daily max trades limit reached ({self.daily_trades_count}/{self.max_daily_trades})"
            return False, self.pause_reason

        if strategy_id and strategy_id in self.max_daily_trades_by_strategy:
            strat_limit = self.max_daily_trades_by_strategy[strategy_id]
            strat_count = self.daily_trades_count_by_strategy.get(strategy_id, 0)
            if strat_limit > 0 and strat_count >= strat_limit:
                return False, f"Daily max trades limit reached for {strategy_id} ({strat_count}/{strat_limit})"

        # 3. Max open positions (total across symbols; then per-strategy if configured)
        total_open = sum(self.open_positions_by_symbol.values())
        if total_open >= self.max_concurrent_positions:
            return False, f"Max open positions reached ({total_open}/{self.max_concurrent_positions})"

        if strategy_id and strategy_id in self.max_concurrent_positions_by_strategy:
            strat_cap = self.max_concurrent_positions_by_strategy[strategy_id]
            strat_open = self.open_positions_by_strategy.get(strategy_id, 0)
            if strat_open >= strat_cap:
                return False, f"Max open positions reached for {strategy_id} ({strat_open}/{strat_cap})"

        # 4. Daily Drawdown Percentage
        # Bug 4 fix: Use the day-start balance as the denominator, not the
        # current account balance.  A fixed-capital drawdown limit (e.g. 3% of
        # $25,000 = $750) must not shrink as the account grows — otherwise
        # $750 loss on a $100k account only shows 0.75% drawdown.
        if self._day_start_balance <= 0:
            self._day_start_balance = account_balance
        if self.max_daily_drawdown_pct > 0 and self.daily_pnl < 0 and self._day_start_balance > 0:
            daily_dd_pct = (-self.daily_pnl / self._day_start_balance) * 100
            if daily_dd_pct >= self.max_daily_drawdown_pct:
                self.is_paused = True
                self.pause_reason = f"Daily drawdown limit reached: {daily_dd_pct:.2f}% >= {self.max_daily_drawdown_pct}%"
                return False, self.pause_reason
            
        # 5. Weekly Drawdown Percentage
        # §2.6 fix: anchor on the week-start balance (like the daily check does with
        # _day_start_balance), not the live account_balance — the live balance already
        # reflects weekly_pnl (balance ≈ week_start + weekly_pnl), so dividing by it
        # made the computed % non-linear and inconsistent with max_weekly_drawdown_pct.
        if self._week_start_balance <= 0:
            self._week_start_balance = account_balance
        if self.max_weekly_drawdown_pct > 0 and self.weekly_pnl < 0 and self._week_start_balance > 0:
            weekly_dd_pct = (-self.weekly_pnl / self._week_start_balance) * 100
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

    def get_open_risk(self) -> float:
        """Calculate the total initial risk dollars of all currently active groups."""
        return sum(g.get("initial_risk", 0.0) for g in self.active_groups.values())

    def check_symbol(
        self,
        symbol: str,
        timeframe: str = "M15",
        current_time: datetime | None = None,
        slot_id: str | None = None,
        slot_max_positions: int | None = None,
        slot_max_losses_per_day: int | None = None,
    ) -> tuple[bool, str]:
        """Check if a new position can be opened on the given symbol.
        Enforces one active signal group per symbol at a time, and a timeframe-aware cooldown.

        [12.5/12.6/Part14] When `slot_id` is supplied, the position-count
        check resolves against THIS SLOT's own open-position count and
        `slot_max_positions` (falling back to the global symbol-wide check
        when either is omitted — this is what makes two InstrumentSlots on
        the same symbol independent of each other rather than sharing one
        counter), and a per-slot daily-loss cap is enforced when
        `slot_max_losses_per_day` is supplied. Every existing call site that
        doesn't pass `slot_id` gets EXACTLY today's symbol-wide-only
        behaviour — this is purely additive.
        """
        # 1. Timeframe Cooldown Check
        if symbol in self.last_trade_closed_time and current_time is not None:
            try:
                tf_seconds = _timeframe_to_seconds(timeframe)
                current_epoch = int(current_time.timestamp()) if hasattr(current_time, 'timestamp') else float(current_time)

                # Floor both to the candle boundary to see if we are in the same candle
                current_candle = (int(current_epoch) // tf_seconds) * tf_seconds
                closed_candle = (self.last_trade_closed_time[symbol] // tf_seconds) * tf_seconds

                if current_candle <= closed_candle:
                    return False, f"{timeframe} Cooldown Active: Waiting for current {timeframe} candle to close"
            except (ValueError, TypeError):
                pass

        # 2. Max positions check — per-slot when possible, else symbol-wide.
        if slot_id and slot_max_positions is not None:
            slot_open = self.open_positions_by_slot.get(slot_id, 0)
            if slot_open >= slot_max_positions:
                return False, f"Max positions reached for slot {slot_id} on {symbol} ({slot_open}/{slot_max_positions})"
        else:
            sym_open = self.open_positions_by_symbol.get(symbol, 0)
            if sym_open >= self.max_positions_per_symbol:
                return False, f"Max positions reached for {symbol} ({sym_open}/{self.max_positions_per_symbol})"

        # 3. [12.6] Per-slot daily loss cap.
        if slot_id and slot_max_losses_per_day is not None and slot_max_losses_per_day > 0:
            slot_losses = self.losses_today_by_slot.get(slot_id, 0)
            if slot_losses >= slot_max_losses_per_day:
                return False, f"Max daily losses reached for slot {slot_id} on {symbol} ({slot_losses}/{slot_max_losses_per_day})"

        return True, "OK"

    def get_cluster_open_risk(self, cluster: str, overrides: dict[str, str] | None = None) -> float:
        """[9.5] Sum of initial_risk across every active group whose symbol resolves to `cluster`."""
        from backend.risk.portfolio_governor import resolve_cluster
        return sum(
            g.get("initial_risk", 0.0)
            for g in self.active_groups.values()
            if resolve_cluster(g.get("symbol", ""), overrides) == cluster
        )

    def get_net_direction_open_risk(self, cluster: str, direction: str, overrides: dict[str, str] | None = None) -> float:
        """[9.6] Sum of initial_risk across every active group in `cluster`, taken in the SAME effective direction as `direction` (cluster-inversion-aware)."""
        from backend.risk.portfolio_governor import resolve_net_direction_key
        total = 0.0
        for g in self.active_groups.values():
            g_cluster, g_dir = resolve_net_direction_key(g.get("symbol", ""), g.get("direction", ""), overrides)
            if g_cluster == cluster and g_dir == direction:
                total += g.get("initial_risk", 0.0)
        return total

    def position_opened(self, group_id: str, sub_trade_count: int, symbol: str = "", initial_risk_dollars: float = 0.0, strategy_id: str = "", direction: str = "", slot_id: str = ""):
        """
        Track a new signal group opening.

        Increments open_positions_by_symbol[symbol] by 1 per SIGNAL GROUP,
        regardless of how many TP legs (sub_trade_count) are placed. This ensures
        max_concurrent_positions and max_positions_per_symbol correctly gate on
        concurrent signals, not individual TP legs. A user setting max_concurrent=3
        expects 3 active signals, not 3 TP legs.

        daily_trades_count is incremented here (on group open), not on close.
        This is intentional: the limit is conservative — it prevents opening the Nth
        signal even if previous groups have already closed (groups-opened semantics).
        """
        self.daily_trades_count += 1
        if strategy_id:
            self.daily_trades_count_by_strategy[strategy_id] = self.daily_trades_count_by_strategy.get(strategy_id, 0) + 1
            self.open_positions_by_strategy[strategy_id] = self.open_positions_by_strategy.get(strategy_id, 0) + 1
        if slot_id:  # [12.5]
            self.open_positions_by_slot[slot_id] = self.open_positions_by_slot.get(slot_id, 0) + 1
        self.active_groups[group_id] = {
            "pnl": 0.0,
            "sub_trades": sub_trade_count,
            "symbol": symbol,
            "strategy_id": strategy_id,
            "direction": direction,  # [9.6]
            "slot_id": slot_id,  # [12.5/12.6]
            "initial_risk": initial_risk_dollars,
            # Store open timestamp so daily reset can detect cross-day stale groups
            "opened_at": _utc_now_str(),
        }
        if symbol:
            # Increment by 1 per group, not by sub_trade_count
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
            
            # Only record the trade result once ALL sub-trades (TP legs) for this signal
            # group have closed, using the group's net PnL — not once per leg as each
            # individual TP fires. The circuit breaker tracks SIGNAL (group) outcomes,
            # not individual leg outcomes.
            if self.active_groups[group_id]["sub_trades"] <= 0:
                group_pnl = self.active_groups[group_id]["pnl"]
                self._record_trade_result(
                    group_pnl, group_pnl >= 0,
                    strategy_id=self.active_groups[group_id].get("strategy_id", ""),
                    slot_id=self.active_groups[group_id].get("slot_id", ""),
                )
                # Set per-symbol cooldown (live only)
                sym = self.active_groups[group_id].get("symbol", "")
                if not self.is_backtest and current_time is not None and sym:
                    current_epoch = int(current_time.timestamp()) if hasattr(current_time, 'timestamp') else float(current_time)
                    self.last_trade_closed_time[sym] = int(current_epoch)

                strat = self.active_groups[group_id].get("strategy_id", "")
                slot = self.active_groups[group_id].get("slot_id", "")
                del self.active_groups[group_id]
                if sym and sym in self.open_positions_by_symbol:
                    self.open_positions_by_symbol[sym] = max(0, self.open_positions_by_symbol[sym] - 1)
                    if self.open_positions_by_symbol[sym] == 0:
                        del self.open_positions_by_symbol[sym]
                if strat and strat in self.open_positions_by_strategy:
                    self.open_positions_by_strategy[strat] = max(0, self.open_positions_by_strategy[strat] - 1)
                    if self.open_positions_by_strategy[strat] == 0:
                        del self.open_positions_by_strategy[strat]
                if slot and slot in self.open_positions_by_slot:  # [12.5]
                    self.open_positions_by_slot[slot] = max(0, self.open_positions_by_slot[slot] - 1)
                    if self.open_positions_by_slot[slot] == 0:
                        del self.open_positions_by_slot[slot]

    def rollback_position(self, group_id: str):
        """Rollback an opened position if MT5 execution failed entirely."""
        if group_id in self.active_groups:
            sym = self.active_groups[group_id].get("symbol", "")
            strat = self.active_groups[group_id].get("strategy_id", "")
            slot = self.active_groups[group_id].get("slot_id", "")
            del self.active_groups[group_id]
            self.daily_trades_count = max(0, self.daily_trades_count - 1)
            if strat and strat in self.daily_trades_count_by_strategy:
                self.daily_trades_count_by_strategy[strat] = max(0, self.daily_trades_count_by_strategy[strat] - 1)
            if sym and sym in self.open_positions_by_symbol:
                self.open_positions_by_symbol[sym] = max(0, self.open_positions_by_symbol[sym] - 1)
                if self.open_positions_by_symbol[sym] == 0:
                    del self.open_positions_by_symbol[sym]
            if strat and strat in self.open_positions_by_strategy:
                self.open_positions_by_strategy[strat] = max(0, self.open_positions_by_strategy[strat] - 1)
                if self.open_positions_by_strategy[strat] == 0:
                    del self.open_positions_by_strategy[strat]
            if slot and slot in self.open_positions_by_slot:  # [12.5]
                self.open_positions_by_slot[slot] = max(0, self.open_positions_by_slot[slot] - 1)
                if self.open_positions_by_slot[slot] == 0:
                    del self.open_positions_by_slot[slot]
        self.save_state()

    def record_external_close(self, symbol: str, pnl: float, current_time: datetime | None = None):
        """
        Record a trade close from the MT5 sync loop.
        If an active group for this symbol is tracked, delegates to position_closed
        to decrement sub_trades and clean up memory. If not (e.g. post-restart),
        forcefully decrements the symbol count.

        [4.8/D10] group_id is now a real UUID (bot_service.py no longer uses
        `group_id = signal.symbol`), so groups must be found by their stored
        "symbol" field rather than the old `symbol in self.active_groups`
        direct-key check, which only ever worked because group_id used to BE
        the symbol string. With `allow_pyramiding` off (the default), at most
        one active group exists per symbol, so this match is unambiguous;
        with pyramiding on, the FIRST matching group is picked (this call site
        only receives symbol/pnl/close_time, not a ticket-to-group mapping, so
        exact per-position attribution isn't available here) — the group's
        sub_trades/PnL bookkeeping may attribute a close to the wrong sibling
        group when >1 group shares a symbol, though the symbol-level and
        daily/weekly PnL totals are unaffected either way.
        """
        matching_group_id = next(
            (gid for gid, g in self.active_groups.items() if g.get("symbol") == symbol),
            None,
        )
        if matching_group_id is not None:
            self.position_closed(group_id=matching_group_id, pnl=pnl, current_time=current_time)
        else:
            self._record_trade_result(pnl, pnl >= 0)
            # Decrement symbol count if tracked
            if symbol in self.open_positions_by_symbol:
                self.open_positions_by_symbol[symbol] = max(0, self.open_positions_by_symbol[symbol] - 1)
                if self.open_positions_by_symbol[symbol] == 0:
                    del self.open_positions_by_symbol[symbol]
            # Set per-symbol cooldown
            if current_time is not None:
                current_epoch = int(current_time.timestamp()) if hasattr(current_time, 'timestamp') else float(current_time)
                self.last_trade_closed_time[symbol] = int(current_epoch)
            self.save_state()

    def record_backtest_close(self, group_id: str, group_pnl: float, current_time: datetime | None = None):
        """Used by backtester to finalize a group's total PnL at once, triggering drawdown checks."""
        _strat = self.active_groups[group_id].get("strategy_id", "") if group_id in self.active_groups else ""
        _slot = self.active_groups[group_id].get("slot_id", "") if group_id in self.active_groups else ""
        self._record_trade_result(group_pnl, group_pnl >= 0, strategy_id=_strat, slot_id=_slot)

        sym = self.active_groups[group_id].get("symbol", "") if group_id in self.active_groups else ""
        
        # Bug 9: skip cooldown in backtest — see position_closed() comment above.
        if not self.is_backtest and current_time is not None and sym:
            try:
                current_epoch = int(current_time.timestamp()) if hasattr(current_time, 'timestamp') else float(current_time)
                self.last_trade_closed_time[sym] = int(current_epoch)
            except (ValueError, TypeError):
                pass
            
        if group_id in self.active_groups:
            strat = self.active_groups[group_id].get("strategy_id", "")
            slot = self.active_groups[group_id].get("slot_id", "")
            del self.active_groups[group_id]
            if sym and sym in self.open_positions_by_symbol:
                self.open_positions_by_symbol[sym] = max(0, self.open_positions_by_symbol[sym] - 1)
                if self.open_positions_by_symbol[sym] == 0:
                    del self.open_positions_by_symbol[sym]
            if strat and strat in self.open_positions_by_strategy:
                self.open_positions_by_strategy[strat] = max(0, self.open_positions_by_strategy[strat] - 1)
                if self.open_positions_by_strategy[strat] == 0:
                    del self.open_positions_by_strategy[strat]
            if slot and slot in self.open_positions_by_slot:  # [12.5]
                self.open_positions_by_slot[slot] = max(0, self.open_positions_by_slot[slot] - 1)
                if self.open_positions_by_slot[slot] == 0:
                    del self.open_positions_by_slot[slot]

    def save_state(self):
        """Save current state to file (skipped in backtest)."""
        if self.is_backtest:
            return
            
        try:
            os.makedirs(os.path.dirname(CB_STATE_FILE), exist_ok=True)
            state = {
                # Scopes this file to one MT5 login. load_state() throws the
                # whole file away when it does not match the account now
                # connected.
                "mt5_account": self.account_id,
                "day_start_balance": self._day_start_balance,
                "week_start_balance": self._week_start_balance,
                "last_known_balance": self._last_known_balance,
                "cumulative_pnl": self._cumulative_pnl,
                "cumulative_pnl_at_last_balance": self._cumulative_pnl_at_last_balance,
                "daily_trades_count": self.daily_trades_count,
                "daily_pnl": self.daily_pnl,
                "weekly_pnl": self.weekly_pnl,
                "open_positions_by_symbol": self.open_positions_by_symbol,
                "last_trade_closed_time": self.last_trade_closed_time,
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

            # Account scoping. State written against a different MT5 login says
            # nothing about this one: its daily/weekly P&L, its open-position
            # counts and its drawdown baselines all belong to another account's
            # balance. Carrying them over is what made a freshly reset demo
            # account come up already halted on "max daily drawdown".
            saved_account = data.get("mt5_account")
            if self.account_id and saved_account and int(saved_account) != int(self.account_id):
                logger.warning(
                    f"[CB] Persisted state belongs to MT5 account {saved_account}, "
                    f"but {self.account_id} is connected - discarding it and starting clean."
                )
                self.reset_for_new_account(self.account_id)
                return
            if self.account_id and not saved_account:
                # Pre-scoping state file: we cannot prove it belongs to this
                # account, so we refuse to trust its P&L. Same treatment.
                logger.warning(
                    "[CB] Persisted state has no account tag (written before account "
                    "scoping existed) - discarding it rather than risk booking another "
                    "account's P&L as this one's."
                )
                self.reset_for_new_account(self.account_id)
                return

            self._day_start_balance = data.get("day_start_balance", 0.0) or 0.0
            self._week_start_balance = data.get("week_start_balance", 0.0) or 0.0
            self._last_known_balance = data.get("last_known_balance", 0.0) or 0.0
            self._cumulative_pnl = data.get("cumulative_pnl", 0.0) or 0.0
            self._cumulative_pnl_at_last_balance = (
                data.get("cumulative_pnl_at_last_balance", self._cumulative_pnl) or 0.0
            )

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
                self.last_trade_closed_time = data.get("last_trade_closed_time", {})
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


    # -- Account identity & balance-reset handling ---------------------------

    def reset_for_new_account(self, account_id: int | None):
        """Wipe every carried-over counter and re-tag this state to `account_id`.

        Called when the connected MT5 login changes (or when persisted state
        cannot be proven to belong to the connected login). Everything the old
        account taught us - realised P&L, trade counts, open-position counts,
        pause latches, drawdown baselines - is about a different balance and a
        different set of positions, so none of it survives.
        """
        self.account_id = int(account_id) if account_id else None
        self.daily_trades_count = 0
        self.daily_trades_count_by_strategy = {}
        self.daily_pnl = 0.0
        self.weekly_pnl = 0.0
        self.strategy_daily_pnl = {}
        self.strategy_weekly_pnl = {}
        self.open_positions_by_symbol = {}
        self.open_positions_by_strategy = {}
        self.open_positions_by_slot = {}
        self.losses_today_by_slot = {}
        self.last_trade_closed_time = {}
        self.active_groups = {}
        self.is_paused = False
        self.pause_reason = ""
        # Baselines are re-anchored to the new account's balance on the next
        # check_all() call, because they are zero here.
        self._day_start_balance = 0.0
        self._week_start_balance = 0.0
        self._last_known_balance = 0.0
        self._cumulative_pnl = 0.0
        self._cumulative_pnl_at_last_balance = 0.0
        self.last_reset_day = datetime.now(timezone.utc).date()
        self.last_reset_week = datetime.now(timezone.utc).isocalendar()[1]
        logger.info(f"[CB] State reset for MT5 account {self.account_id}.")
        self.save_state()

    def note_account_balance(self, balance: float, account_id: int | None = None) -> bool:
        """Feed the live account balance in, and re-baseline if it was reset.

        A drawdown check compares realised P&L against a *baseline balance*. If
        the balance moves for a reason that is not trading - a new account, a
        deposit, a withdrawal, a demo-account reset, a broker correction - then
        the old baseline describes money that is no longer there, and the
        drawdown percentage computed from it is meaningless.

        We can detect exactly that, because we know how much of the change our
        own trading accounts for: `daily_pnl`. If the balance moved by
        materially more than our realised P&L explains, the difference did not
        come from trading, and the baselines are re-anchored to the balance we
        are actually looking at now.

        Returns True if a re-baseline happened.
        """
        if not balance or balance <= 0:
            return False

        # Account switch takes priority - nothing about the old state applies.
        if account_id and self.account_id and int(account_id) != int(self.account_id):
            self.reset_for_new_account(account_id)
            self._last_known_balance = balance
            self._day_start_balance = balance
            self._week_start_balance = balance
            self.save_state()
            return True
        if account_id and not self.account_id:
            self.account_id = int(account_id)

        if self._last_known_balance <= 0:
            # First observation on this account - just anchor.
            self._last_known_balance = balance
            self._cumulative_pnl_at_last_balance = self._cumulative_pnl
            if self._day_start_balance <= 0:
                self._day_start_balance = balance
            if self._week_start_balance <= 0:
                self._week_start_balance = balance
            self.save_state()
            return False

        # How much the balance moved, and how much of that our own trading
        # accounts for. Anything left over did not come from trading.
        #
        # The comparison is against the P&L booked SINCE THE LAST OBSERVATION,
        # not against the day's total: an earlier version compared |delta| to
        # abs(daily_pnl) as a tolerance, which meant a -$250 losing day made a
        # +$250 unexplained jump (exactly what a demo-account reset looks like)
        # fall inside tolerance and go undetected.
        delta = balance - self._last_known_balance
        explained = self._cumulative_pnl - self._cumulative_pnl_at_last_balance
        unexplained = delta - explained
        # Slack for swap, commission and rounding we did not book precisely,
        # and for floating positions that closed between observations.
        tolerance = max(balance * 0.001, 1.0)
        if abs(unexplained) > tolerance:
            logger.warning(
                f"[CB] Balance moved {delta:+.2f} to {balance:.2f}, of which our own "
                f"trading explains {explained:+.2f} - {unexplained:+.2f} is unaccounted "
                f"for. Treating as a balance reset / deposit / withdrawal, NOT drawdown. "
                f"Re-baselining."
            )
            self._day_start_balance = balance
            self._week_start_balance = balance
            self._last_known_balance = balance
            self._cumulative_pnl_at_last_balance = self._cumulative_pnl
            # The P&L that produced the (now void) drawdown reading belonged to
            # the old balance, so it is cleared alongside the baseline. Weekly
            # goes with it: a weekly drawdown percentage measured from a
            # week-start balance that no longer exists is just as meaningless
            # as the daily one.
            self.daily_pnl = 0.0
            self.weekly_pnl = 0.0
            self.strategy_daily_pnl = {}
            self.strategy_weekly_pnl = {}
            if self.is_paused and ("drawdown" in self.pause_reason.lower()):
                logger.warning(f"[CB] Clearing drawdown pause ({self.pause_reason}) after balance reset.")
                self.is_paused = False
                self.pause_reason = ""
            self.save_state()
            return True

        self._last_known_balance = balance
        self._cumulative_pnl_at_last_balance = self._cumulative_pnl
        return False

    def _record_trade_result(self, pnl: float, is_win: bool, strategy_id: str = "", slot_id: str = ""):
        """Update state after a grouped trade fully closes."""
        self.daily_pnl += pnl
        self.weekly_pnl += pnl
        self._cumulative_pnl += pnl
        if strategy_id:
            self.strategy_daily_pnl[strategy_id] = self.strategy_daily_pnl.get(strategy_id, 0.0) + pnl
            self.strategy_weekly_pnl[strategy_id] = self.strategy_weekly_pnl.get(strategy_id, 0.0) + pnl
        if slot_id and not is_win:  # [12.6]
            self.losses_today_by_slot[slot_id] = self.losses_today_by_slot.get(slot_id, 0) + 1
        self.save_state()

    def reconcile_from_mt5(self, open_symbols: list[str]):
        """
        Reconcile open_positions_by_symbol against actual MT5 state.

        Called on bot startup after querying MT5 for currently open positions.
        Resets open_positions_by_symbol to match reality, clearing any stale counts
        from cb_state.json that were left over from trades closed while the bot
        was offline (e.g. manual MT5 close, SL hit during downtime).

        Args:
            open_symbols: List of symbol strings for positions currently open in MT5.
                          Duplicates are counted (e.g. ['EURUSD', 'EURUSD'] = 2 open on EURUSD).
        """
        fresh_counts: dict[str, int] = {}
        for sym in open_symbols:
            fresh_counts[sym] = fresh_counts.get(sym, 0) + 1

        stale = self.open_positions_by_symbol
        self.open_positions_by_symbol = fresh_counts

        if stale != fresh_counts:
            logger.info(
                f"[CB] reconcile_from_mt5: open positions corrected. "
                f"Was={stale} → Now={fresh_counts}"
            )
        else:
            logger.info(f"[CB] reconcile_from_mt5: state consistent — {fresh_counts}")

        # §2.10 fix: reconcile_from_mt5 previously only repaired open_positions_by_symbol,
        # leaving active_groups (and the initial_risk it feeds into get_open_risk()) with
        # no defense against a missed close callback, a mid-callback exception, or a
        # process restart at the wrong moment — such a stale group would otherwise survive
        # reconciliation indefinitely. Drop any group whose symbol has zero open positions
        # in the fresh MT5 snapshot; a group can't legitimately still be active if MT5
        # reports nothing open for its symbol.
        stale_group_ids = [
            gid for gid, g in self.active_groups.items()
            if g.get("symbol") and fresh_counts.get(g.get("symbol"), 0) <= 0
        ]
        for gid in stale_group_ids:
            logger.warning(
                f"[CB] reconcile_from_mt5: dropping stale active_groups entry "
                f"{gid} (symbol={self.active_groups[gid].get('symbol')}) — "
                f"no matching open position found in MT5 snapshot."
            )
            del self.active_groups[gid]

        self.save_state()

    def manual_resume(self):
        """User manually re-enables trading after a streak pause. Resets ALL state."""
        self.is_paused = False
        self.pause_reason = ""
        self.last_trade_closed_time = {}
        self.save_state()
        logger.info("Circuit breaker manually resumed by user")


    def _daily_resume(self):
        """
        Auto-resume at start of a new trading day.
        Only resets DAILY state — does NOT touch weekly_pnl or the weekly drawdown pause.
        Calling manual_resume() here was a bug: it cleared weekly state at midnight
        every day, making the weekly drawdown limit completely ineffective.
        """
        self.is_paused = False
        self.pause_reason = ""
        self.last_trade_closed_time = {}
        logger.info("Circuit breaker auto-resumed for new trading day (weekly drawdown state preserved)")

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
                
        # Bug 2 fix: None sentinel means first bar → always trigger reset to set last_reset_day.
        if self.last_reset_day is None or today != self.last_reset_day:
            self.daily_pnl = 0.0
            self.daily_trades_count = 0
            self.daily_trades_count_by_strategy = {}
            self.strategy_daily_pnl = {}  # [9.7]
            self.losses_today_by_slot = {}  # [12.6]
            self.last_reset_day = today
            # Bug 4 fix: reset the day-start balance anchor so the new day's
            # first check_all() captures the opening balance correctly.
            self._day_start_balance = 0.0
            # Purge fully-closed groups that survived from previous days.
            # A group with sub_trades <= 0 is done — it should not continue to count
            # against open_positions_by_symbol, but can linger in active_groups if the
            # final position_closed() call was missed (e.g. MT5 closed it externally).
            stale_gids = [gid for gid, g in self.active_groups.items() if g.get("sub_trades", 1) <= 0]
            for gid in stale_gids:
                sym = self.active_groups[gid].get("symbol", "")
                if sym and sym in self.open_positions_by_symbol:
                    self.open_positions_by_symbol[sym] = max(0, self.open_positions_by_symbol[sym] - 1)
                    if self.open_positions_by_symbol[sym] == 0:
                        del self.open_positions_by_symbol[sym]
                del self.active_groups[gid]
            if stale_gids:
                logger.info(f"[CB] Daily reset: purged {len(stale_gids)} stale groups: {stale_gids}")
            # Auto-resume daily pauses only — weekly streak is preserved intentionally.
            if self.is_paused and "daily" in self.pause_reason.lower():
                self._daily_resume()

    def _check_weekly_reset(self, current_time: datetime | None = None):
        """Reset weekly counters on Monday."""
        now = current_time if current_time is not None else datetime.now(timezone.utc)
        if hasattr(now, "isocalendar"):
            current_week = now.isocalendar()[1]
        else:
            _, current_week = self._parse_timestamp_date(now)

        # Bug 2 fix: None sentinel means first bar — always trigger to set last_reset_week.
        if self.last_reset_week is None or current_week != self.last_reset_week:
            self.weekly_pnl = 0.0
            self.strategy_weekly_pnl = {}  # [9.7]
            self.last_reset_week = current_week
            # §2.6 fix: reset the week-start balance anchor so the new week's first
            # check_all() captures the opening balance correctly (mirrors
            # _check_daily_reset's _day_start_balance reset).
            self._week_start_balance = 0.0
            if "Weekly" in self.pause_reason:
                self.is_paused = False
                self.pause_reason = ""
