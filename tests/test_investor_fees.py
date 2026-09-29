"""
tests/test_investor_fees.py

Phase 4 — fees, and money the fund owes but has not yet paid out.

  * an approved-but-unpaid withdrawal must not inflate everyone else's NAV
    (a real Phase 1 bug: the other investor's $1,000 priced at $1,333.33)
  * management: value x rate x days / 365, pro-rated for a first part-month
  * performance: on lifetime profit above a per-investor high-water mark, the
    mark set NET of the fee so nobody pays twice
  * a charged fee is a liability until the manager takes it out
  * rates are the investor's committed terms, not today's
"""

from __future__ import annotations

import asyncio
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from backend.data.models import Base
from backend.investor import fees as feesmod
from backend.investor import fund as fundmod
from backend.investor import reconcile as recmod
from backend.investor import units as unitsmod
from backend.investor.models import FeeAccrual, Investor

D = Decimal
ADMIN = "admin-1"


def run(coro):
    return asyncio.run(coro)


def session_test(fn):
    def wrapper():
        async def go():
            engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
                                         connect_args={"check_same_thread": False})
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as s:
                await fn(s)
            await engine.dispose()
        run(go())
    wrapper.__name__ = fn.__name__
    return wrapper


async def _people(s, *ids):
    for i in ids:
        s.add(Investor(id=i, email=f"{i}@x.com", name=i.upper()))
    await s.flush()


async def _deposit(s, i, amount, on):
    await fundmod.record_admin_deposit(s, investor_id=i, amount=amount, actor_id=ADMIN, on=on)


async def _value(s, i, on):
    return D((await recmod.investor_statement(s, i, on))["current_value"])


# ── money owed out ───────────────────────────────────────────────────────────

@session_test
async def test_an_unpaid_withdrawal_does_not_inflate_everyone_elses_nav(s):
    await _people(s, "a", "b")
    await _deposit(s, "a", 1000, date(2026, 6, 1))
    await _deposit(s, "b", 1000, date(2026, 6, 1))
    w = await fundmod.request_withdrawal(s, investor_id="a", amount=500, justification="x",
                                         on=date(2026, 9, 10))
    await fundmod.approve_withdrawal(s, withdrawal_id=w.id, actor_id=ADMIN, on=date(2026, 9, 10))
    # approved, not yet paid: the $500 is still in the broker account
    snap = await fundmod.take_snapshot(s, pool_equity=2000, on=date(2026, 9, 11))
    assert snap.liabilities == D("500.00")
    assert await _value(s, "b", date(2026, 9, 11)) == D("1000.00")

    await fundmod.mark_withdrawal_paid(s, withdrawal_id=w.id, actor_id=ADMIN, reference="T",
                                       on=date(2026, 9, 20))
    # paid: the money has left, and the price does not move
    snap = await fundmod.take_snapshot(s, pool_equity=1500, on=date(2026, 9, 30))
    assert snap.liabilities == D("0.00") and await _value(s, "b", date(2026, 9, 30)) == D("1000.00")


@session_test
async def test_a_backdated_snapshot_counts_what_was_owed_on_that_day(s):
    await _people(s, "a", "b")
    await _deposit(s, "a", 1000, date(2026, 6, 1))
    await _deposit(s, "b", 1000, date(2026, 6, 1))
    w = await fundmod.request_withdrawal(s, investor_id="a", amount=500, justification="x",
                                         on=date(2026, 9, 10))
    await fundmod.approve_withdrawal(s, withdrawal_id=w.id, actor_id=ADMIN, on=date(2026, 9, 10))
    assert await fundmod.owed_on(s, date(2026, 9, 9)) == D("0.00")      # before approval
    assert await fundmod.owed_on(s, date(2026, 9, 10)) == D("500.00")


# ── management fee ───────────────────────────────────────────────────────────

@session_test
async def test_management_fee_is_a_share_of_the_periods_profit(s):
    """Flat to the end of June, +1,000 by the end of August: the Jul-Aug period
    made 1,000, so management is 2% of that; performance is then 20% of the
    lifetime profit left after management."""
    await _people(s, "a")
    await _deposit(s, "a", 10_000, date(2026, 6, 1))
    await fundmod.take_snapshot(s, pool_equity=10_000, on=date(2026, 6, 30))
    await fundmod.take_snapshot(s, pool_equity=11_000, on=date(2026, 8, 31))
    start, end = feesmod.fee_period(2026, 8)
    line = await feesmod.compute(s, "a", start, end)
    assert (start, end) == (date(2026, 7, 1), date(2026, 8, 31))
    assert line.value_before == D("10000.00") and line.period_profit == D("1000.00")
    assert line.management == D("20.00")            # 2% of 1,000
    assert line.profit == D("980.00")               # 11,000 - 20 - 10,000
    assert line.performance == D("196.00")          # 20% of 980, never of the 20 already taken


@session_test
async def test_no_management_fee_in_a_losing_period(s):
    await _people(s, "a")
    await _deposit(s, "a", 10_000, date(2026, 6, 1))
    await fundmod.take_snapshot(s, pool_equity=10_000, on=date(2026, 6, 30))
    await fundmod.take_snapshot(s, pool_equity=9_500, on=date(2026, 8, 31))
    line = await feesmod.compute(s, "a", *feesmod.fee_period(2026, 7))
    assert line.period_profit == D("-500.00")
    assert line.management == D("0.00") and line.performance == D("0.00")


@session_test
async def test_money_added_during_the_period_is_not_profit(s):
    await _people(s, "a")
    await _deposit(s, "a", 10_000, date(2026, 6, 1))
    await fundmod.take_snapshot(s, pool_equity=10_000, on=date(2026, 6, 30))
    await _deposit(s, "a", 5_000, date(2026, 7, 15))        # at 100 a unit
    await fundmod.take_snapshot(s, pool_equity=15_600, on=date(2026, 8, 31))
    line = await feesmod.compute(s, "a", *feesmod.fee_period(2026, 8))
    assert line.period_profit == D("600.00")                # 15,600 - 10,000 - 5,000
    assert line.management == D("12.00")


@session_test
async def test_a_first_deposit_inside_the_period_counts_from_what_it_paid_in(s):
    await _people(s, "a")
    await _deposit(s, "a", 10_000, date(2026, 8, 21))
    await fundmod.take_snapshot(s, pool_equity=10_200, on=date(2026, 8, 31))
    line = await feesmod.compute(s, "a", *feesmod.fee_period(2026, 8))
    assert line.value_before == D("0.00") and line.period_profit == D("200.00")
    assert line.days == 11 and line.management == D("4.00")


@pytest.mark.real_terms
@session_test
async def test_the_agreed_terms_5pct_of_period_profit_then_50pct(s):
    await _people(s, "a")
    await _deposit(s, "a", 10_000, date(2026, 6, 1))
    await fundmod.take_snapshot(s, pool_equity=10_000, on=date(2026, 6, 30))
    await fundmod.take_snapshot(s, pool_equity=11_000, on=date(2026, 8, 31))
    line = await feesmod.compute(s, "a", *feesmod.fee_period(2026, 8))
    assert line.management == D("50.00")            # 5% of the period's 1,000
    assert line.performance == D("475.00")          # 50% of the 950 left
    assert line.total == D("525.00")


@session_test
async def test_an_investors_own_rates_are_the_ones_charged(s):
    await _people(s, "a")
    await _deposit(s, "a", 10_000, date(2026, 6, 1))
    inv = await s.get(Investor, "a")
    inv.performance_fee_pct, inv.management_fee_pct = D("30"), D("0")
    await fundmod.take_snapshot(s, pool_equity=10_000, on=date(2026, 6, 30))
    await fundmod.take_snapshot(s, pool_equity=11_000, on=date(2026, 8, 31))
    line = await feesmod.compute(s, "a", *feesmod.fee_period(2026, 8))
    assert line.management == D("0.00") and line.performance == D("300.00")
    terms = await fundmod.terms_for(s, "a")
    assert sorted(terms.overridden) == ["management_fee_pct", "performance_fee_pct"]


def test_fee_periods_are_pairs_of_months():
    assert feesmod.fee_period(2026, 1) == (date(2026, 1, 1), date(2026, 2, 28))
    assert feesmod.fee_period(2026, 2) == (date(2026, 1, 1), date(2026, 2, 28))
    assert feesmod.fee_period(2028, 2) == (date(2028, 1, 1), date(2028, 2, 29))
    assert feesmod.fee_period(2026, 12) == (date(2026, 11, 1), date(2026, 12, 31))
    assert feesmod.previous_fee_period(date(2026, 9, 29)) == (date(2026, 7, 1), date(2026, 8, 31))
    assert feesmod.previous_fee_period(date(2026, 1, 5)) == (date(2025, 11, 1), date(2025, 12, 31))


# ── performance fee and the high-water mark ─────────────────────────────────

async def _month(s, year, month, equity):
    start, end = feesmod.month_period(year, month)
    await fundmod.take_snapshot(s, pool_equity=equity, on=end)
    await feesmod.close_period(s, start, end, actor_id=ADMIN)
    return end


@session_test
async def test_the_high_water_mark_means_a_loss_is_earned_back_before_any_fee(s):
    await _people(s, "a")
    await _deposit(s, "a", 10_000, date(2026, 5, 31))
    # management off, to follow the performance fee on its own
    await fundmod.new_settings_version(s, actor_id=ADMIN, management_fee_pct=0)
    inv = await s.get(Investor, "a")
    inv.terms_version = 2

    # June: +1,000 -> fee 20% = 200; the mark is set NET of it, at 800
    await _month(s, 2026, 6, 11_000)
    perf = (await s.execute(select(FeeAccrual).where(FeeAccrual.kind == "PERFORMANCE")
                            .order_by(FeeAccrual.period_end))).scalars().all()
    assert perf[-1].fee_amount == D("200.00") and D(str(perf[-1].high_water_mark)) == D("800.00")

    # July: flat after the fee. With the mark net of the fee, nothing is due —
    # the investor does not pay again on the 200 they already paid
    await _month(s, 2026, 7, 11_000)
    assert (await feesmod.high_water_mark(s, "a")) == D("800.00")
    july = [p for p in (await s.execute(select(FeeAccrual))).scalars() if p.period_end == date(2026, 7, 31)]
    assert all(p.fee_amount == D("0.00") for p in july)

    # (The June fee is still in the broker account, owed to the manager, so
    # equity E prices Ada's holding at E - 200 from here on.)
    # August: a loss. September: recovering but still below the mark -> no fee.
    # October: above it -> fee only on the part above.
    await _month(s, 2026, 8, 9_800)
    sept = await _month(s, 2026, 9, 10_800)
    assert (await feesmod.compute(s, "a", date(2026, 9, 1), sept)) is not None
    sep_perf = (await s.execute(select(FeeAccrual).where(FeeAccrual.kind == "PERFORMANCE",
                                                         FeeAccrual.period_end == sept))).scalar_one()
    assert sep_perf.fee_amount == D("0.00")
    await _month(s, 2026, 10, 12_000)
    oct_perf = (await s.execute(select(FeeAccrual).where(
        FeeAccrual.kind == "PERFORMANCE", FeeAccrual.period_end == date(2026, 10, 31)))).scalar_one()
    # equity 12,000 - 200 owed = holding 11,800 -> profit 1,800; above the
    # 800 mark by 1,000 -> fee 20% = 200
    assert oct_perf.fee_amount == D("200.00")


@session_test
async def test_a_charged_fee_is_owed_until_paid_and_does_not_move_other_investors(s):
    await _people(s, "a", "b")
    await _deposit(s, "a", 1000, date(2026, 5, 31))
    await _deposit(s, "b", 1000, date(2026, 5, 31))
    end = await _month(s, 2026, 6, 2200)                        # +10%
    charged = await fundmod.owed_on(s, end)
    assert charged > 0
    b_before = await _value(s, "b", end)

    # next day, same broker equity: the fees are still in the account and owed
    snap = await fundmod.take_snapshot(s, pool_equity=2200, on=date(2026, 7, 1))
    assert snap.liabilities == charged
    assert await _value(s, "b", date(2026, 7, 1)) == b_before
    rec = await recmod.build(s, pool_equity=2200, on=date(2026, 7, 1))
    assert rec.healthy, rec.as_dict()

    await feesmod.mark_paid(s, date(2026, 6, 1), end, actor_id=ADMIN, reference="MGR-1",
                            on=date(2026, 7, 2))
    snap = await fundmod.take_snapshot(s, pool_equity=D("2200") - charged, on=date(2026, 7, 2))
    assert snap.liabilities == D("0.00") and await _value(s, "b", date(2026, 7, 2)) == b_before


@session_test
async def test_a_month_is_charged_once_and_needs_a_fresh_price(s):
    await _people(s, "a")
    await _deposit(s, "a", 1000, date(2026, 5, 31))
    with pytest.raises(fundmod.FundError, match="snapshot"):
        await feesmod.close_period(s, date(2026, 6, 1), date(2026, 6, 30), actor_id=ADMIN)
    await _month(s, 2026, 6, 1100)
    with pytest.raises(fundmod.FundError, match="already charged"):
        await feesmod.close_period(s, date(2026, 6, 1), date(2026, 6, 30), actor_id=ADMIN)


@session_test
async def test_a_waiver_records_the_fee_charges_nothing_and_still_moves_the_mark(s):
    await _people(s, "a")
    await _deposit(s, "a", 1000, date(2026, 5, 31))
    units_before = await unitsmod.ledger_units(s, "a")
    start, end = feesmod.month_period(2026, 6)
    await fundmod.take_snapshot(s, pool_equity=1100, on=end)
    await feesmod.close_period(s, start, end, actor_id=ADMIN, waive={"a"})
    assert await unitsmod.ledger_units(s, "a") == units_before
    rows = (await s.execute(select(FeeAccrual))).scalars().all()
    assert {r.state for r in rows} == {"waived"} and await fundmod.owed_on(s, end) == 0
    assert await feesmod.high_water_mark(s, "a") > 0


@session_test
async def test_fee_rates_are_the_committed_terms_not_todays(s):
    await _people(s, "a")
    await _deposit(s, "a", 10_000, date(2026, 5, 31))          # under v1: 2% / 20%
    await fundmod.new_settings_version(s, actor_id=ADMIN, management_fee_pct=5,
                                       performance_fee_pct=50)
    await fundmod.take_snapshot(s, pool_equity=11_000, on=date(2026, 6, 30))
    line = await feesmod.compute(s, "a", date(2026, 6, 1), date(2026, 6, 30))
    assert line.management_pct == D("2") and line.performance_pct == D("20")
