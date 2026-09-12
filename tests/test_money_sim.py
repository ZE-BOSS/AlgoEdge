"""The account simulator must size like position_sizer and share one balance."""

import pytest

from backend.analytics.money_sim import AccountRules, Contract, Leg, simulate_account

FX = {"X": Contract(value_per_price_per_lot=1000.0, min_lot=0.01, lot_step=0.01)}


def _legs(rs, stop=0.1, gap=3600):
    return [Leg("X", i * gap, i * gap + 600, r, stop, group=f"g{i}") for i, r in enumerate(rs)]


def test_static_sizing_risks_the_same_dollars_every_trade():
    s = simulate_account(_legs([1.0, 1.0]), FX, AccountRules(10_000, 1.0))
    assert s["final_balance"] == pytest.approx(10_200.0)


def test_compounding_sizes_from_the_current_balance():
    s = simulate_account(_legs([1.0, 1.0]), FX, AccountRules(10_000, 1.0, compounding=True))
    assert s["final_balance"] == pytest.approx(10_201.0)


def test_lots_are_floored_and_unsizable_trades_refused():
    big_stop = {"X": Contract(value_per_price_per_lot=1000.0, min_lot=0.01, lot_step=0.01)}
    # $3.50 budget; one min lot on a 1.0 stop risks $10 -> refused, not rounded up
    s = simulate_account(_legs([1.0], stop=1.0), big_stop, AccountRules(350, 1.0))
    assert s["trades"] == 0 and s["skipped_unsizable"] == 1


def test_daily_cap_blocks_entries_after_the_days_losses():
    legs = [Leg("X", i * 60, i * 60 + 30, -1.0, 0.1, group=f"g{i}") for i in range(6)]
    s = simulate_account(legs, FX, AccountRules(10_000, 2.0, daily_dd_cap_pct=5.0))
    # 2 + 2 = 4% lost, third is scaled to the remaining 1%, then blocked
    assert s["trades"] == 3 and s["blocked_daily_cap"] == 3
    assert s["final_balance"] == pytest.approx(9_500.0)


def test_pyramid_add_needs_its_base_open_and_the_rule_on():
    base = Leg("X", 0, 1000, 2.0, 0.1, group="g")
    add = Leg("X", 300, 1000, 1.0, 0.1, group="g", is_add=True)
    off = simulate_account([base, add], FX, AccountRules(10_000, 1.0))
    on = simulate_account([base, add], FX, AccountRules(10_000, 1.0, pyramiding=True))
    assert off["final_balance"] == pytest.approx(10_200.0)
    assert on["final_balance"] == pytest.approx(10_300.0) and on["adds_taken"] == 1


def test_drawdown_and_monthly_returns():
    s = simulate_account(_legs([1.0, -1.0, -1.0, 1.0]), FX, AccountRules(10_000, 1.0))
    assert s["max_dd_pct"] == pytest.approx(200 / 10_100 * 100, rel=1e-3)
    assert s["months"] == 1
