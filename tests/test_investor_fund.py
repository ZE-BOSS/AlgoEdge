"""
tests/test_investor_fund.py

Deposits, withdrawals, the 30%-of-monthly-profit cap, and reconciliation.

The rules being pinned here are the ones agreed on 2026-09-29:
  * units are issued at the CONFIRMED date's NAV, for the CONFIRMED amount
  * the withdrawal cap is 30% (admin-settable) of the investor's profit for the
    current WAT month — not of their capital
  * over the cap is an exception needing a written reason, not a refusal
  * settings are versioned, never edited in place
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
from backend.investor import fund as fundmod
from backend.investor import nav as navmod
from backend.investor import reconcile as recmod
from backend.investor import units as unitsmod
from backend.investor.models import (
    WITHDRAWAL_APPROVED,
    WITHDRAWAL_EXCEPTION,
    WITHDRAWAL_PAID,
    WITHDRAWAL_REQUESTED,
    Deposit,
    FundSettings,
    Investor,
)

D = Decimal
ADMIN = "admin-1"


def run(coro):
    return asyncio.run(coro)


async def _fresh():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
                                 connect_args={"check_same_thread": False})
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _investor(session, investor_id="a", name="A"):
    session.add(Investor(id=investor_id, email=f"{investor_id}@x.com",
                         name=name, status="pending"))
    await session.flush()


# ── settings ─────────────────────────────────────────────────────────────────

@pytest.mark.real_terms
def test_settings_default_to_the_agreed_terms():
    async def go():
        engine, S = await _fresh()
        async with S() as s:
            cfg = await fundmod.current_settings(s)
            assert cfg.version == 1
            assert D(str(cfg.withdrawal_cap_pct)) == D("30")
            assert navmod.money(cfg.min_investment) == D("3000.00")
            assert D(str(cfg.performance_fee_pct)) == D("50")
            assert D(str(cfg.management_fee_pct)) == D("5")
            assert cfg.lockup_days == 30 and cfg.notice_days == 7
            assert cfg.base_currency == "USD"
        await engine.dispose()
    run(go())


def test_changing_terms_writes_a_new_version_and_leaves_the_old_readable():
    """A lock-up is a promise; editing it in place would rewrite the terms of
    money already committed."""
    async def go():
        engine, S = await _fresh()
        async with S() as s:
            v1 = await fundmod.current_settings(s)
            v2 = await fundmod.new_settings_version(s, actor_id=ADMIN,
                                                    lockup_days=90,
                                                    withdrawal_cap_pct=D("25"))
            assert v2.version == 2 and v2.lockup_days == 90
            assert D(str(v2.withdrawal_cap_pct)) == D("25")
            # unchanged fields carry forward
            assert navmod.money(v2.min_investment) == D("200.00")
            # and v1 is still there, unchanged
            rows = (await s.execute(select(FundSettings).order_by(FundSettings.version))
                    ).scalars().all()
            assert len(rows) == 2 and rows[0].lockup_days == 30
            assert (await fundmod.current_settings(s)).version == 2
        await engine.dispose()
    run(go())


def test_an_unknown_settings_field_is_refused_rather_than_silently_dropped():
    async def go():
        engine, S = await _fresh()
        async with S() as s:
            await fundmod.current_settings(s)
            with pytest.raises(fundmod.FundError):
                await fundmod.new_settings_version(s, actor_id=ADMIN, perfromance_fee_pct=10)
        await engine.dispose()
    run(go())


# ── deposits ─────────────────────────────────────────────────────────────────

def test_an_admin_recorded_deposit_issues_units_and_activates_the_investor():
    async def go():
        engine, S = await _fresh()
        async with S() as s:
            await _investor(s)
            dep = await fundmod.record_admin_deposit(
                s, investor_id="a", amount=1000, actor_id=ADMIN, on=date(2026, 1, 5))
            assert navmod.money(dep.amount_confirmed) == D("1000.00")
            assert await unitsmod.ledger_units(s, "a") == D("10.00000000")
            inv = await s.get(Investor, "a")
            assert inv.status == "active" and inv.terms_version == 1
        await engine.dispose()
    run(go())


def test_a_deposit_below_the_minimum_is_refused():
    async def go():
        engine, S = await _fresh()
        async with S() as s:
            await _investor(s)
            with pytest.raises(fundmod.FundError, match="minimum"):
                await fundmod.record_admin_deposit(s, investor_id="a", amount=199,
                                                   actor_id=ADMIN)
        await engine.dispose()
    run(go())


def test_units_are_issued_for_the_confirmed_amount_not_the_claim():
    """The investor's claim is not evidence — what landed is."""
    async def go():
        engine, S = await _fresh()
        async with S() as s:
            await _investor(s)
            dep = Deposit(investor_id="a", amount_claimed=D("1000.00"),
                          state="claimed_sent")
            s.add(dep)
            await s.flush()
            await fundmod.confirm_deposit(s, deposit_id=dep.id, amount_confirmed=D("950.00"),
                                          actor_id=ADMIN, on=date(2026, 1, 5))
            assert await unitsmod.ledger_units(s, "a") == D("9.50000000")
        await engine.dispose()
    run(go())


def test_a_deposit_is_priced_at_its_effective_date_not_todays_nav():
    """A historic deposit entered during onboarding must buy units at the NAV
    that applied then, or the investor is handed profit they were not in for."""
    async def go():
        engine, S = await _fresh()
        async with S() as s:
            await _investor(s, "a")
            await _investor(s, "b", "B")
            # A subscribes at 100, the pool then doubles
            await fundmod.record_admin_deposit(s, investor_id="a", amount=1000,
                                               actor_id=ADMIN, on=date(2026, 1, 1))
            await fundmod.take_snapshot(s, pool_equity=2000, on=date(2026, 6, 1))
            assert await fundmod.latest_nav(s, date(2026, 6, 1)) == D("200.00000000")

            # B's historic deposit is dated BEFORE that snapshot, so it prices at 100
            await fundmod.record_admin_deposit(s, investor_id="b", amount=1000,
                                               actor_id=ADMIN, on=date(2026, 2, 1))
            assert await unitsmod.ledger_units(s, "b") == D("10.00000000")
        await engine.dispose()
    run(go())


def test_confirming_a_deposit_twice_is_refused():
    async def go():
        engine, S = await _fresh()
        async with S() as s:
            await _investor(s)
            dep = await fundmod.record_admin_deposit(s, investor_id="a", amount=1000,
                                                     actor_id=ADMIN)
            with pytest.raises(fundmod.FundError, match="already confirmed"):
                await fundmod.confirm_deposit(s, deposit_id=dep.id,
                                              amount_confirmed=1000, actor_id=ADMIN)
        await engine.dispose()
    run(go())


def test_a_confirmed_deposit_cannot_be_rejected():
    async def go():
        engine, S = await _fresh()
        async with S() as s:
            await _investor(s)
            dep = await fundmod.record_admin_deposit(s, investor_id="a", amount=1000,
                                                     actor_id=ADMIN)
            with pytest.raises(fundmod.FundError, match="correction"):
                await fundmod.reject_deposit(s, deposit_id=dep.id, reason="oops",
                                             actor_id=ADMIN)
        await engine.dispose()
    run(go())


# ── the withdrawal cap ───────────────────────────────────────────────────────

async def _funded_with_profit(s, *, deposit=1000, month_start_equity=1000,
                              now_equity=1500):
    """A holds `deposit`, the month opens at one equity and stands at another."""
    await _investor(s)
    await fundmod.record_admin_deposit(s, investor_id="a", amount=deposit,
                                       actor_id=ADMIN, on=date(2026, 8, 20))
    await fundmod.take_snapshot(s, pool_equity=month_start_equity, on=date(2026, 9, 1))
    await fundmod.take_snapshot(s, pool_equity=now_equity, on=date(2026, 9, 20))


def test_the_cap_is_thirty_percent_of_the_months_profit():
    async def go():
        engine, S = await _fresh()
        async with S() as s:
            await _funded_with_profit(s)          # 1000 -> 1500, profit 500
            check = await fundmod.withdrawal_cap(s, "a", date(2026, 9, 20))
            assert check.month_profit == D("500.00")
            assert check.cap == D("150.00")
            assert check.within
        await engine.dispose()
    run(go())


def test_a_flat_month_leaves_nothing_withdrawable_and_says_why():
    """A real customer-service edge: the UI must state the reason rather than
    just disabling a button."""
    async def go():
        engine, S = await _fresh()
        async with S() as s:
            await _funded_with_profit(s, now_equity=1000)     # no profit
            check = await fundmod.withdrawal_cap(s, "a", date(2026, 9, 20))
            assert check.cap == D("0.00") and not check.within
            assert "no profit this month" in check.explanation
            assert "exception" in check.explanation
        await engine.dispose()
    run(go())


def test_a_losing_month_does_not_produce_a_negative_cap():
    async def go():
        engine, S = await _fresh()
        async with S() as s:
            await _funded_with_profit(s, now_equity=600)
            check = await fundmod.withdrawal_cap(s, "a", date(2026, 9, 20))
            assert check.month_profit < 0
            assert check.cap == D("0.00")
        await engine.dispose()
    run(go())


def test_the_cap_percentage_is_admin_settable():
    async def go():
        engine, S = await _fresh()
        async with S() as s:
            await _funded_with_profit(s)
            await fundmod.new_settings_version(s, actor_id=ADMIN, withdrawal_cap_pct=D("50"))
            check = await fundmod.withdrawal_cap(s, "a", date(2026, 9, 20))
            assert check.cap == D("250.00")
        await engine.dispose()
    run(go())


def test_money_paid_in_this_month_is_capital_not_profit():
    """Otherwise the cap becomes a way to cycle a fresh deposit straight back
    out as if it were a gain."""
    async def go():
        engine, S = await _fresh()
        async with S() as s:
            await _investor(s)
            await fundmod.take_snapshot(s, pool_equity=0, on=date(2026, 9, 1))
            await fundmod.record_admin_deposit(s, investor_id="a", amount=5000,
                                               actor_id=ADMIN, on=date(2026, 9, 10))
            await fundmod.take_snapshot(s, pool_equity=5000, on=date(2026, 9, 20))
            check = await fundmod.withdrawal_cap(s, "a", date(2026, 9, 20))
            assert check.month_profit == D("0.00"), "a deposit is not profit"
            assert check.cap == D("0.00")
        await engine.dispose()
    run(go())


# ── withdrawal flow ──────────────────────────────────────────────────────────

def test_a_request_within_the_cap_is_ordinary():
    async def go():
        engine, S = await _fresh()
        async with S() as s:
            await _funded_with_profit(s)
            w = await fundmod.request_withdrawal(s, investor_id="a", amount=100,
                                                 on=date(2026, 9, 20))
            assert w.state == WITHDRAWAL_REQUESTED and not w.is_exception
            assert navmod.money(w.cap_at_request) == D("150.00")
        await engine.dispose()
    run(go())


def test_over_the_cap_without_a_reason_is_refused_with_the_numbers():
    async def go():
        engine, S = await _fresh()
        async with S() as s:
            await _funded_with_profit(s)
            with pytest.raises(fundmod.FundError) as err:
                await fundmod.request_withdrawal(s, investor_id="a", amount=400,
                                                 on=date(2026, 9, 20))
            assert "150" in str(err.value) and "written reason" in str(err.value)
        await engine.dispose()
    run(go())


def test_over_the_cap_with_a_reason_becomes_an_exception_not_a_refusal():
    async def go():
        engine, S = await _fresh()
        async with S() as s:
            await _funded_with_profit(s)
            w = await fundmod.request_withdrawal(
                s, investor_id="a", amount=400, justification="school fees",
                on=date(2026, 9, 20))
            assert w.state == WITHDRAWAL_EXCEPTION and w.is_exception
        await engine.dispose()
    run(go())


def test_nobody_can_withdraw_more_than_their_holding_is_worth():
    async def go():
        engine, S = await _fresh()
        async with S() as s:
            await _funded_with_profit(s)
            with pytest.raises(fundmod.FundError, match="cannot withdraw"):
                await fundmod.request_withdrawal(s, investor_id="a", amount=99_999,
                                                 justification="everything",
                                                 on=date(2026, 9, 20))
        await engine.dispose()
    run(go())


def test_approval_cancels_units_and_payment_is_a_separate_step():
    async def go():
        engine, S = await _fresh()
        async with S() as s:
            await _funded_with_profit(s)
            before = await unitsmod.ledger_units(s, "a")
            w = await fundmod.request_withdrawal(s, investor_id="a", amount=150,
                                                 on=date(2026, 9, 20))
            await fundmod.approve_withdrawal(s, withdrawal_id=w.id, actor_id=ADMIN,
                                             on=date(2026, 9, 20))
            assert w.state == WITHDRAWAL_APPROVED
            assert await unitsmod.ledger_units(s, "a") < before

            assert w.amount_paid is None, "approval is not payment"
            await fundmod.mark_withdrawal_paid(s, withdrawal_id=w.id, actor_id=ADMIN,
                                               reference="TRF-1")
            assert w.state == WITHDRAWAL_PAID
            assert navmod.money(w.amount_paid) == D("150.00")
        await engine.dispose()
    run(go())


def test_an_approved_withdrawal_cannot_be_approved_again():
    """Double approval would cancel the units twice."""
    async def go():
        engine, S = await _fresh()
        async with S() as s:
            await _funded_with_profit(s)
            w = await fundmod.request_withdrawal(s, investor_id="a", amount=100,
                                                 on=date(2026, 9, 20))
            await fundmod.approve_withdrawal(s, withdrawal_id=w.id, actor_id=ADMIN,
                                             on=date(2026, 9, 20))
            with pytest.raises(fundmod.FundError, match="already"):
                await fundmod.approve_withdrawal(s, withdrawal_id=w.id, actor_id=ADMIN,
                                                 on=date(2026, 9, 20))
        await engine.dispose()
    run(go())


def test_an_approved_withdrawal_cannot_be_declined_after_the_units_are_gone():
    async def go():
        engine, S = await _fresh()
        async with S() as s:
            await _funded_with_profit(s)
            w = await fundmod.request_withdrawal(s, investor_id="a", amount=100,
                                                 on=date(2026, 9, 20))
            await fundmod.approve_withdrawal(s, withdrawal_id=w.id, actor_id=ADMIN,
                                             on=date(2026, 9, 20))
            with pytest.raises(fundmod.FundError, match="correction"):
                await fundmod.decline_withdrawal(s, withdrawal_id=w.id,
                                                 reason="changed my mind", actor_id=ADMIN)
        await engine.dispose()
    run(go())


def test_paying_an_unapproved_withdrawal_is_refused():
    async def go():
        engine, S = await _fresh()
        async with S() as s:
            await _funded_with_profit(s)
            w = await fundmod.request_withdrawal(s, investor_id="a", amount=100,
                                                 on=date(2026, 9, 20))
            with pytest.raises(fundmod.FundError, match="approved"):
                await fundmod.mark_withdrawal_paid(s, withdrawal_id=w.id, actor_id=ADMIN)
        await engine.dispose()
    run(go())


# ── reconciliation ───────────────────────────────────────────────────────────

def test_a_clean_fund_reconciles():
    async def go():
        engine, S = await _fresh()
        async with S() as s:
            await _investor(s, "a")
            await _investor(s, "b", "B")
            await fundmod.record_admin_deposit(s, investor_id="a", amount=1000,
                                               actor_id=ADMIN, on=date(2026, 1, 1))
            await fundmod.record_admin_deposit(s, investor_id="b", amount=1000,
                                               actor_id=ADMIN, on=date(2026, 1, 1))
            await fundmod.take_snapshot(s, pool_equity=2000, on=date(2026, 1, 1))

            report = await recmod.build(s, pool_equity=2000, on=date(2026, 1, 1))
            assert report.healthy, report.as_dict()
        await engine.dispose()
    run(go())


def test_reconciliation_catches_money_that_belongs_to_nobody():
    async def go():
        engine, S = await _fresh()
        async with S() as s:
            await _investor(s, "a")
            await fundmod.record_admin_deposit(s, investor_id="a", amount=1000,
                                               actor_id=ADMIN, on=date(2026, 1, 1))
            await fundmod.take_snapshot(s, pool_equity=1000, on=date(2026, 1, 1))

            # the broker holds $1,500 but the NAV still says the pool is $1,000
            report = await recmod.build(s, pool_equity=1500, on=date(2026, 1, 1))
            assert not report.healthy
            equity_line = report.lines[0]
            assert equity_line.difference == D("500.00")
        await engine.dispose()
    run(go())


def test_reconciliation_catches_a_deposit_that_never_issued_units():
    """The equity line alone cannot see this; the second line exists for it."""
    async def go():
        engine, S = await _fresh()
        async with S() as s:
            await _investor(s, "a")
            await fundmod.record_admin_deposit(s, investor_id="a", amount=1000,
                                               actor_id=ADMIN, on=date(2026, 1, 1))
            await fundmod.take_snapshot(s, pool_equity=1000, on=date(2026, 1, 1))
            # a confirmed deposit recorded with no ledger row behind it
            s.add(Deposit(investor_id="a", amount_claimed=D("500.00"),
                          amount_confirmed=D("500.00"), state="confirmed",
                          effective_date=date(2026, 1, 2)))
            await s.flush()

            report = await recmod.build(s, pool_equity=1000, on=date(2026, 1, 1))
            assert not report.healthy
            deposit_line = report.lines[1]
            assert deposit_line.difference == D("500.00")
        await engine.dispose()
    run(go())


def test_reconciliation_is_unhealthy_while_the_cache_drifts():
    """Totals can balance while one investor's cached units are wrong — exactly
    the failure that stays hidden until somebody withdraws."""
    async def go():
        engine, S = await _fresh()
        async with S() as s:
            await _investor(s, "a")
            await fundmod.record_admin_deposit(s, investor_id="a", amount=1000,
                                               actor_id=ADMIN, on=date(2026, 1, 1))
            await fundmod.take_snapshot(s, pool_equity=1000, on=date(2026, 1, 1))
            from backend.investor.models import InvestorUnits
            cached = await s.get(InvestorUnits, "a")
            cached.units = D("99")
            await s.flush()

            report = await recmod.build(s, pool_equity=1000, on=date(2026, 1, 1))
            assert not report.healthy
            assert report.ledger_drift and report.ledger_drift[0][0] == "a"
        await engine.dispose()
    run(go())


def test_an_investor_statement_adds_up():
    async def go():
        engine, S = await _fresh()
        async with S() as s:
            await _investor(s, "a")
            await fundmod.record_admin_deposit(s, investor_id="a", amount=1000,
                                               actor_id=ADMIN, on=date(2026, 8, 1))
            await fundmod.take_snapshot(s, pool_equity=1500, on=date(2026, 9, 20))

            st = await recmod.investor_statement(s, "a", date(2026, 9, 20))
            assert st["capital_in"] == "1000.00"
            assert st["current_value"] == "1500.00"
            assert st["profit"] == "500.00"
            assert st["share_of_pool_pct"] == "100.00"
        await engine.dispose()
    run(go())


def test_profit_stays_correct_across_a_partial_withdrawal():
    """value + withdrawn - capital is the only definition that survives this."""
    async def go():
        engine, S = await _fresh()
        async with S() as s:
            await _investor(s, "a")
            await fundmod.record_admin_deposit(s, investor_id="a", amount=1000,
                                               actor_id=ADMIN, on=date(2026, 8, 1))
            await fundmod.take_snapshot(s, pool_equity=1500, on=date(2026, 9, 20))
            w = await fundmod.request_withdrawal(s, investor_id="a", amount=150,
                                                 on=date(2026, 9, 20))
            await fundmod.approve_withdrawal(s, withdrawal_id=w.id, actor_id=ADMIN,
                                             on=date(2026, 9, 20))
            await fundmod.mark_withdrawal_paid(s, withdrawal_id=w.id, actor_id=ADMIN)

            st = await recmod.investor_statement(s, "a", date(2026, 9, 20))
            assert st["withdrawn"] == "150.00"
            assert st["current_value"] == "1350.00"
            assert st["profit"] == "500.00", "taking money out is not losing it"
        await engine.dispose()
    run(go())


def test_a_falling_nav_between_request_and_approval_is_a_message_not_a_crash():
    """An ordinary business outcome: the request was affordable on Monday and is
    not on Friday. The admin must be shown why, not a 500."""
    async def go():
        engine, S = await _fresh()
        async with S() as s:
            await _funded_with_profit(s)                     # 10 units, NAV 150
            w = await fundmod.request_withdrawal(s, investor_id="a", amount=150,
                                                 on=date(2026, 9, 20))
            # the pool then collapses before the admin gets to the queue
            await fundmod.take_snapshot(s, pool_equity=100, on=date(2026, 9, 21))
            with pytest.raises(fundmod.FundError, match="no longer covers"):
                await fundmod.approve_withdrawal(s, withdrawal_id=w.id, actor_id=ADMIN,
                                                 on=date(2026, 9, 21))
        await engine.dispose()
    run(go())
