"""A profit target belongs to ONE SLOT, and must never read another's profit.

The failure this guards against, in the user's own words: a Boom slot $20 in
profit against a $50 target being closed because a Crash slot happened to be $40
up at the same moment. Account equity is the SUM of every slot's floating P&L, so
any target that reads it couples two instruments that never traded each other.

Before 2026-09-27 a target was two numbers (`max_daily_profit`,
`max_weekly_profit`), measured on REALISED profit only, and could do exactly one
thing: stop the slot opening anything new. A running position was never touched,
so "bank this trade once it is $50 up" was not expressible. These tests cover the
replacement: scope (TRADE/DAY/WEEK/MONTH), basis (BALANCE/FLOATING), action
(PAUSE/CLOSE_AND_PAUSE), and money-or-percent amounts.
"""

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from backend.risk.circuit_breaker import CircuitBreaker
from backend.risk.profit_target import (
    ACTION_CLOSE,
    BASIS_BALANCE,
    BASIS_FLOATING,
    ProfitTargetPolicy,
    note_and_check,
)


def _breaker(**cfg) -> CircuitBreaker:
    """A slot's breaker with two open groups: 'boom' and 'crash'."""
    cb = CircuitBreaker({"target_profit_enabled": True, **cfg}, is_backtest=True)
    cb.position_opened("boom", 1, symbol="Boom 900 Index", strategy_id="S", slot_id="k")
    cb.position_opened("crash", 1, symbol="Crash 900 Index", strategy_id="S", slot_id="k")
    return cb


# ── the attribution rule ────────────────────────────────────────────────────
def test_one_groups_profit_does_not_close_another():
    """THE test. Boom +$20, Crash +$60, per-trade target $50: only Crash closes.
    Combined floating is $80, and if that number were the one being read, Boom
    would be closed at $20 for no reason."""
    cb = _breaker(profit_target_scopes=["TRADE"], profit_target_basis=BASIS_FLOATING,
                  max_trade_profit=50.0)
    cb.note_group_floating("boom", 20.0)
    cb.note_group_floating("crash", 60.0)
    assert cb.floating_pnl == 80.0
    hit = cb.check_profit_target(10_000.0)
    assert hit is not None and hit.scope == "TRADE"
    assert hit.group_ids == ("crash",)
    assert cb.take_pending_closes() == ["crash"]


def test_neither_closes_when_neither_earned_it():
    """Boom +$20 and Crash +$40 sum to $60, past a $50 per-trade target — and
    nothing fires, because no single trade reached it."""
    cb = _breaker(profit_target_scopes=["TRADE"], profit_target_basis=BASIS_FLOATING,
                  max_trade_profit=50.0)
    cb.note_group_floating("boom", 20.0)
    cb.note_group_floating("crash", 40.0)
    assert cb.check_profit_target(10_000.0) is None
    assert cb.take_pending_closes() == []


def test_a_breaker_cannot_see_another_slots_groups():
    """Two slots, two breakers. The structural guarantee the design rests on."""
    boom = CircuitBreaker({"target_profit_enabled": True, "profit_target_scopes": ["DAY"],
                           "profit_target_basis": BASIS_FLOATING, "max_daily_profit": 50.0},
                          is_backtest=True)
    crash = CircuitBreaker({"target_profit_enabled": True, "profit_target_scopes": ["DAY"],
                            "profit_target_basis": BASIS_FLOATING, "max_daily_profit": 50.0},
                           is_backtest=True)
    boom.position_opened("b1", 1, symbol="Boom 900 Index", slot_id="boom")
    crash.position_opened("c1", 1, symbol="Crash 900 Index", slot_id="crash")
    boom.note_group_floating("b1", 20.0)
    crash.note_group_floating("c1", 500.0)
    assert boom.floating_pnl == 20.0, "a slot's floating P&L is its own groups only"
    assert boom.check_profit_target(10_000.0) is None
    assert crash.check_profit_target(10_000.0) is not None


# ── basis ───────────────────────────────────────────────────────────────────
def test_balance_basis_ignores_unrealised_profit():
    cb = _breaker(profit_target_scopes=["DAY"], profit_target_basis=BASIS_BALANCE,
                  max_daily_profit=50.0)
    cb.note_group_floating("boom", 999.0)
    assert cb.check_profit_target(10_000.0) is None, "BALANCE must not see open profit"
    cb.daily_pnl = 50.0
    assert cb.check_profit_target(10_000.0) is not None


def test_floating_basis_adds_realised_and_unrealised():
    cb = _breaker(profit_target_scopes=["DAY"], profit_target_basis=BASIS_FLOATING,
                  max_daily_profit=50.0)
    cb.daily_pnl = 30.0
    cb.note_group_floating("boom", 10.0)
    assert cb.check_profit_target(10_000.0) is None
    cb.note_group_floating("crash", 10.0)
    hit = cb.check_profit_target(10_000.0)
    assert hit is not None and hit.reached == pytest.approx(50.0)


# ── scope ───────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("scope,field,attr", [
    ("DAY", "max_daily_profit", "daily_pnl"),
    ("WEEK", "max_weekly_profit", "weekly_pnl"),
    ("MONTH", "max_monthly_profit", "monthly_pnl"),
])
def test_each_period_scope_reads_its_own_accumulator(scope, field, attr):
    cb = _breaker(profit_target_scopes=[scope], **{field: 100.0})
    setattr(cb, attr, 99.0)
    assert cb.check_profit_target(10_000.0) is None
    setattr(cb, attr, 100.0)
    hit = cb.check_profit_target(10_000.0)
    assert hit is not None and hit.scope == scope


def test_a_scope_that_is_not_armed_never_fires():
    cb = _breaker(profit_target_scopes=["WEEK"], max_daily_profit=1.0, max_weekly_profit=1e9)
    cb.daily_pnl = 10_000.0
    assert cb.check_profit_target(10_000.0) is None


def test_several_scopes_can_be_armed_at_once():
    cb = _breaker(profit_target_scopes=["TRADE", "WEEK"], profit_target_basis=BASIS_FLOATING,
                  max_trade_profit=50.0, max_weekly_profit=200.0)
    cb.note_group_floating("boom", 60.0)
    assert cb.check_profit_target(10_000.0).scope == "TRADE"
    cb.take_pending_closes()
    cb.note_group_floating("boom", 10.0)
    cb.weekly_pnl = 195.0
    assert cb.check_profit_target(10_000.0).scope == "WEEK"


def test_a_trade_target_is_checked_before_a_period_target():
    """The same profit can satisfy both; banking the trade that earned it is the
    more specific answer and must win."""
    cb = _breaker(profit_target_scopes=["TRADE", "DAY"], profit_target_basis=BASIS_FLOATING,
                  max_trade_profit=50.0, max_daily_profit=50.0)
    cb.note_group_floating("boom", 60.0)
    hit = cb.check_profit_target(10_000.0)
    assert hit.scope == "TRADE" and hit.group_ids == ("boom",)


# ── action ──────────────────────────────────────────────────────────────────
def test_pause_action_closes_nothing():
    cb = _breaker(profit_target_scopes=["DAY"], profit_target_basis=BASIS_FLOATING,
                  max_daily_profit=50.0, profit_target_action="PAUSE")
    cb.note_group_floating("boom", 60.0)
    hit = cb.check_profit_target(10_000.0)
    assert hit is not None and hit.group_ids == () and not hit.closes
    assert cb.take_pending_closes() == []


def test_close_and_pause_flattens_the_whole_slot():
    cb = _breaker(profit_target_scopes=["DAY"], profit_target_basis=BASIS_FLOATING,
                  max_daily_profit=50.0, profit_target_action=ACTION_CLOSE)
    cb.note_group_floating("boom", 20.0)
    cb.note_group_floating("crash", 40.0)
    hit = cb.check_profit_target(10_000.0)
    assert sorted(hit.group_ids) == ["boom", "crash"]


def test_a_trade_target_always_closes_even_under_pause():
    """A per-trade target that only paused new entries would leave the trade it
    was about to bank running to its stop."""
    cb = _breaker(profit_target_scopes=["TRADE"], profit_target_basis=BASIS_FLOATING,
                  max_trade_profit=50.0, profit_target_action="PAUSE")
    cb.note_group_floating("boom", 60.0)
    assert cb.check_profit_target(10_000.0).group_ids == ("boom",)


def test_a_period_pause_stops_new_entries_but_a_trade_hit_does_not():
    cb = _breaker(profit_target_scopes=["DAY"], max_daily_profit=50.0)
    now = datetime.now(timezone.utc)
    cb.check_all(10_000.0, now)          # a backtest breaker zeroes its day on the first check
    cb.daily_pnl = 60.0
    ok, reason = cb.check_all(10_000.0, now)
    assert not ok and "profit target" in reason.lower()

    cb2 = _breaker(profit_target_scopes=["TRADE"], profit_target_basis=BASIS_FLOATING,
                   max_trade_profit=50.0)
    cb2.check_all(10_000.0, now)
    cb2.note_group_floating("boom", 60.0)
    ok2, _ = cb2.check_all(10_000.0, now)
    assert ok2, "banking one trade must not stop the slot trading"


# ── amounts ─────────────────────────────────────────────────────────────────
def test_percent_amounts_measure_against_the_period_start_balance():
    cb = _breaker(profit_target_scopes=["WEEK"], profit_target_is_pct=True, max_weekly_profit=2.0)
    cb.weekly_pnl = 199.0
    assert cb.check_profit_target(10_000.0) is None
    cb.weekly_pnl = 200.0
    hit = cb.check_profit_target(10_000.0)
    assert hit.amount == pytest.approx(200.0)


def test_a_percent_target_with_no_known_balance_does_not_fire():
    """A percent of an unknown balance is a guess, not a target."""
    policy = ProfitTargetPolicy.from_config({
        "target_profit_enabled": True, "profit_target_scopes": ["DAY"],
        "profit_target_is_pct": True, "max_daily_profit": 5.0})
    assert policy.evaluate(realised={"DAY": 1e9}, floating_total=0.0, group_floating={},
                           period_start_balance={"DAY": 0.0}) is None


def test_a_zero_or_negative_amount_disarms_that_scope():
    cb = _breaker(profit_target_scopes=["DAY"], max_daily_profit=0.0)
    cb.daily_pnl = 10_000.0
    assert cb.check_profit_target(10_000.0) is None


def test_the_master_switch_disarms_everything():
    cb = CircuitBreaker({"target_profit_enabled": False, "profit_target_scopes": ["DAY"],
                         "max_daily_profit": 1.0}, is_backtest=True)
    cb.daily_pnl = 10_000.0
    assert cb.check_profit_target(10_000.0) is None


# ── config compatibility ────────────────────────────────────────────────────
def test_a_config_saved_before_this_change_still_means_what_it_meant():
    """Only `target_profit_enabled` / `max_daily_profit` / `max_weekly_profit`
    were ever written. Such a config must still pause on the daily target."""
    cb = CircuitBreaker({"target_profit_enabled": True, "max_daily_profit": 500.0,
                         "max_weekly_profit": 2000.0}, is_backtest=True)
    assert cb.profit_target.scopes == ("DAY",)
    assert cb.profit_target.basis == BASIS_BALANCE
    assert cb.profit_target.action == "PAUSE"
    now = datetime.now(timezone.utc)
    cb.check_all(10_000.0, now)
    cb.daily_pnl = 500.0
    ok, reason = cb.check_all(10_000.0, now)
    assert not ok and "Daily profit target" in reason, reason


def test_scopes_accept_a_comma_string_as_well_as_a_list():
    assert ProfitTargetPolicy.from_config({"profit_target_scopes": "TRADE,WEEK"}).scopes == \
        ("TRADE", "WEEK")


def test_an_unknown_scope_or_basis_falls_back_instead_of_raising():
    p = ProfitTargetPolicy.from_config({"profit_target_scopes": ["NONSENSE"],
                                        "profit_target_basis": "EQUITY",
                                        "profit_target_action": "SELL_EVERYTHING"})
    assert p.scopes == ("DAY",) and p.basis == BASIS_BALANCE and p.action == "PAUSE"


# ── rollover ────────────────────────────────────────────────────────────────
def test_the_month_rolls_over_and_re_anchors_its_balance():
    cb = CircuitBreaker({"target_profit_enabled": True, "profit_target_scopes": ["MONTH"],
                         "max_monthly_profit": 100.0}, is_backtest=True)
    t = datetime(2026, 1, 20, tzinfo=timezone.utc)
    cb.check_all(10_000.0, t)
    cb.monthly_pnl = 90.0
    assert cb.check_all(10_000.0, t)[0]
    cb.monthly_pnl = 100.0
    assert not cb.check_all(10_000.0, t)[0]
    cb.check_all(11_000.0, datetime(2026, 2, 3, tzinfo=timezone.utc))
    assert cb.monthly_pnl == 0.0
    assert cb._month_start_balance == 11_000.0
    assert not cb.is_paused, "a monthly pause must lift when the month rolls"


def test_monthly_pnl_accumulates_on_a_closed_group():
    cb = _breaker(profit_target_scopes=["MONTH"], max_monthly_profit=100.0)
    cb.position_closed("boom", 60.0, datetime.now(timezone.utc))
    assert cb.monthly_pnl == pytest.approx(60.0)
    assert cb.daily_pnl == pytest.approx(60.0) and cb.weekly_pnl == pytest.approx(60.0)


# ── note_and_check ──────────────────────────────────────────────────────────
def test_note_and_check_sums_legs_into_their_group():
    """A signal group is several TP legs; the target is on the group."""
    cb = _breaker(profit_target_scopes=["TRADE"], profit_target_basis=BASIS_FLOATING,
                  max_trade_profit=50.0)
    positions = [{"group_id": "boom", "n": 30.0}, {"group_id": "boom", "n": 25.0},
                 {"group_id": "crash", "n": 10.0}]
    got = note_and_check(cb, positions, lambda p: p["n"], 10_000.0)
    assert got == ["boom"], "30 + 25 = 55 on one group"


def test_note_and_check_survives_a_position_it_cannot_value():
    cb = _breaker(profit_target_scopes=["TRADE"], profit_target_basis=BASIS_FLOATING,
                  max_trade_profit=50.0)

    def boom(p):
        raise RuntimeError("no price")

    assert note_and_check(cb, [{"group_id": "boom"}], boom, 10_000.0) == []


# ── through the real backtester ─────────────────────────────────────────────
T0 = 1_767_571_200          # 2026-01-05 00:00 UTC, a Monday
BT_RISK = {
    "commission_per_lot": 0.0, "slippage_pips": 0.0, "exit_slippage_pips": 0.0,
    "spread_pips": 0.0, "stops_level_pips": 0.0, "swap_long_per_lot_per_day": 0.0,
    "swap_short_per_lot_per_day": 0.0, "stop_fill_model": "OFF",
    "risk_per_trade_pct": 1.0, "min_rr": 0.5, "max_risk_hard_cap_pct": 3.0,
    "tp_count": 1, "tp1_rr": 50.0, "tp_splits": "100",
    "be_mode": "NONE", "trail_mode": "NONE", "trail_method_tp1": "NONE",
    "max_daily_drawdown_pct": 100.0, "max_weekly_drawdown_pct": 100.0,
    "max_daily_trades": 50, "use_strategy_exit_defaults": False,
}


def _rising(n=200):
    """EURUSD marching steadily up, so a long runs to whatever target is set."""
    close = 1.1000 + np.arange(n) * 0.00020
    open_ = np.r_[1.1000, close[:-1]]
    t = T0 + 300 * np.arange(n)
    return pd.DataFrame({"time": t, "open": open_, "high": np.maximum(open_, close) + 1e-5,
                         "low": np.minimum(open_, close) - 1e-5, "close": close,
                         "spread": np.zeros(n), "tick_volume": np.full(n, 100.0)})


def _long_signal(df, i=5):
    return {"symbol": "EURUSD", "_cache_key": "EURUSD", "direction": "BUY",
            "time": int(df["time"][i]), "entry_price": float(df["close"][i]),
            "stop_loss": float(df["close"][i]) - 0.0020,
            "take_profit": float(df["close"][i]) + 0.1000,
            "timeframe": "M5", "confluence_score": 80, "metadata": {}}


def _run(extra):
    from backend.backtester.engine import BacktestEngine
    df = _rising()
    res = BacktestEngine({**BT_RISK, **extra}).run(df, [_long_signal(df)], 10_000.0,
                                                   df, df, None, None, None)
    return res.get("trades") or []


def test_the_backtester_closes_a_trade_on_its_floating_target():
    trades = _run({"target_profit_enabled": True, "profit_target_scopes": ["TRADE"],
                   "profit_target_basis": BASIS_FLOATING, "max_trade_profit": 25.0})
    assert len(trades) == 1
    t = trades[0]
    assert t["exit_reason"] == "PROFIT_TARGET", t["exit_reason"]
    assert t["pnl"] >= 25.0, "closed before it had earned the target"
    assert t["pnl"] < 60.0, "ran far past the target before closing"


def test_the_same_run_without_a_target_does_not_stop_there():
    """Proves the test above is about the target and not about the fixture."""
    trades = _run({"target_profit_enabled": False})
    assert len(trades) == 1
    assert trades[0]["exit_reason"] != "PROFIT_TARGET"
    assert trades[0]["pnl"] > 60.0, "should have run much further with no target"


def test_a_balance_basis_target_does_not_close_an_open_trade():
    """BALANCE counts closed trades only, so a single open winner never trips it."""
    trades = _run({"target_profit_enabled": True, "profit_target_scopes": ["TRADE"],
                   "profit_target_basis": BASIS_BALANCE, "max_trade_profit": 25.0})
    assert trades[0]["exit_reason"] != "PROFIT_TARGET"


# -- the portfolio path: two slots, one basket, no cross-contamination -------
def _pair_bars(n=200):
    """Two symbols that both rise, so both slots' longs go into profit — the
    situation where an account-wide reading would close the wrong one."""
    t = T0 + 300 * np.arange(n)
    out = {}
    for sym, step in (("EURUSD", 0.00020), ("GBPUSD", 0.00060)):
        close = 1.1000 + np.arange(n) * step
        open_ = np.r_[1.1000, close[:-1]]
        out[sym] = pd.DataFrame({
            "time": t, "open": open_, "high": np.maximum(open_, close) + 1e-5,
            "low": np.minimum(open_, close) - 1e-5, "close": close,
            "spread": np.zeros(n), "tick_volume": np.full(n, 100.0)})
    return out


def _pair_signals(data, i=5):
    return {sym: [{"symbol": sym, "_cache_key": sym, "direction": "BUY",
                   "time": int(df["time"][i]), "entry_price": float(df["close"][i]),
                   "stop_loss": float(df["close"][i]) - 0.0020,
                   "take_profit": float(df["close"][i]) + 0.1000,
                   "timeframe": "M5", "confluence_score": 80, "metadata": {}}]
            for sym, df in data.items()}


def _portfolio(slot_extra):
    from backend.backtester.portfolio_engine import PortfolioBacktestEngine
    data = _pair_bars()
    eng = PortfolioBacktestEngine({**BT_RISK})
    for sym in data:
        eng.book.set_slot_config(sym, {**BT_RISK, **slot_extra}, symbol=sym)
    res = eng.run(data, _pair_signals(data), 10_000.0)
    return res.get("trades") or []


def test_in_a_basket_only_the_slot_that_earned_its_target_is_closed():
    """GBPUSD moves three times as fast, so it reaches a $25 per-trade target
    well before EURUSD does. EURUSD must still be running at that moment — if
    the target read the account's floating equity, GBPUSD's profit would close
    it too."""
    trades = _portfolio({"target_profit_enabled": True, "profit_target_scopes": ["TRADE"],
                         "profit_target_basis": BASIS_FLOATING, "max_trade_profit": 25.0})
    by_symbol = {t["symbol"]: t for t in trades}
    assert set(by_symbol) == {"EURUSD", "GBPUSD"}
    for sym, t in by_symbol.items():
        assert t["exit_reason"] == "PROFIT_TARGET", f"{sym}: {t['exit_reason']}"
        assert t["pnl"] >= 25.0, f"{sym} closed at {t['pnl']:.2f}, below its own target"
    assert by_symbol["GBPUSD"]["exit_time"] < by_symbol["EURUSD"]["exit_time"], \
        "the faster symbol must reach its target first and not drag the other with it"


def test_a_basket_with_no_target_runs_both_much_further():
    trades = _portfolio({"target_profit_enabled": False})
    assert len(trades) == 2
    for t in trades:
        assert t["exit_reason"] != "PROFIT_TARGET"
        assert t["pnl"] > 60.0
