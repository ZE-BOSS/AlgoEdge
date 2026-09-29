"""
tests/test_profit_target_live_bugs.py

The four profit-target defects found from live evidence on 2026-09-29, each
pinned by the scenario that exposed it.

THE REPORT
----------
Two Crash 1000 Index positions were open at +$97.54 and +$52.83 against a
per-trade target of $50 (scope TRADE, basis FLOATING, action CLOSE_AND_PAUSE).
Neither closed. Nothing was logged. The dashboard showed the floating P&L the
whole time, so the number the target needed was on screen while the target
ignored it.

ROOT CAUSE
----------
`CircuitBreaker.active_groups` is only ever populated when THIS process opens a
trade (`register_position`), and nothing rehydrates it. `note_group_floating`
began with

    group = self.active_groups.get(group_id)
    if group is None:
        return

so every position that survived a bot restart was dropped on the floor, and
`check_profit_target` then built its numbers from an empty dict and found
nothing to do. Silent, and indistinguishable from "no target set".

The caller had ALREADY established ownership — bot_service matches
`Trade.group_id`, symbol and strategy_id to the slot before calling — so
membership of `active_groups` was a redundant second gate, and the fragile one.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.risk.circuit_breaker import CircuitBreaker
from backend.risk.profit_target import (
    ACTION_CLOSE,
    ACTION_CLOSE_ONLY,
    ACTION_PAUSE,
    note_and_check,
)

# The live settings from the screenshots, exactly.
LIVE = {
    "target_profit_enabled": True,
    "profit_target_scopes": ["TRADE"],
    "profit_target_basis": "FLOATING",
    "profit_target_action": "CLOSE_AND_PAUSE",
    "profit_target_is_pct": False,
    "max_trade_profit": 50.0,
    "max_daily_profit": 500.0,
    "max_weekly_profit": 2000.0,
    "max_monthly_profit": 8000.0,
}


def _breaker(**over):
    return CircuitBreaker({**LIVE, **over}, is_backtest=False)


# ── 1. the bug that was costing money ───────────────────────────────────────
def test_a_position_this_process_did_not_open_is_still_targeted():
    """THE REGRESSION TEST. Before the fix this returned [] and both trades ran on."""
    cb = _breaker()
    assert not cb.active_groups, "precondition: the breaker has no groups, as after a restart"

    positions = [
        {"group_id": "g-97", "ticket": 1, "symbol": "Crash 1000 Index", "profit": 97.54},
        {"group_id": "g-52", "ticket": 2, "symbol": "Crash 1000 Index", "profit": 52.83},
    ]
    to_close = note_and_check(cb, positions, lambda p: p["profit"], 3585.44)

    assert set(to_close) == {"g-97", "g-52"}, (
        "both trades were past the $50 target and both must be banked")


def test_the_adopted_group_carries_its_symbol_for_the_log():
    cb = _breaker()
    note_and_check(cb, [{"group_id": "g1", "ticket": 1, "symbol": "Crash 1000 Index",
                         "profit": 60.0}], lambda p: p["profit"], 3585.44)
    assert cb.active_groups["g1"]["symbol"] == "Crash 1000 Index"
    assert cb.active_groups["g1"]["adopted"] is True
    assert cb.active_groups["g1"]["initial_risk"] == 0.0, (
        "risk is genuinely unknown for a position this breaker did not open")


def test_a_position_below_the_target_is_left_alone():
    """The control: adoption must not close things that have not earned it."""
    cb = _breaker()
    to_close = note_and_check(
        cb, [{"group_id": "g1", "ticket": 1, "symbol": "X", "profit": 49.99}],
        lambda p: p["profit"], 3585.44)
    assert to_close == []


def test_a_position_with_no_group_id_is_reported_not_swallowed(monkeypatch):
    """It cannot be attributed to a slot, so it cannot be targeted — but that is
    a broken write upstream, not a normal condition, so it must be loud.

    (The project logs through loguru, which pytest's `caplog` does not see, so
    the logger is captured directly rather than through the fixture.)"""
    import backend.risk.profit_target as pt

    said: list[str] = []
    monkeypatch.setattr(pt.logger, "warning", lambda msg, *a, **k: said.append(str(msg)))

    cb = _breaker()
    to_close = note_and_check(
        cb, [{"group_id": None, "ticket": 9, "symbol": "X", "profit": 500.0}],
        lambda p: p["profit"], 3585.44)
    assert to_close == []
    assert any("no group_id" in m for m in said)


# ── 2. pyramiding: what a "trade" is ────────────────────────────────────────
def test_legs_of_one_pyramided_group_are_summed():
    """Two fills under ONE signal are one trade, so the target sees their total."""
    cb = _breaker()
    to_close = note_and_check(cb, [
        {"group_id": "same", "ticket": 1, "symbol": "X", "profit": 30.0},
        {"group_id": "same", "ticket": 2, "symbol": "X", "profit": 25.0},
    ], lambda p: p["profit"], 3585.44)
    assert to_close == ["same"], "30 + 25 = 55 is past a $50 target"


def test_separate_signals_are_judged_independently():
    """Two signals are two trades. One reaching $50 must not close the other."""
    cb = _breaker()
    to_close = note_and_check(cb, [
        {"group_id": "winner", "ticket": 1, "symbol": "X", "profit": 60.0},
        {"group_id": "young", "ticket": 2, "symbol": "X", "profit": 5.0},
    ], lambda p: p["profit"], 3585.44)
    assert to_close == ["winner"], "the $5 trade has not earned anything yet"


# ── 3. the new CLOSE action ─────────────────────────────────────────────────
def test_close_only_banks_a_period_target_without_stopping_the_slot():
    cb = _breaker(profit_target_scopes=["DAY"], profit_target_action=ACTION_CLOSE_ONLY,
                  max_daily_profit=100.0)
    note_and_check(cb, [{"group_id": "g1", "ticket": 1, "symbol": "X", "profit": 150.0}],
                   lambda p: p["profit"], 3585.44)
    ok, _ = cb.check_all(account_balance=3585.44)
    assert ok, "CLOSE must leave the slot free to take the next signal"
    assert not cb.is_paused


def test_close_and_pause_still_stops_the_slot():
    cb = _breaker(profit_target_scopes=["DAY"], profit_target_action=ACTION_CLOSE,
                  max_daily_profit=100.0)
    note_and_check(cb, [{"group_id": "g1", "ticket": 1, "symbol": "X", "profit": 150.0}],
                   lambda p: p["profit"], 3585.44)
    ok, reason = cb.check_all(account_balance=3585.44)
    assert not ok and cb.is_paused and "Daily" in reason


def test_a_per_trade_target_never_pauses_whatever_the_action_says():
    """A per-trade target that paused the slot would end the day on the first
    winner. TRADE scope banks the trade and carries on, always."""
    for action in (ACTION_PAUSE, ACTION_CLOSE, ACTION_CLOSE_ONLY):
        cb = _breaker(profit_target_action=action)
        to_close = note_and_check(
            cb, [{"group_id": "g1", "ticket": 1, "symbol": "X", "profit": 60.0}],
            lambda p: p["profit"], 3585.44)
        assert to_close == ["g1"], f"{action}: a per-trade target must close"
        ok, _ = cb.check_all(account_balance=3585.44)
        assert ok and not cb.is_paused, f"{action}: TRADE scope must not pause"


# ── 4. the accounting day: UTC by default, WAT on request ──────────────────
def test_the_default_trading_day_is_utc_as_it_always_was():
    """Restored 2026-09-29: the bot's limits roll at midnight UTC unless an
    account opts in to another offset."""
    cb = _breaker()
    cb.daily_pnl = 99.0
    cb._check_daily_reset(datetime(2026, 9, 29, 23, 30, tzinfo=timezone.utc))
    assert cb.daily_pnl == 99.0, "the UTC day has not turned yet"
    cb._check_daily_reset(datetime(2026, 9, 30, 0, 5, tzinfo=timezone.utc))
    assert cb.daily_pnl == 0.0


def test_the_day_rolls_over_at_midnight_wat_when_an_account_asks_for_it():
    """23:30 UTC is 00:30 the NEXT day in WAT, so with offset 1 the day must
    already have turned."""
    cb = _breaker(accounting_utc_offset_hours=1.0)
    cb.daily_pnl = 250.0
    cb._check_daily_reset(datetime(2026, 9, 29, 22, 0, tzinfo=timezone.utc))   # 23:00 WAT
    assert cb.daily_pnl == 250.0, "still the same WAT day"

    cb._check_daily_reset(datetime(2026, 9, 29, 23, 30, tzinfo=timezone.utc))  # 00:30 WAT
    assert cb.daily_pnl == 0.0, "the WAT day turned and the counters must reset"


def test_the_offset_is_configurable_and_utc_is_still_available():
    cb = _breaker(accounting_utc_offset_hours=0.0)
    cb.daily_pnl = 99.0
    cb._check_daily_reset(datetime(2026, 9, 29, 23, 30, tzinfo=timezone.utc))
    assert cb.daily_pnl == 99.0, "at offset 0 the UTC day has not turned yet"


def test_a_naive_backtest_bar_time_is_treated_as_utc():
    """Backtest bar times arrive without a tzinfo. Attaching UTC is what makes a
    backtest day and a live day the same day."""
    cb = _breaker(accounting_utc_offset_hours=1.0)
    shifted = cb._accounting_time(datetime(2026, 9, 29, 23, 30))
    assert shifted.hour == 0 and shifted.day == 30


def test_week_and_month_use_the_same_boundary():
    cb = _breaker(accounting_utc_offset_hours=1.0)
    # 30 Sep 23:30 UTC is 1 Oct 00:30 WAT — a new month, and a new ISO week.
    cb.monthly_pnl, cb.weekly_pnl = 400.0, 400.0
    when = datetime(2026, 9, 30, 23, 30, tzinfo=timezone.utc)
    cb._check_monthly_reset(when)
    cb._check_weekly_reset(when)
    assert cb.monthly_pnl == 0.0, "October began in WAT"


# ── 5. the latent percent bug ───────────────────────────────────────────────
def test_one_unknown_start_balance_no_longer_disables_the_target_for_everyone():
    """With a PERCENT per-trade target, a group whose start balance is unknown
    used to `return` out of the loop and spare every other open group with it."""
    from backend.risk.profit_target import ProfitTargetPolicy

    policy = ProfitTargetPolicy.from_config({
        **LIVE, "profit_target_is_pct": True, "max_trade_profit": 1.0,   # 1% of balance
    })
    hit = policy.evaluate(
        realised={}, floating_total=0.0,
        group_floating={"unknown": 500.0, "known": 500.0},
        group_start_balance={"known": 10_000.0},    # "unknown" deliberately absent
    )
    assert hit is not None and hit.group_ids == ("known",), (
        "the group with a known balance must still be judged")


@pytest.mark.parametrize("basis,expected", [("FLOATING", ["g1"]), ("BALANCE", [])])
def test_basis_decides_whether_an_open_trade_counts(basis, expected):
    """FLOATING is what lets a target fire while a trade is still running; on
    BALANCE an open position contributes nothing until it closes."""
    cb = _breaker(profit_target_basis=basis)
    to_close = note_and_check(
        cb, [{"group_id": "g1", "ticket": 1, "symbol": "X", "profit": 80.0}],
        lambda p: p["profit"], 3585.44)
    assert to_close == expected
