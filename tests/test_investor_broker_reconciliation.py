"""
tests/test_investor_broker_reconciliation.py

Does what investors are told match what the broker actually holds?

WHY THIS FILE EXISTS
--------------------
It did not, and the consequence was measurable. On the VPS database, 2026-10-02:

    confirmed deposits        5,536.00   (4 deposits, 2 investors, all OFF_PLATFORM)
    units in issue               55.36   = 5,536 / 100.00
    NAV per unit 2026-10-01     110.38
    investor claims           6,110.62
    trading account balance   3,903.11
    ----------------------------------
    SHORTFALL                 2,207.51

Nothing showed it, for three compounding reasons:

  1. every `nav_snapshots` row had `source='TRADES'` — the NAV had only ever
     been moved by booked trade results and never read from an account;
  2. `/reconciliation` fell back to that snapshot, so it compared investor
     claims against a figure derived from the same bookings and could not
     disagree with itself;
  3. the journal could not be split per MT5 account, so there was no per-account
     balance to compare against anyway.

The most likely explanation is innocent — all four deposits are `OFF_PLATFORM`,
money recorded as already paid, and `reconcile.py`'s own docstring names
"deposits confirmed but not yet funded to the broker" as THE legitimate
non-zero difference. The defect was never that the gap existed. It was that the
line existed and nothing populated it.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.data.models import Base
from backend.investor import fund as fundmod
from backend.investor import reconcile as recmod
from backend.investor import sources as srcmod
from backend.investor.models import Investor
from backend.mt5 import history as histmod
from backend.mt5.cash_models import CAPITAL_IN, MT5Account, OpeningAnchor

D = Decimal
LOGIN = 6194570


class Fund:
    async def __aenter__(self):
        self._tmp = tempfile.mkdtemp(prefix="algoedge-recon-")
        self.engine = create_async_engine(
            f"sqlite+aiosqlite:///{Path(self._tmp).as_posix()}/f.db")
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        return self

    async def __aexit__(self, *exc):
        await self.engine.dispose()
        shutil.rmtree(self._tmp, ignore_errors=True)


def run(coro):
    return asyncio.run(coro)


def _deal(**kw):
    from types import SimpleNamespace

    base = dict(ticket=1, type=2, entry=1, position_id=0, order=0, symbol="",
                volume=0.0, price=0.0, profit=0.0, commission=0.0, swap=0.0,
                fee=0.0, comment="", magic=0, time=1759000000)
    base.update(kw)
    return SimpleNamespace(**base)


async def _two_investors(site, *, deposits: list[Decimal]):
    """The VPS shape: deposits confirmed off-platform, units issued at 100."""
    async with site.Session() as s:
        for i, amount in enumerate(deposits):
            iid = f"inv-{i}"
            s.add(Investor(id=iid, email=f"{iid}@x.com", name=f"Investor {i}"))
            await s.flush()
            await fundmod.record_admin_deposit(
                s, investor_id=iid, amount=amount, actor_id="admin",
                on=date(2026, 9, 29))
        await s.commit()


async def _broker_account(site, *, funded: Decimal, holds_capital=True):
    """An MT5 account that received `funded` and nothing else."""
    async with site.Session() as s:
        s.add(MT5Account(login=LOGIN, broker="Deriv", holds_investor_capital=holds_capital,
                         deal_class_overrides=json.dumps({})))
        s.add(OpeningAnchor(account_login=LOGIN, as_of=datetime(2026, 9, 29),
                            balance=0.0, source="manual"))
        await s.flush()
        await histmod.store_deals(s, LOGIN, [
            _deal(ticket=1, type=2, profit=float(funded), comment="Deposit")])
        await s.commit()


# ── the gap is found ────────────────────────────────────────────────────────

def test_the_vps_fund_is_covered_once_cash_in_transit_counts():
    """The real numbers, and the correction building this produced.

    $5,536 was confirmed in; only $3,266.92 ever reached the trading account.
    Valuing the pool at broker cash alone makes that look like a $2,207
    shortfall — which is how I first read it. It is not one. The $2,269 is
    fund cash in a bank account, and counting it:

        3,903.11 at the broker + 2,269.08 in transit = 6,172.19
        against claims of 6,110.62  ->  covered, with 61.57 to spare

    The defect was never a missing $2,207. It was that nothing computed this,
    so neither answer was available.
    """
    async def go():
        async with Fund() as site:
            await _two_investors(site, deposits=[D("2686"), D("1550"), D("700"), D("600")])
            await _broker_account(site, funded=D("3266.92"))

            async with site.Session() as s:
                in_transit = await srcmod.unfunded_capital(s)
                pool, _ = await srcmod.pool_value(s)
                gap = await recmod.shortfall(s, pool_equity=pool)

            assert in_transit == D("2269.08"), "confirmed in, never sent to the broker"
            assert pool == D("5536.00"), "broker cash plus cash on its way"
            assert gap["shortfall"] == "0.00"
            assert gap["is_covered"] is True

            # Valuing at broker cash ALONE is the mistake, kept here so the
            # difference between the two readings stays visible.
            async with site.Session() as s:
                wrong = await recmod.shortfall(s, pool_equity=D("3266.92"))
            assert wrong["shortfall"] == "2269.08", (
                "this is the figure that looks like a hole and is not one")
    run(go())


def test_the_capital_in_line_shows_what_never_reached_the_broker():
    async def go():
        async with Fund() as site:
            await _two_investors(site, deposits=[D("2686"), D("1550"), D("700"), D("600")])
            await _broker_account(site, funded=D("3266.92"))

            async with site.Session() as s:
                report = await recmod.build(s, pool_equity=D("3903.11"))

            line = next(ln for ln in report.lines
                        if ln.label == "Broker capital in vs confirmed deposits")
            assert line.pool == D("3266.92"), "what the broker actually received"
            assert line.investor_facing == D("5536.00"), "what investors were told"
            assert line.difference == D("-2269.08")
            # Visible, but NOT a fault: money in transit is the one difference
            # this module's docstring has always called legitimate. `balanced`
            # is the arithmetic, `ok` is whether anyone needs to act.
            assert line.balanced is False
            assert line.must_balance is False
            assert line.ok is True
            assert "not been transferred" in line.means
    run(go())


def test_a_fully_funded_pool_reconciles():
    """The same machinery must be able to say yes."""
    async def go():
        async with Fund() as site:
            await _two_investors(site, deposits=[D("1000")])
            await _broker_account(site, funded=D("1000"))

            async with site.Session() as s:
                report = await recmod.build(s, pool_equity=D("1000"))
                gap = await recmod.shortfall(s, pool_equity=D("1000"))

            assert report.lines_agree is True, [
                (ln.label, str(ln.difference)) for ln in report.lines if not ln.ok]
            assert report.broker_configured is True
            assert report.healthy is True
            assert gap["shortfall"] == "0.00" and gap["is_covered"] is True
    run(go())


# ── the ways this must refuse to look clean ────────────────────────────────

def test_with_no_broker_account_nothing_claims_to_be_verified():
    """The state the platform was in: a reconciliation comparing the ledger
    with itself."""
    async def go():
        async with Fund() as site:
            await _two_investors(site, deposits=[D("1000")])
            async with site.Session() as s:
                report = await recmod.build(s, pool_equity=D("1000"))
            assert report.lines_agree is True, "the internal arithmetic is fine"
            assert report.broker_configured is False
            assert report.healthy is False, (
                "nothing outside the ledger has confirmed a single figure")
    run(go())


def test_an_unreviewed_deal_stops_the_report_being_healthy():
    async def go():
        async with Fund() as site:
            await _two_investors(site, deposits=[D("1000")])
            await _broker_account(site, funded=D("1000"))
            async with site.Session() as s:
                # a broker CORRECTION: could be capital or a cost, so the
                # classifier refuses to guess
                await histmod.store_deals(s, LOGIN, [
                    _deal(ticket=99, type=5, profit=0.0, comment="correction")])
                await s.commit()
            async with site.Session() as s:
                report = await recmod.build(s, pool_equity=D("1000"))
            assert report.lines_agree is True
            assert report.broker_complete is False
            assert report.healthy is False
            assert any("review" in m for m in report.broker_missing)
    run(go())


def test_an_account_with_no_anchor_stops_the_report_being_healthy():
    async def go():
        async with Fund() as site:
            await _two_investors(site, deposits=[D("1000")])
            async with site.Session() as s:
                s.add(MT5Account(login=LOGIN, holds_investor_capital=True))
                await s.commit()
            async with site.Session() as s:
                report = await recmod.build(s, pool_equity=D("1000"))
            assert report.broker_complete is False
            assert any("anchor" in m for m in report.broker_missing)
    run(go())


def test_a_prop_account_does_not_back_investor_units():
    """FundedNext's $25,000 is the firm's money under a profit split.

    Counting it as capital available to investors would overstate the fund by
    exactly that much, so an account only contributes when it is marked.
    """
    async def go():
        async with Fund() as site:
            await _two_investors(site, deposits=[D("1000")])
            await _broker_account(site, funded=D("25000"), holds_capital=False)
            async with site.Session() as s:
                side = await srcmod.broker_side(s)
            assert side.logins == (), "an unmarked account contributes nothing"
            assert side.capital_in == D("0.00")
            assert side.configured is False
    run(go())


def test_the_ledger_equity_is_derived_from_the_brokers_own_deals():
    """So `/reconciliation` has an independent figure and never has to fall
    back on a NAV it produced itself."""
    async def go():
        async with Fund() as site:
            await _two_investors(site, deposits=[D("1000")])
            await _broker_account(site, funded=D("1000"))
            async with site.Session() as s:
                equity, source = await srcmod.live_equity(s)
            assert source == "ledger"
            assert equity == D("1000.00")
    run(go())


@pytest.mark.parametrize("claims,equity,owed,expected", [
    (D("6110.62"), D("3903.11"), D("0"), "2207.51"),   # the measured VPS gap
    (D("1000"), D("1000"), D("0"), "0.00"),
    (D("1000"), D("1200"), D("0"), "-200.00"),         # covered, with room
    (D("1000"), D("1100"), D("150"), "50.00"),         # money already promised out
])
def test_the_shortfall_arithmetic(claims, equity, owed, expected):
    """shortfall = claims - (equity - owed). Pinned, because this is the
    number somebody will be asked to cover."""
    from backend.investor import nav as navmod

    got = navmod.money(claims - navmod.money(equity - owed))
    assert navmod.text(got) == expected


# ── the daily broker valuation ──────────────────────────────────────────────
#
# The job whose absence hid the gap. Every nav_snapshots row on the VPS has
# source='TRADES' — a price moved only by booked results, never measured
# against an account — and /reconciliation fell back to it, so it compared the
# ledger with itself.

from backend.investor import jobs as jobsmod
from backend.investor.models import NavSnapshot


def test_the_valuation_writes_a_snapshot_the_broker_can_vouch_for():
    async def go():
        async with Fund() as site:
            await _two_investors(site, deposits=[D("1000")])
            await _broker_account(site, funded=D("1200"))
            async with site.Session() as s:
                out = await jobsmod.broker_valuation(s, date(2026, 10, 8))

            assert out and "pool_equity" in out, out
            assert out["pool_equity"] == "1200.00"
            async with site.Session() as s:
                snap = (await s.execute(
                    select(NavSnapshot).where(NavSnapshot.as_of_date == date(2026, 10, 8))
                )).scalar_one()
            assert snap.source != "TRADES", (
                "the whole point: a valuation the reconciliation can treat as "
                "independent evidence")
    run(go())


def test_the_valuation_refuses_rather_than_guesses_when_the_ledger_is_incomplete():
    """A confident wrong number would overwrite the one honest signal there is."""
    async def go():
        async with Fund() as site:
            await _two_investors(site, deposits=[D("1000")])
            await _broker_account(site, funded=D("1000"))
            async with site.Session() as s:
                await histmod.store_deals(s, LOGIN, [
                    _deal(ticket=77, type=5, profit=0.0, comment="correction")])
                await s.commit()
            async with site.Session() as s:
                out = await jobsmod.broker_valuation(s, date(2026, 10, 8))

            assert "blocked" in out, out
            assert any("review" in b for b in out["blocked"])
            async with site.Session() as s:
                rows = (await s.execute(select(NavSnapshot))).scalars().all()
            assert rows == [], "no snapshot may be written over an incomplete ledger"
    run(go())


def test_the_valuation_skips_quietly_when_no_account_is_marked():
    async def go():
        async with Fund() as site:
            await _two_investors(site, deposits=[D("1000")])
            async with site.Session() as s:
                out = await jobsmod.broker_valuation(s, date(2026, 10, 8))
            assert "skipped" in out
    run(go())


def test_the_valuation_runs_once_a_day_however_many_workers_are_up():
    async def go():
        async with Fund() as site:
            await _two_investors(site, deposits=[D("1000")])
            await _broker_account(site, funded=D("1000"))
            async with site.Session() as s:
                first = await jobsmod.broker_valuation(s, date(2026, 10, 8))
            async with site.Session() as s:
                second = await jobsmod.broker_valuation(s, date(2026, 10, 8))
            assert first is not None
            assert second is None, "the day is claimed by whoever gets there first"
    run(go())


def test_after_a_valuation_the_reconciliation_has_independent_evidence():
    """End to end: this is the sequence that would have shown the gap."""
    async def go():
        async with Fund() as site:
            # investors told 5,536; only 3,266.92 ever reached the broker
            await _two_investors(site, deposits=[D("2686"), D("1550"), D("700"), D("600")])
            await _broker_account(site, funded=D("3266.92"))

            async with site.Session() as s:
                await jobsmod.broker_valuation(s, date(2026, 10, 8))
            async with site.Session() as s:
                equity, source = await srcmod.live_equity(s)
                report = await recmod.build(s, pool_equity=equity)
                gap = await recmod.shortfall(s, pool_equity=equity)

            assert source == "ledger", "not a figure the ledger produced itself"
            # Cash at the broker PLUS cash confirmed in and not yet sent there.
            # Valuing at 3,266.92 alone would mark every unit down 41% and tell
            # these investors they had lost money sitting in a bank account.
            assert equity == D("5536.00")
            assert gap["shortfall"] == "0.00"
            assert gap["is_covered"] is True
            # But the difference is still REPORTED, on its own line, because an
            # admin confirming a deposit that never arrived would look exactly
            # like this.
            line = next(ln for ln in report.lines
                        if ln.label == "Broker capital in vs confirmed deposits")
            assert line.difference == D("-2269.08"), "the funding gap must show"
            assert line.balanced is False and line.ok is True, (
                "visible, but not something to act on")
            assert gap["confirmed_but_not_funded"] == "2269.08"
    run(go())


# ── which lines are allowed to differ ───────────────────────────────────────

def test_only_the_lines_that_must_balance_decide_whether_to_raise_the_alarm():
    """A page that goes red on a normal state is a page nobody reads.

    Three of five lines are legitimately non-zero at any moment — money in
    transit, unpublished profit — so `lines_agree` must ignore those and watch
    the two where a difference is genuinely a defect.
    """
    async def go():
        async with Fund() as site:
            await _two_investors(site, deposits=[D("2686"), D("1550"), D("700"), D("600")])
            await _broker_account(site, funded=D("3266.92"))
            async with site.Session() as s:
                pool, _ = await srcmod.pool_value(s)
                report = await recmod.build(s, pool_equity=pool)

            must = {ln.label for ln in report.lines if ln.must_balance}
            assert must == {"Equity vs allocated, after unpublished results",
                            "Confirmed deposits vs units issued",
                            "Broker capital out vs withdrawals paid"}, must
            # The headline line stays STRICT. It looked non-zero only because
            # it ignored a known cause — profit the accounts made that has not
            # been published into the NAV — so the cause is now accounted for
            # rather than the check being downgraded. A difference that
            # survives that is genuinely unexplained money.
            headline = next(ln for ln in report.lines if ln.must_balance
                            and ln.label.startswith("Equity vs allocated"))
            assert headline.balanced is True, (headline.pool, headline.investor_facing)
            # every informational line carries a plain-English meaning, or the
            # reader is left to guess what a number signifies
            for ln in report.lines:
                if not ln.must_balance:
                    assert ln.means, f"{ln.label} must say what a difference means"
            # 2,269 in transit and nothing wrong
            assert report.lines_agree is True
    run(go())


def test_money_leaving_without_a_withdrawal_row_is_still_an_alarm():
    """The line the docstring calls the most serious one must stay strict."""
    async def go():
        async with Fund() as site:
            await _two_investors(site, deposits=[D("1000")])
            await _broker_account(site, funded=D("1000"))
            async with site.Session() as s:
                # $300 leaves the account with no withdrawal recorded
                await histmod.store_deals(s, LOGIN, [
                    _deal(ticket=50, type=2, profit=-300.0, comment="Withdrawal")])
                await s.commit()
            async with site.Session() as s:
                pool, _ = await srcmod.pool_value(s)
                report = await recmod.build(s, pool_equity=pool)

            line = next(ln for ln in report.lines
                        if ln.label == "Broker capital out vs withdrawals paid")
            assert line.must_balance is True
            assert line.ok is False
            assert report.lines_agree is False, (
                "money leaving with no record must raise the alarm")
    run(go())
