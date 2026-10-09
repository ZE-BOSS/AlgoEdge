"""
backend/investor/reconcile.py

Does what the investors are told add up to what the fund actually has?

A healthy platform runs at one non-zero difference — deposits confirmed but not
yet funded to the broker — and nothing else. Anything more is a flag, and the
point of this module is that the flag is raised by arithmetic rather than by
somebody noticing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select

from backend.investor import fund as fundmod
from backend.investor import nav as navmod
from backend.investor import units as unitsmod
from backend.investor.models import (
    DEPOSIT_CONFIRMED,
    KIND_FEE,
    KIND_SUBSCRIBE,
    WITHDRAWAL_APPROVED,
    Deposit,
    Investor,
    InvestorUnits,
    TradeDisclosure,
    UnitTransaction,
    Withdrawal,
)

D = Decimal


@dataclass
class Line:
    """One row of the reconciliation: what we have, what we owe, the gap.

    NOT EVERY LINE MUST BALANCE, and treating them alike makes the page cry
    wolf. This module's own opening paragraph says so: a healthy platform runs
    at one non-zero difference — deposits confirmed but not yet funded to the
    broker — and nothing else.

    `must_balance` separates the two kinds:

      True   a difference is a DEFECT. A confirmed deposit that issued no
             units, or money that left an account with no withdrawal row.
      False  a difference has a known, legitimate meaning, printed in
             `means`. Money in transit and profit not yet published are
             both normal; they are worth SEEING, not worth alarming about.

    Measured on the VPS shape: three of five lines are legitimately non-zero
    at any moment, so a page that goes red on any of them is a page that is
    always red, which is a page nobody reads.
    """
    label: str
    pool: Decimal
    investor_facing: Decimal
    note: str = ""
    must_balance: bool = True
    # What a non-zero difference on an informational line signifies.
    means: str = ""

    @property
    def difference(self) -> Decimal:
        return navmod.money(self.pool - self.investor_facing)

    @property
    def balanced(self) -> bool:
        """Literally zero, whatever kind of line this is."""
        return self.difference == D("0.00")

    @property
    def ok(self) -> bool:
        """Nothing here needs attention."""
        return self.balanced or not self.must_balance


@dataclass
class Report:
    as_of: date
    nav_per_unit: Decimal
    units_in_issue: Decimal
    lines: list[Line] = field(default_factory=list)
    ledger_drift: list = field(default_factory=list)
    # Whether the broker side of the comparison could be computed at all: no
    # account marked as holding investor capital, a missing opening anchor, or
    # a deal still awaiting classification all mean the answer is incomplete
    # rather than clean. Reported separately from the lines, because "every
    # line agrees" is worthless if the lines were built from nothing.
    broker_complete: bool = True
    # Whether any MT5 account is marked as holding investor capital. False
    # means this report compared the investor ledger with itself and nothing
    # else, which the caller must say out loud rather than present as clean.
    broker_configured: bool = False
    broker_missing: list = field(default_factory=list)

    @property
    def lines_agree(self) -> bool:
        """Every comparison on the page balances, and no investor's cached
        units differ from their ledger.

        This is the INTERNAL check: it says the books are consistent with
        themselves. It does not say anything has been confirmed against a
        broker — see `healthy` for that.
        """
        return not self.ledger_drift and all(line.ok for line in self.lines)

    @property
    def healthy(self) -> bool:
        """Clean only when every line agrees AND the ledger matches its cache.

        The drift check is part of this on purpose: a reconciliation that
        balances on totals while an individual investor's cache is wrong is
        exactly the failure that stays hidden until somebody withdraws.
        """
        return self.lines_agree and self.broker_complete and self.broker_configured

    def as_dict(self) -> dict:
        return {
            "as_of": self.as_of.isoformat(),
            "nav_per_unit": navmod.text(self.nav_per_unit),
            "units_in_issue": navmod.text(self.units_in_issue),
            "healthy": self.healthy,
            "lines_agree": self.lines_agree,
            "broker_complete": self.broker_complete,
            "broker_configured": self.broker_configured,
            "broker_missing": list(self.broker_missing),
            "ledger_drift": [
                {"investor_id": i, "ledger": navmod.text(w), "cache": navmod.text(g)}
                for i, w, g in self.ledger_drift
            ],
            "lines": [
                {"label": ln.label, "pool": navmod.text(ln.pool),
                 "investor_facing": navmod.text(ln.investor_facing),
                 "difference": navmod.text(ln.difference), "ok": ln.ok,
                 "balanced": ln.balanced, "must_balance": ln.must_balance,
                 "means": ln.means, "note": ln.note}
                for ln in self.lines
            ],
        }


async def _sum(session, column, *where) -> Decimal:
    total = (await session.execute(
        select(func.coalesce(func.sum(column), 0)).where(*where)
    )).scalar_one()
    return navmod.money(total)


async def build(session, *, pool_equity, on: date | None = None) -> Report:
    """Compare the broker's reality against the investors' claims.

    `pool_equity` is passed in rather than read here: this module must not reach
    into MT5. The investor side reads the pool's value, it never touches the
    trading engine.
    """
    day = on or navmod.accounting_date()
    price = await fundmod.latest_nav(session, day)
    outstanding = await unitsmod.units_in_issue(session)
    equity = navmod.money(pool_equity)

    # what every investor's holding is worth at today's price
    allocated = navmod.money(sum(
        (navmod.amount_for_units(navmod.units(r.units), price)
         for r in (await session.execute(select(InvestorUnits))).scalars()),
        D("0")))

    # approved but not yet paid — real money already promised out
    # approved-but-unpaid withdrawals and charged-but-unpaid fees
    owed = await fundmod.owed_on(session, day)

    confirmed_in = await _sum(session, Deposit.amount_confirmed,
                              Deposit.state == DEPOSIT_CONFIRMED)

    report = Report(as_of=day, nav_per_unit=price, units_in_issue=outstanding)

    # Computed before the headline line, because it is the one known reason
    # the pool may legitimately hold more than investors are credited with.
    from backend.investor import sources as _srcmod

    _broker = await _srcmod.broker_side(session, day)
    booked = await _sum(session, TradeDisclosure.booked_amount,
                        TradeDisclosure.booked_amount.isnot(None))
    unbooked = (navmod.money(_broker.trade_result + _broker.costs - booked)
                if _broker.logins else D("0.00"))

    report.lines.append(Line(
        label="Equity vs allocated, after unpublished results",
        pool=navmod.money(equity - owed),
        investor_facing=navmod.money(allocated + unbooked),
        note=("Broker equity less what is owed out (approved withdrawals and fees "
              "not yet paid), against every holding at today's NAV PLUS results "
              "the accounts have made that are not published into the NAV yet. "
              "Accounting for that known cause rather than ignoring it is what "
              "keeps this line strict: a difference that survives it is money "
              "that belongs to nobody, or more owed than the fund holds."),
    ))

    # Units only exist because money was confirmed in, so the ledger's own
    # subscription total must equal the confirmed deposits. This catches a
    # deposit confirmed without units issued, which the equity line cannot.
    subscribed = await _sum(session, UnitTransaction.amount,
                            UnitTransaction.kind == KIND_SUBSCRIBE)
    report.lines.append(Line(
        label="Confirmed deposits vs units issued",
        pool=confirmed_in,
        investor_facing=subscribed,
        note=("Every confirmed deposit must have issued units. A gap is a deposit "
              "recorded but never subscribed, which no equity check would show."),
    ))

    # ── against the broker's own books ───────────────────────────────────
    #
    # The three lines above compare the investor ledger with itself and with a
    # figure handed in. These compare it with what the BROKER recorded, which
    # is the check that was missing: every `nav_snapshots` row on the VPS has
    # `source='TRADES'`, so the NAV had only ever been moved by bookings and
    # never measured against an account. A difference of $2,207.51 was
    # therefore invisible.
    #
    # Read from tables, never from MT5 — see backend/investor/sources.py.
    from backend.investor import sources as srcmod

    broker = await srcmod.broker_side(session, day)
    report.broker_complete = broker.complete
    report.broker_configured = broker.configured
    report.broker_missing = list(broker.missing)

    if broker.logins:
        report.lines.append(Line(
            label="Broker capital in vs confirmed deposits",
            must_balance=False,
            means="money confirmed in that has not been transferred to the broker yet",
            pool=broker.capital_in,
            investor_facing=confirmed_in,
            note=("What actually reached the trading accounts, against what we told "
                  "investors we had received. A gap is normal when a deposit is "
                  "confirmed but not yet funded to the broker — and it is the "
                  "number that has to be visible rather than assumed."),
        ))
        paid_out = await _sum(session, Withdrawal.amount_paid)
        report.lines.append(Line(
            label="Broker capital out vs withdrawals paid",
            pool=navmod.money(-broker.capital_out),     # stored negative
            investor_facing=paid_out,
            note=("Money that left the accounts, against withdrawals we recorded. "
                  "A gap here means money left without a withdrawal row, which is "
                  "the most serious difference on this page."),
        ))
        report.lines.append(Line(
            label="Broker results vs booked trades",
            must_balance=False,
            means="trading results that have not been published into the NAV yet",
            pool=navmod.money(broker.trade_result + broker.costs),
            investor_facing=booked,
            note=("What the accounts made after costs, against the results published "
                  "into the NAV. A gap is trading that never reached a balance."),
        ))

    report.ledger_drift = await unitsmod.assert_cache_matches_ledger(session)
    return report


async def shortfall(session, *, pool_equity, on: date | None = None) -> dict:
    """What must be covered: the gap between what investors are owed and what
    the pool holds.

    This is the figure asked for as "cover up". Positive means the fund owes
    more than it has and the difference has to be injected; zero or negative
    means it is covered.

        shortfall = investor claims - (pool equity - amounts owed out)

    Reported with its history, because the question is never only "how much"
    but "is it closing". `covered_by_profit` answers that: how much of it this
    month's trading has already absorbed.
    """
    day = on or navmod.accounting_date()
    price = await fundmod.latest_nav(session, day)
    claims = navmod.money(sum(
        (navmod.amount_for_units(navmod.units(r.units), price)
         for r in (await session.execute(select(InvestorUnits))).scalars()),
        D("0")))
    owed = await fundmod.owed_on(session, day)
    available = navmod.money(navmod.money(pool_equity) - owed)
    gap = navmod.money(claims - available)

    confirmed_in = await _sum(session, Deposit.amount_confirmed,
                              Deposit.state == DEPOSIT_CONFIRMED)
    from backend.investor import sources as srcmod

    broker = await srcmod.broker_side(session, day)
    unfunded = navmod.money(confirmed_in - broker.capital_in) if broker.logins else None

    return {
        "as_of": day.isoformat(),
        "investor_claims": navmod.text(claims),
        "pool_equity": navmod.text(navmod.money(pool_equity)),
        "owed_out": navmod.text(owed),
        "available_to_investors": navmod.text(available),
        "shortfall": navmod.text(gap),
        "is_covered": gap <= D("0.00"),
        # The most likely innocent explanation, separated out so it is not
        # confused with a loss: money an investor has paid that has not been
        # moved to the broker yet is a shortfall on paper and nothing more.
        "confirmed_but_not_funded": None if unfunded is None else navmod.text(unfunded),
        "unexplained": None if unfunded is None else navmod.text(navmod.money(gap - unfunded)),
        "broker_complete": broker.complete,
        "broker_missing": list(broker.missing),
    }


async def gross_net(session, investor_id: str, price: Decimal | None = None) -> dict:
    """What an investor's holding would be worth with no fees ever charged
    (gross), what it is worth (net, the figure they see), and the difference.

    Every fee cancelled units; had those units stayed, they would be worth
    fee_units x today's price. So gross = (units held + units cancelled for
    fees) x price, and gross - net is what the fees have cost the investor at
    today's value. `fees_charged` is the dollar amount on the day each fee was
    taken, which differs from the cost today by how the fund has moved since.
    Both come straight off the ledger, so both reconcile.
    """
    price = navmod.nav(price if price is not None else await fundmod.latest_nav(session))
    held = await unitsmod.ledger_units(session, investor_id)
    fee_units, fee_amount = (await session.execute(
        select(func.coalesce(func.sum(UnitTransaction.units), 0),
               func.coalesce(func.sum(UnitTransaction.amount), 0))
        .where(UnitTransaction.investor_id == investor_id, UnitTransaction.kind == KIND_FEE))).one()
    fee_units = navmod.units(-D(str(fee_units)))            # stored negative
    net = navmod.amount_for_units(held, price)
    gross = navmod.amount_for_units(held + fee_units, price)
    diff = navmod.money(gross - net)
    pct = navmod.money(diff / gross * D("100")) if gross > 0 else D("0.00")
    return {"gross_value": navmod.text(gross), "net_value": navmod.text(net),
            "difference": navmod.text(diff), "difference_pct": navmod.text(pct),
            "fees_charged": navmod.text(navmod.money(D(str(fee_amount))))}


async def investor_statement(session, investor_id: str, on: date | None = None) -> dict:
    """One investor's position — the numbers their dashboard shows.

    Every figure is derived from the ledger and today's NAV, so an investor
    asking "why is my number X" can be answered from rows rather than opinion.
    """
    day = on or navmod.accounting_date()
    price = await fundmod.latest_nav(session, day)
    held = await unitsmod.ledger_units(session, investor_id)
    value = navmod.amount_for_units(held, price)

    paid_in = await _sum(session, Deposit.amount_confirmed,
                         Deposit.investor_id == investor_id,
                         Deposit.state == DEPOSIT_CONFIRMED)
    taken_out = await _sum(session, Withdrawal.amount_paid,
                           Withdrawal.investor_id == investor_id)

    outstanding = await unitsmod.units_in_issue(session)
    share = (held / outstanding * D("100")) if outstanding > 0 else D("0")
    cap = await fundmod.withdrawal_cap(session, investor_id, day)
    investor = await session.get(Investor, investor_id)

    return {
        "investor_id": investor_id,
        "name": getattr(investor, "name", None),
        "as_of": day.isoformat(),
        "units": navmod.text(held),
        "nav_per_unit": navmod.text(price),
        "current_value": navmod.text(value),
        "capital_in": navmod.text(paid_in),
        "withdrawn": navmod.text(taken_out),
        # profit is value + what they have taken out, less what they put in —
        # the only definition that stays right across partial withdrawals
        "profit": navmod.text(navmod.money(value + taken_out - paid_in)),
        "share_of_pool_pct": navmod.text(navmod.money(share)),
        "withdrawable_now": navmod.text(cap.cap),
        "withdrawable_explanation": cap.explanation,
    }
