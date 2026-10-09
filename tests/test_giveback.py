"""
tests/test_giveback.py

"It was 3R up and it gave it all back."

WHY A HARD RULE AND NOT AN AGENT
--------------------------------
This is the answer to "the agent should watch the market every second". A
learned per-tick exit cannot be backtested here - `BarFeed` guarantees that
backtest and live see the same BAR sequence and there is no tick-level
equivalent - so a learned tick exit would be a decision path whose backtest
does not predict live. Three numbers and a comparison need no backtest to be
understood.

WHY IT IS OFF BY DEFAULT, AND WHY THE DEFAULTS ARE CONSERVATIVE
----------------------------------------------------------------
This project has measured what a well-meaning protective exit does to a real
edge: break-even at 1.5R turned 16 of 113 BTCUSD trades into BE_SL exits the
research never took. So the rule arms only at 2R of PEAK profit, which means
it can never turn a winner into a loser - only a big winner into a smaller one.
That property is tested below, because it is the whole safety argument.
"""

from __future__ import annotations

import pytest

from backend.risk.giveback import GiveBackRule, check, peak_r, r_of

LONG = {"entry_price": 100.0, "stop_loss": 99.0, "initial_stop_loss": 99.0,
        "direction": "BUY"}
SHORT = {"entry_price": 100.0, "stop_loss": 101.0, "initial_stop_loss": 101.0,
         "direction": "SELL"}

ON = {"giveback_enabled": True, "giveback_arm_at_r": 2.0, "giveback_pct": 50.0}


# ── R, measured from the ORIGINAL stop ─────────────────────────────────────

def test_r_is_measured_from_the_original_stop_not_the_current_one():
    """`stop_loss` is mutated in place by break-even and trailing, so by exit
    time it sits at entry. Dividing by that collapsed denominator is the
    measured bug that once reported +243.51R on a plain 1.5R target."""
    moved = dict(LONG, stop_loss=100.0)          # break-even has moved it
    assert r_of(moved, 103.0) == pytest.approx(3.0)


def test_r_works_both_ways_round():
    assert r_of(LONG, 102.0) == pytest.approx(2.0)
    assert r_of(SHORT, 98.0) == pytest.approx(2.0)
    assert r_of(LONG, 99.5) == pytest.approx(-0.5)


def test_a_degenerate_stop_gives_no_R_rather_than_infinity():
    assert r_of(dict(LONG, initial_stop_loss=100.0), 101.0) is None


def test_the_peak_prefers_the_engines_own_record():
    assert peak_r(dict(LONG, mfe_r=4.0), 102.0) == pytest.approx(4.0)
    assert peak_r(dict(LONG, mfe_r=1.0), 103.0) == pytest.approx(3.0)
    assert peak_r(LONG, 102.5) == pytest.approx(2.5)


# ── the rule ───────────────────────────────────────────────────────────────

def test_it_does_nothing_until_the_trade_has_been_well_in_profit():
    rule = GiveBackRule(enabled=True, arm_at_r=2.0, give_back_pct=50.0)
    assert rule.trigger_r(1.9) is None
    assert rule.should_close(1.9, 0.1) is False


def test_once_armed_it_keeps_half_the_peak():
    rule = GiveBackRule(enabled=True, arm_at_r=2.0, give_back_pct=50.0)
    assert rule.trigger_r(4.0) == pytest.approx(2.0)
    assert rule.should_close(4.0, 2.5) is False
    assert rule.should_close(4.0, 2.0) is True
    assert rule.should_close(4.0, 1.5) is True


def test_it_can_never_turn_a_winner_into_a_loser():
    """The whole safety argument, as a property over the whole input space."""
    rule = GiveBackRule(enabled=True, arm_at_r=2.0, give_back_pct=50.0)
    for peak in (2.0, 3.0, 5.0, 12.0, 100.0):
        level = rule.trigger_r(peak)
        assert level is not None and level > 0, peak


def test_a_hundred_percent_give_back_still_respects_the_floor():
    rule = GiveBackRule(enabled=True, arm_at_r=2.0, give_back_pct=100.0, floor_r=0.5)
    assert rule.trigger_r(4.0) == pytest.approx(0.5)


def test_it_is_off_unless_switched_on():
    assert GiveBackRule().enabled is False
    assert GiveBackRule().trigger_r(50.0) is None
    assert check(LONG, 105.0, {})[0] is False


def test_the_config_is_read_from_a_dict_or_an_object():
    from types import SimpleNamespace

    as_dict = GiveBackRule.from_config(ON)
    as_obj = GiveBackRule.from_config(SimpleNamespace(**ON))
    assert as_dict == as_obj
    assert as_dict.enabled and as_dict.arm_at_r == 2.0


# ── the whole check ────────────────────────────────────────────────────────

def test_a_trade_that_peaked_and_reversed_is_closed():
    """The case you described: a long profitable run that starts rejecting."""
    pos = dict(LONG, mfe_r=3.0)
    closed, why = check(pos, 101.4, ON)      # 3R peak, now 1.4R
    assert closed is True
    assert "peaked at 3.00R" in why and "1.40R" in why


def test_the_same_trade_is_left_alone_while_it_holds_its_gains():
    pos = dict(LONG, mfe_r=3.0)
    closed, why = check(pos, 102.5, ON)      # still 2.5R of a 3R peak
    assert closed is False
    assert "keeping at least" in why


def test_a_trade_that_never_got_going_is_untouched():
    pos = dict(LONG, mfe_r=0.8)
    closed, why = check(pos, 99.2, ON)       # went straight to a loss
    assert closed is False
    assert "not armed" in why


def test_it_works_on_a_short():
    pos = dict(SHORT, mfe_r=3.0)
    assert check(pos, 98.6, ON)[0] is True   # 3R peak, now 1.4R
    assert check(pos, 97.5, ON)[0] is False  # still 2.5R


def test_a_broken_position_never_raises_on_the_hot_path():
    """Called on every price update. An exception here is worse than the rule
    not firing."""
    for bad in ({}, {"entry_price": "x"}, {"entry_price": 0.0, "stop_loss": 0.0}):
        closed, why = check(bad, 100.0, ON)
        assert closed is False and isinstance(why, str)


def test_the_reason_is_always_readable():
    """It goes in the exit reason and the Journal, so it has to say what it did
    rather than only that it did something."""
    for price in (99.0, 101.0, 103.0, 106.0):
        _, why = check(dict(LONG, mfe_r=4.0), price, ON)
        assert why and not why.startswith("give-back check failed")
