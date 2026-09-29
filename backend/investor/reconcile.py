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
    UnitTransaction,
    Withdrawal,
)

D = Decimal


@dataclass
class Line:
    """One row of the reconciliation: what we have, what we owe, the gap."""
    label: str
    pool: Decimal
    investor_facing: Decimal
    note: str = ""

    @property
    def difference(self) -> Decimal:
        return navmod.money(self.pool - self.investor_facing)

    @property
    def ok(self) -> bool:
        return self.difference == D("0.00")


@dataclass
class Report:
    as_of: date
    nav_per_unit: Decimal
    units_in_issue: Decimal
    lines: list[Line] = field(default_factory=list)
    ledger_drift: list = field(default_factory=list)

    @property
    def healthy(self) -> bool:
        """Clean only when every line agrees AND the ledger matches its cache.

        The drift check is part of this on purpose: a reconciliation that
        balances on totals while an individual investor's cache is wrong is
        exactly the failure that stays hidden until somebody withdraws.
        """
        return not self.ledger_drift and all(line.ok for line in self.lines)

    def as_dict(self) -> dict:
        return {
            "as_of": self.as_of.isoformat(),
            "nav_per_unit": navmod.text(self.nav_per_unit),
            "units_in_issue": navmod.text(self.units_in_issue),
            "healthy": self.healthy,
            "ledger_drift": [
                {"investor_id": i, "ledger": navmod.text(w), "cache": navmod.text(g)}
                for i, w, g in self.ledger_drift
            ],
            "lines": [
                {"label": ln.label, "pool": navmod.text(ln.pool),
                 "investor_facing": navmod.text(ln.investor_facing),
                 "difference": navmod.text(ln.difference), "ok": ln.ok, "note": ln.note}
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

    report.lines.append(Line(
        label="Equity vs allocated",
        pool=navmod.money(equity - owed),
        investor_facing=allocated,
        note=("Broker equity less what is owed out (approved withdrawals and fees not yet "
              "paid), against the sum of "
              "every holding at today's NAV. A gap here means money in the account "
              "belongs to nobody, or the fund owes more than it holds."),
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

    report.ledger_drift = await unitsmod.assert_cache_matches_ledger(session)
    return report


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
