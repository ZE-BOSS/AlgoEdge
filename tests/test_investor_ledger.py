"""
tests/test_investor_ledger.py

The fund's arithmetic. If anything in this file is wrong, every investor
statement is wrong and nobody finds out until somebody withdraws.

The centrepiece is `test_a_late_investor_gets_no_retroactive_profit`, which is
the scenario that motivated unitisation in the first place
(implementation/INVESTOR-PLATFORM-2026-09-29.md §0). Percentage-of-capital
passes every other test in this file and fails that one, silently.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from backend.data.models import Base
from backend.investor import nav as navmod
from backend.investor import units as unitsmod
from backend.investor.models import (  # noqa: F401 — imported so create_all sees the tables
    Deposit,
    FundSettings,
    Investor,
    InvestorUnits,
    NavSnapshot,
    UnitTransaction,
    Withdrawal,
)

D = Decimal


def run(coro):
    return asyncio.run(coro)


async def _fresh():
    """A throwaway in-memory database with the investor tables.

    StaticPool because every async connection to `:memory:` otherwise gets its
    OWN empty database, and the tables would vanish between statements.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:",
                                 poolclass=StaticPool,
                                 connect_args={"check_same_thread": False})
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _investor(session, investor_id: str, name: str) -> Investor:
    inv = Investor(id=investor_id, email=f"{investor_id}@example.com",
                   name=name, status="active")
    session.add(inv)
    await session.flush()
    return inv


async def _value(session, investor_id: str, price) -> Decimal:
    held = await unitsmod.ledger_units(session, investor_id)
    return navmod.amount_for_units(held, price)


# ── NAV arithmetic ───────────────────────────────────────────────────────────

def test_an_empty_fund_opens_at_the_initial_nav():
    assert navmod.nav_per_unit(0, 0, 0) == navmod.INITIAL_NAV
    # and a pool holding cash but no units is still priced at the opening NAV,
    # because there is nobody to own the cash yet
    assert navmod.nav_per_unit(5000, 0, 0) == navmod.INITIAL_NAV


def test_nav_is_net_of_liabilities():
    # $10,000 of equity, $1,000 already owed out, 90 units -> $9,000 / 90
    assert navmod.nav_per_unit(10_000, 1_000, 90) == D("100.00000000")


def test_a_wiped_out_pool_is_worth_zero_not_something_comfortable():
    """Flooring NAV at a positive number would let a redemption pay out money
    the fund does not have."""
    assert navmod.nav_per_unit(0, 0, 50) == D("0E-8")
    assert navmod.nav_per_unit(500, 900, 50) == D("0E-8")


def test_units_round_down_so_a_deposit_cannot_dilute_everyone_else():
    """Rounding units UP would issue a sliver nobody paid for, on every deposit,
    diluting every existing holder by a hair each time."""
    issued = navmod.units_for_amount(D("100"), D("3"))     # 33.333...
    assert issued == D("33.33333333")
    assert issued * D("3") <= D("100")


def test_money_never_travels_through_a_float():
    """Decimal(0.1) is 0.1000000000000000055…; the ledger must not inherit that."""
    assert navmod.money(0.1) == D("0.10")
    assert navmod.money("0.005") == D("0.01")      # half-up, like a bank statement
    assert navmod.units(1 / 3) == D("0.33333333")  # and down, for units


def test_issuing_at_a_dead_nav_is_refused_rather_than_dividing_by_zero():
    with pytest.raises(ValueError):
        navmod.units_for_amount(1000, 0)


# ── the accounting day ───────────────────────────────────────────────────────

def test_the_accounting_day_is_west_african_not_utc():
    # 23:30 UTC is 00:30 the next day in WAT
    assert navmod.accounting_date(datetime(2026, 9, 29, 23, 30, tzinfo=timezone.utc)) \
        == date(2026, 9, 30)
    assert navmod.accounting_date(datetime(2026, 9, 29, 22, 0, tzinfo=timezone.utc)) \
        == date(2026, 9, 29)


def test_a_naive_timestamp_is_treated_as_utc():
    assert navmod.accounting_date(datetime(2026, 9, 29, 23, 30)) == date(2026, 9, 30)


@pytest.mark.parametrize("day,first,last", [
    (date(2026, 9, 15), date(2026, 9, 1), date(2026, 9, 30)),
    (date(2026, 2, 3), date(2026, 2, 1), date(2026, 2, 28)),
    (date(2024, 2, 9), date(2024, 2, 1), date(2024, 2, 29)),   # leap year
    (date(2026, 12, 31), date(2026, 12, 1), date(2026, 12, 31)),
])
def test_month_bounds(day, first, last):
    assert navmod.month_bounds(day) == (first, last)


# ── THE ONE THAT MATTERS ─────────────────────────────────────────────────────

def test_a_late_investor_gets_no_retroactive_profit():
    """§0's worked example, which is the whole case for unitisation.

    A and B put in $1,000 each. The pool trades to $3,000. C then deposits
    $1,000. Split by percentage-of-capital, C owns 25% of the pool and so 25% of
    the $1,000 lifetime profit — $250 that was earned before C's money existed,
    taken from A and B, with nothing in the UI looking wrong.

    With units, C simply buys in at the higher NAV and the arithmetic cannot
    produce that result.
    """
    async def go():
        engine, Session = await _fresh()
        async with Session() as s:
            for who in ("a", "b", "c"):
                await _investor(s, who, who.upper())

            # A and B subscribe into an empty fund at NAV 100
            price = navmod.nav_per_unit(0, 0, 0)
            assert price == D("100.00000000")
            await unitsmod.subscribe(s, investor_id="a", amount=1000, price=price,
                                     effective=date(2026, 1, 1),
                                     source_kind="deposit", source_id=1)
            await unitsmod.subscribe(s, investor_id="b", amount=1000, price=price,
                                     effective=date(2026, 1, 1),
                                     source_kind="deposit", source_id=2)
            assert await unitsmod.units_in_issue(s) == D("20.00000000")

            # the pool trades from $2,000 to $3,000
            price2 = navmod.nav_per_unit(3000, 0, await unitsmod.units_in_issue(s))
            assert price2 == D("150.00000000")

            # C deposits $1,000 at the NEW price
            await unitsmod.subscribe(s, investor_id="c", amount=1000, price=price2,
                                     effective=date(2026, 3, 1),
                                     source_kind="deposit", source_id=3)

            # nobody's value changed except by their own deposit
            assert await _value(s, "a", price2) == D("1500.00")
            assert await _value(s, "b", price2) == D("1500.00")
            c_value = await _value(s, "c", price2)
            assert c_value <= D("1000.00"), "C must not gain on the way in"
            assert c_value >= D("999.99"), "nor lose more than rounding"

            # the pool then makes a further $400
            total_units = await unitsmod.units_in_issue(s)
            price3 = navmod.nav_per_unit(4400, 0, total_units)
            gains = {who: await _value(s, who, price3) - base
                     for who, base in (("a", D("1500.00")), ("b", D("1500.00")),
                                       ("c", c_value))}

            # C's share of the NEW profit is proportional to units, and C's
            # LIFETIME gain never includes a cent earned before they joined
            assert sum(gains.values()) == pytest.approx(D("400"), abs=D("0.02"))
            assert gains["a"] == gains["b"]
            assert gains["a"] > gains["c"], "A was in for the earlier run-up, C was not"
            lifetime_c = await _value(s, "c", price3) - D("1000.00")
            assert lifetime_c < D("250.00"), (
                "percentage-of-capital would have handed C $250 of profit earned "
                "before their money arrived")
        await engine.dispose()
    run(go())


# ── subscribe / redeem ───────────────────────────────────────────────────────

def test_a_subscription_issues_units_and_updates_the_cache():
    async def go():
        engine, Session = await _fresh()
        async with Session() as s:
            await _investor(s, "a", "A")
            m = await unitsmod.subscribe(s, investor_id="a", amount=500,
                                         price=D("100"), effective=date(2026, 1, 1),
                                         source_kind="deposit", source_id=1)
            assert m.units == D("5.00000000")
            assert await unitsmod.ledger_units(s, "a") == D("5.00000000")
            cached = await s.get(InvestorUnits, "a")
            assert navmod.units(cached.units) == D("5.00000000")
        await engine.dispose()
    run(go())


def test_an_investor_cannot_redeem_more_than_they_hold():
    async def go():
        engine, Session = await _fresh()
        async with Session() as s:
            await _investor(s, "a", "A")
            await unitsmod.subscribe(s, investor_id="a", amount=1000, price=D("100"),
                                     effective=date(2026, 1, 1),
                                     source_kind="deposit", source_id=1)
            with pytest.raises(unitsmod.LedgerError):
                await unitsmod.redeem(s, investor_id="a", amount=1001, price=D("100"),
                                      effective=date(2026, 2, 1),
                                      source_kind="withdrawal", source_id=1)
        await engine.dispose()
    run(go())


def test_the_overdraw_check_reads_the_ledger_not_the_cache():
    """A stale cache must never be able to authorise a payout. Corrupt the cache
    to say the investor is rich and confirm the ledger still refuses."""
    async def go():
        engine, Session = await _fresh()
        async with Session() as s:
            await _investor(s, "a", "A")
            await unitsmod.subscribe(s, investor_id="a", amount=100, price=D("100"),
                                     effective=date(2026, 1, 1),
                                     source_kind="deposit", source_id=1)
            cached = await s.get(InvestorUnits, "a")
            cached.units = D("9999")
            await s.flush()
            with pytest.raises(unitsmod.LedgerError):
                await unitsmod.redeem(s, investor_id="a", amount=5000, price=D("100"),
                                      effective=date(2026, 2, 1),
                                      source_kind="withdrawal", source_id=1)
        await engine.dispose()
    run(go())


def test_a_full_redemption_lands_on_exactly_zero():
    """Converting a holding to cash and back leaves dust behind on almost every
    exit; `redeem_all` works in units so the register really does empty."""
    async def go():
        engine, Session = await _fresh()
        async with Session() as s:
            await _investor(s, "a", "A")
            await unitsmod.subscribe(s, investor_id="a", amount=1000, price=D("77.77"),
                                     effective=date(2026, 1, 1),
                                     source_kind="deposit", source_id=1)
            await unitsmod.redeem_all(s, investor_id="a", price=D("83.31"),
                                      effective=date(2026, 6, 1))
            assert await unitsmod.ledger_units(s, "a") == D("0E-8")
        await engine.dispose()
    run(go())


def test_a_zero_or_negative_movement_is_refused():
    async def go():
        engine, Session = await _fresh()
        async with Session() as s:
            await _investor(s, "a", "A")
            for bad in (0, -5):
                with pytest.raises(unitsmod.LedgerError):
                    await unitsmod.subscribe(s, investor_id="a", amount=bad,
                                             price=D("100"), effective=date(2026, 1, 1))
        await engine.dispose()
    run(go())


def test_a_deposit_too_small_to_buy_a_unit_is_refused_not_swallowed():
    """Taking money and issuing zero units would be theft by rounding."""
    async def go():
        engine, Session = await _fresh()
        async with Session() as s:
            await _investor(s, "a", "A")
            with pytest.raises(unitsmod.LedgerError):
                await unitsmod.subscribe(s, investor_id="a", amount=D("0.01"),
                                         price=D("100000000"),
                                         effective=date(2026, 1, 1))
        await engine.dispose()
    run(go())


# ── idempotency ──────────────────────────────────────────────────────────────

def test_the_same_deposit_cannot_issue_units_twice():
    """A retried admin click or a doubly-delivered webhook must not mint units
    again — and because the ledger is append-only there is no clean way to take
    them back afterwards."""
    async def go():
        engine, Session = await _fresh()
        async with Session() as s:
            await _investor(s, "a", "A")
            await unitsmod.subscribe(s, investor_id="a", amount=1000, price=D("100"),
                                     effective=date(2026, 1, 1),
                                     source_kind="deposit", source_id=42)
            with pytest.raises(Exception):
                await unitsmod.subscribe(s, investor_id="a", amount=1000,
                                         price=D("100"), effective=date(2026, 1, 1),
                                         source_kind="deposit", source_id=42)
        await engine.dispose()
    run(go())


# ── the nightly integrity check ──────────────────────────────────────────────

def test_a_healthy_ledger_reports_no_drift():
    async def go():
        engine, Session = await _fresh()
        async with Session() as s:
            for who in ("a", "b"):
                await _investor(s, who, who.upper())
                await unitsmod.subscribe(s, investor_id=who, amount=1000,
                                         price=D("100"), effective=date(2026, 1, 1),
                                         source_kind="deposit", source_id=hash(who) % 10_000)
            assert await unitsmod.assert_cache_matches_ledger(s) == []
        await engine.dispose()
    run(go())


def test_drift_between_cache_and_ledger_is_detected():
    """The check has to actually catch something, or it is decoration."""
    async def go():
        engine, Session = await _fresh()
        async with Session() as s:
            await _investor(s, "a", "A")
            await unitsmod.subscribe(s, investor_id="a", amount=1000, price=D("100"),
                                     effective=date(2026, 1, 1),
                                     source_kind="deposit", source_id=1)
            cached = await s.get(InvestorUnits, "a")
            cached.units = D("11")          # ledger says 10
            await s.flush()

            drift = await unitsmod.assert_cache_matches_ledger(s)
            assert len(drift) == 1
            investor_id, want, got = drift[0]
            assert investor_id == "a"
            assert want == D("10.00000000") and got == D("11.00000000")

            # and a rebuild repairs it from the ledger
            await unitsmod.refresh_cache(s, "a")
            assert await unitsmod.assert_cache_matches_ledger(s) == []
        await engine.dispose()
    run(go())


# ── corrections ──────────────────────────────────────────────────────────────

def test_a_correction_needs_a_reason_and_an_author():
    async def go():
        engine, Session = await _fresh()
        async with Session() as s:
            await _investor(s, "a", "A")
            await unitsmod.subscribe(s, investor_id="a", amount=1000, price=D("100"),
                                     effective=date(2026, 1, 1),
                                     source_kind="deposit", source_id=1)
            for bad in ("", "   "):
                with pytest.raises(unitsmod.LedgerError):
                    await unitsmod.correct(s, investor_id="a", unit_delta=1,
                                           price=D("100"), effective=date(2026, 2, 1),
                                           reason=bad, created_by="admin")
            with pytest.raises(unitsmod.LedgerError):
                await unitsmod.correct(s, investor_id="a", unit_delta=0, price=D("100"),
                                       effective=date(2026, 2, 1),
                                       reason="nothing changed", created_by="admin")
        await engine.dispose()
    run(go())


def test_history_is_never_edited_only_compensated():
    async def go():
        engine, Session = await _fresh()
        async with Session() as s:
            await _investor(s, "a", "A")
            await unitsmod.subscribe(s, investor_id="a", amount=1000, price=D("100"),
                                     effective=date(2026, 1, 1),
                                     source_kind="deposit", source_id=1)
            await unitsmod.correct(s, investor_id="a", unit_delta=D("-2"),
                                   price=D("100"), effective=date(2026, 2, 1),
                                   reason="duplicate of deposit 1, reversed",
                                   created_by="admin-1")

            rows = (await s.execute(
                __import__("sqlalchemy").select(UnitTransaction)
                .order_by(UnitTransaction.id))).scalars().all()
            assert len(rows) == 2, "the original row must still be there"
            assert rows[0].units == D("10.00000000")
            assert rows[1].units == D("-2.00000000")
            assert await unitsmod.ledger_units(s, "a") == D("8.00000000")
        await engine.dispose()
    run(go())


def test_a_correction_cannot_take_an_investor_negative():
    async def go():
        engine, Session = await _fresh()
        async with Session() as s:
            await _investor(s, "a", "A")
            await unitsmod.subscribe(s, investor_id="a", amount=100, price=D("100"),
                                     effective=date(2026, 1, 1),
                                     source_kind="deposit", source_id=1)
            with pytest.raises(unitsmod.LedgerError):
                await unitsmod.correct(s, investor_id="a", unit_delta=D("-99"),
                                       price=D("100"), effective=date(2026, 2, 1),
                                       reason="too big", created_by="admin")
        await engine.dispose()
    run(go())
