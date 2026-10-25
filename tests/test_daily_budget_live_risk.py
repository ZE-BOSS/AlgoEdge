"""
The synthetic engines' own daily budget must not block the first trade when
the account risks more per trade than the budget allows - the live account
(5% per trade, budget 4%) was blocked from EVERY trade on every synthetic slot
(SpikeFade, BoomDriftJump, DriftJumpAlpha), 2026-10-19.
"""

from backend.strategies.core import daily_budget as db


def test_first_trade_of_the_day_is_never_blocked_by_a_small_budget():
    assert db.allows(0.0, 5.0, 4.0, strategy_id="SpikeFade_v1", symbol="Vol over Crash 750")


def test_after_the_first_trade_the_budget_applies_as_before():
    assert not db.allows(5.0, 5.0, 4.0)          # one a day at 5% vs 4%
    assert db.allows(1.8, 1.8, 4.0)              # demo: 1.8 + 1.8 <= 4
    assert not db.allows(3.6, 1.8, 4.0)          # third trade over budget
    assert db.allows(2.0, 2.0, 4.0)              # exactly at the cap is allowed


def test_synth_engine_lets_the_first_live_trade_through():
    import pandas as pd
    from backend.core.config_schema import UserConfigV2
    from backend.strategies.registry import get_strategy

    cfg = UserConfigV2()
    cfg.risk.risk_per_trade_pct = 5.0
    eng = get_strategy("SpikeFade_v1")(cfg)
    ok, why = eng._budget_available(pd.Timestamp("2026-10-19 10:00"), 5.0)
    assert ok, why


def test_a_zero_budget_still_means_no_entries():
    # 0% was how a synthetic strategy was paused; the first-trade exception
    # must not turn that into one trade a day.
    assert not db.allows(0.0, 1.0, 0.0)
    assert not db.allows(0.0, 5.0, 0.0)
