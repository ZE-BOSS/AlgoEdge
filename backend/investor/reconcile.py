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
            "nav_per_unit": str(self.nav_per_unit),
            "units_in_issue": str(self.units_in_issue),
            "healthy": self.healthy,
            "ledger_drift": [
                {"investor_id": i, "ledger": str(w), "cache": str(g)}
                for i, w, g in self.ledger_drift
            ],
            "lines": [
                {"label": ln.label, "pool": str(ln.pool),
                 "investor_facing": str(ln.investor_facing),
                 "difference": str(ln.difference), "ok": ln.ok, "note": ln.note}
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
    owed = await _sum(session, Withdrawal.amount_requested,
                      Withdrawal.state == WITHDRAWAL_APPROVED)

    confirmed_in = await _sum(session, Deposit.amount_confirmed,
                              Deposit.state == DEPOSIT_CONFIRMED)

    report = Report(as_of=day, nav_per_unit=price, units_in_issue=outstanding)

    report.lines.append(Line(
        label="Equity vs allocated",
        pool=navmod.money(equity - owed),
        investor_facing=allocated,
        note=("Broker equity less withdrawals already approved, against the sum of "
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
        "units": str(held),
        "nav_per_unit": str(price),
        "current_value": str(value),
        "capital_in": str(paid_in),
        "withdrawn": str(taken_out),
        # profit is value + what they have taken out, less what they put in —
        # the only definition that stays right across partial withdrawals
        "profit": str(navmod.money(value + taken_out - paid_in)),
        "share_of_pool_pct": str(navmod.money(share)),
        "withdrawable_now": str(cap.cap),
        "withdrawable_explanation": cap.explanation,
    }
