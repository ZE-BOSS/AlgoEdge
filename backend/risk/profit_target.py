"""
backend/risk/profit_target.py

When a slot has made enough, and what to do about it.

WHAT CHANGED (2026-09-27)
-------------------------
Before this, a profit target was two numbers — `max_daily_profit` and
`max_weekly_profit` — measured on REALISED profit and able to do exactly one
thing: stop the slot opening anything new. A position already running was never
touched, so "bank this trade once it is $50 up" was not expressible at all.

Now a target is `(scope, basis, action, amount)`:

    scope   TRADE / DAY / WEEK / MONTH, any combination armed at once
    basis   BALANCE (realised only) or FLOATING (realised + this slot's open P&L)
    action  PAUSE (stop new entries) or CLOSE_AND_PAUSE (flatten first)
    amount  money, or a percent of the period's starting balance

PER SLOT IS NOT A FEATURE HERE, IT IS THE STRUCTURE
---------------------------------------------------
Every slot (symbol x strategy) owns its own CircuitBreaker — see risk/slot_book.py
— so that breaker's `active_groups` are that slot's groups and its `daily_pnl` is
that slot's realised profit. This module is only ever handed one slot's numbers,
and it has no way to reach the account's equity even if it wanted to.

That is the whole reason it is built this way. Account equity is the SUM of every
slot's floating P&L, so a target that read it would close a Boom trade sitting at
+$20 because a Crash trade happened to be +$40 at the same moment. Two instruments
that never traded each other would be coupled through a number neither of them
controls. The rule this module enforces instead:

    A slot's floating P&L is the unrealised P&L of the positions THAT SLOT
    OPENED, and nothing else.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from backend.utils.logger import get_logger

logger = get_logger(__name__)

SCOPE_TRADE = "TRADE"
SCOPE_DAY = "DAY"
SCOPE_WEEK = "WEEK"
SCOPE_MONTH = "MONTH"
PERIOD_SCOPES = (SCOPE_DAY, SCOPE_WEEK, SCOPE_MONTH)
ALL_SCOPES = (SCOPE_TRADE,) + PERIOD_SCOPES

BASIS_BALANCE = "BALANCE"
BASIS_FLOATING = "FLOATING"

ACTION_PAUSE = "PAUSE"
ACTION_CLOSE = "CLOSE_AND_PAUSE"

# scope -> the ADJECTIVE its pause message uses. Not cosmetic: the breaker's
# auto-resume looks for "daily" / "Weekly" / "Monthly" inside `pause_reason` to
# decide which rollover lifts which pause, so a message reading "Day profit
# target reached" would latch the slot until the process restarted. These strings
# also match the pre-2026-09-27 wording exactly, so saved runs and log greps
# still read the same.
SCOPE_LABEL = {
    SCOPE_TRADE: "Trade",
    SCOPE_DAY: "Daily",
    SCOPE_WEEK: "Weekly",
    SCOPE_MONTH: "Monthly",
}

# scope -> the risk-config key holding its amount
AMOUNT_KEY = {
    SCOPE_TRADE: "max_trade_profit",
    SCOPE_DAY: "max_daily_profit",
    SCOPE_WEEK: "max_weekly_profit",
    SCOPE_MONTH: "max_monthly_profit",
}


@dataclass
class TargetHit:
    """One target that has been reached."""
    scope: str
    amount: float
    reached: float
    basis: str
    action: str
    group_ids: tuple[str, ...] = ()      # groups to flatten, empty for PAUSE
    reason: str = ""

    @property
    def closes(self) -> bool:
        return bool(self.group_ids)


@dataclass
class ProfitTargetPolicy:
    """A slot's profit-target settings, resolved once from its risk config."""

    enabled: bool = False
    scopes: tuple[str, ...] = (SCOPE_DAY,)
    basis: str = BASIS_BALANCE
    action: str = ACTION_PAUSE
    is_pct: bool = False
    amounts: dict[str, float] = field(default_factory=dict)

    @classmethod
    def from_config(cls, config: dict[str, Any] | None) -> "ProfitTargetPolicy":
        cfg = config or {}
        raw = cfg.get("profit_target_scopes")
        if isinstance(raw, str):                     # a single scope, or "DAY,WEEK"
            raw = [p.strip() for p in raw.split(",") if p.strip()]
        scopes = tuple(str(s).upper() for s in (raw or [SCOPE_DAY]) if str(s).upper() in ALL_SCOPES)

        basis = str(cfg.get("profit_target_basis") or BASIS_BALANCE).upper()
        if basis not in (BASIS_BALANCE, BASIS_FLOATING):
            basis = BASIS_BALANCE
        action = str(cfg.get("profit_target_action") or ACTION_PAUSE).upper()
        if action not in (ACTION_PAUSE, ACTION_CLOSE):
            action = ACTION_PAUSE

        amounts = {}
        for scope, key in AMOUNT_KEY.items():
            try:
                amounts[scope] = float(cfg.get(key))
            except (TypeError, ValueError):
                continue
        return cls(
            enabled=bool(cfg.get("target_profit_enabled", False)),
            scopes=scopes or (SCOPE_DAY,),
            basis=basis,
            action=action,
            is_pct=bool(cfg.get("profit_target_is_pct", False)),
            amounts=amounts,
        )

    # ── the one question worth asking ───────────────────────────────────────
    def evaluate(
        self,
        *,
        realised: dict[str, float],
        floating_total: float,
        group_floating: dict[str, float],
        group_realised: dict[str, float] | None = None,
        period_start_balance: dict[str, float] | None = None,
        group_start_balance: dict[str, float] | None = None,
    ) -> TargetHit | None:
        """The first target this slot has reached, or None.

        Every argument is ONE SLOT's own numbers. `realised` is its profit per
        period scope, `group_floating` the unrealised P&L of each of its open
        groups, `floating_total` their sum. Nothing here is account-wide, and
        passing account equity in would be the bug this module exists to avoid.

        TRADE scope is checked first: a per-trade target should bank the trade
        that reached it rather than be pre-empted by a period target that the
        same profit also happens to satisfy.
        """
        if not self.enabled or not self.scopes:
            return None

        group_realised = group_realised or {}
        period_start_balance = period_start_balance or {}
        group_start_balance = group_start_balance or {}

        if SCOPE_TRADE in self.scopes:
            hit = self._check_trade(group_floating, group_realised, group_start_balance)
            if hit is not None:
                return hit

        for scope in PERIOD_SCOPES:
            if scope not in self.scopes:
                continue
            target = self._target_for(scope, period_start_balance.get(scope, 0.0))
            if target is None:
                continue
            made = float(realised.get(scope, 0.0))
            if self.basis == BASIS_FLOATING:
                made += float(floating_total)
            if made < target:
                continue
            # CLOSE_AND_PAUSE flattens everything this slot has open; PAUSE does not.
            groups = tuple(group_floating) if self.action == ACTION_CLOSE else ()
            return TargetHit(
                scope=scope, amount=target, reached=made, basis=self.basis, action=self.action,
                group_ids=groups,
                reason=(f"{SCOPE_LABEL[scope]} profit target reached: ${made:.2f} / "
                        f"${target:.2f} ({self.basis.lower()})"),
            )
        return None

    # ── internals ───────────────────────────────────────────────────────────
    def _target_for(self, scope: str, start_balance: float) -> float | None:
        amount = self.amounts.get(scope)
        if amount is None or amount <= 0:
            return None
        if not self.is_pct:
            return float(amount)
        if start_balance <= 0:
            # A percent of an unknown balance is not a target, it is a guess.
            logger.debug(f"[TARGET] {scope} target is a percent but the period start balance is unknown")
            return None
        return float(start_balance) * float(amount) / 100.0

    def _check_trade(self, group_floating: dict[str, float], group_realised: dict[str, float],
                     group_start_balance: dict[str, float]) -> TargetHit | None:
        """A per-trade target always CLOSES the group that reached it.

        A per-trade target that only paused new entries would leave the trade it
        was about to bank running to its stop, which is the opposite of what it
        is for — so `action` is not consulted here.
        """
        for group_id, floating in group_floating.items():
            target = self._target_for(SCOPE_TRADE, group_start_balance.get(group_id, 0.0))
            if target is None:
                return None                       # no per-trade amount set at all
            made = float(floating)
            if self.basis == BASIS_BALANCE:
                # realised-only on a still-open group is its already-banked legs
                made = float(group_realised.get(group_id, 0.0))
            else:
                made += float(group_realised.get(group_id, 0.0))
            if made >= target:
                return TargetHit(
                    scope=SCOPE_TRADE, amount=target, reached=made, basis=self.basis,
                    action=ACTION_CLOSE, group_ids=(group_id,),
                    reason=(f"{SCOPE_LABEL[SCOPE_TRADE]} profit target reached: ${made:.2f} / "
                            f"${target:.2f} ({self.basis.lower()})"),
                )
        return None


def note_and_check(circuit: Any, positions, pnl_of, account_balance: float = 0.0) -> list[str]:
    """Feed ONE SLOT's open positions to its breaker, and ask what to flatten.

    The three paths that manage positions — the single backtester, the portfolio
    backtester and the live position manager — differ in how they value an open
    position but not in what they do with the answer, so that part lives here
    once. `positions` must already be filtered to the slot that owns `circuit`;
    `pnl_of(position)` returns its unrealised P&L in account currency.

    Returns the group ids whose positions are to be closed, which is empty for a
    PAUSE-action target and for no target at all.
    """
    by_group: dict[str, float] = {}
    for pos in positions:
        gid = pos.get("group_id")
        if not gid:
            continue
        try:
            by_group[gid] = by_group.get(gid, 0.0) + float(pnl_of(pos) or 0.0)
        except Exception:  # a valuation failure must never stop position management
            logger.debug(f"[TARGET] could not value {gid}", exc_info=True)
    for gid, value in by_group.items():
        circuit.note_group_floating(gid, value, account_balance)
    circuit.check_profit_target(account_balance)
    return circuit.take_pending_closes()
