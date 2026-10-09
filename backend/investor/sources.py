"""
backend/investor/sources.py

The broker's side of the reconciliation, read from tables rather than MT5.

THE BOUNDARY THIS PRESERVES
---------------------------
`backend/investor/__init__.py` states the rule: this package READS the pool's
value and never writes to it, and an investor request must never reach MT5. A
bug in the investor ledger must not be able to touch a live position.

So the investor side does not import `backend.mt5.history`. It reads
`mt5_cash_flows`, `mt5_accounts` and `mt5_opening_anchors` — ordinary tables,
written by the MT5 side on its own schedule. The boundary stays, and the gap
that made `/reconciliation` meaningless closes.

WHICH ACCOUNTS COUNT
--------------------
Only those flagged `holds_investor_capital`. A prop-firm allocation is the
firm's money under a profit split, so counting FundedNext's $25,000 as capital
available to investors would overstate the fund by exactly that much. Only our
profit share from such an account is ours, and that arrives as trade results in
an account we do own.

`CREDIT_NOT_CAPITAL` (broker credit, bonuses) is excluded everywhere for the
same reason: it is not withdrawable, so it cannot back a unit.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import func, select

from backend.investor import nav as navmod
from backend.mt5.cash_models import (
    CAPITAL_IN,
    CAPITAL_OUT,
    TRADE_RESULT,
    UNKNOWN,
    MT5Account,
    MT5CashFlow,
    OpeningAnchor,
)

D = Decimal


@dataclass(frozen=True)
class BrokerSide:
    """What the broker accounts that hold investor money actually did."""

    logins: tuple[int, ...]
    opening_balance: Decimal
    capital_in: Decimal
    capital_out: Decimal          # negative
    trade_result: Decimal
    costs: Decimal                # negative
    adjustments: Decimal
    unclassified_count: int
    anchored_at: datetime | None
    # Whether any account is marked as holding investor capital at all.
    configured: bool
    # True when every contributing account has an anchor AND nothing is
    # waiting to be classified. Vacuously true when no account is configured,
    # because nothing was compared — see `configured`.
    complete: bool
    missing: tuple[str, ...]

    @property
    def net_capital(self) -> Decimal:
        return navmod.money(self.capital_in + self.capital_out)

    @property
    def derived_equity(self) -> Decimal:
        """What the accounts should hold, from the ledger alone."""
        return navmod.money(self.opening_balance + self.capital_in + self.capital_out
                            + self.trade_result + self.costs + self.adjustments)


async def capital_accounts(session) -> list[MT5Account]:
    """The accounts whose money backs investor units."""
    return list((await session.execute(
        select(MT5Account).where(MT5Account.holds_investor_capital.is_(True))
    )).scalars().all())


async def _sum_class(session, logins: list[int], flow_class: str,
                     on_or_before: date | None = None) -> Decimal:
    if not logins:
        return D("0.00")
    q = select(func.coalesce(func.sum(MT5CashFlow.amount), 0.0)).where(
        MT5CashFlow.account_login.in_(logins), MT5CashFlow.flow_class == flow_class)
    if on_or_before is not None:
        q = q.where(MT5CashFlow.deal_time_utc
                    <= datetime.combine(on_or_before, datetime.max.time()))
    return navmod.money(D(str((await session.execute(q)).scalar_one())))


async def broker_side(session, on: date | None = None) -> BrokerSide:
    """Everything the investor reconciliation needs from the broker's books."""
    from backend.mt5.cash_models import ADJUSTMENT, COST

    accounts = await capital_accounts(session)
    logins = [a.login for a in accounts]
    missing: list[str] = []

    opening = D("0.00")
    anchored_at: datetime | None = None
    for a in accounts:
        anchor = (await session.execute(select(OpeningAnchor).where(
            OpeningAnchor.account_login == a.login))).scalar_one_or_none()
        if anchor is None:
            missing.append(f"account {a.login} has no opening anchor")
            continue
        opening += navmod.money(D(str(anchor.balance)))
        if anchored_at is None or anchor.as_of > anchored_at:
            anchored_at = anchor.as_of

    unclassified = 0
    if logins:
        unclassified = int((await session.execute(
            select(func.count()).select_from(MT5CashFlow).where(
                MT5CashFlow.account_login.in_(logins),
                MT5CashFlow.flow_class == UNKNOWN))).scalar_one())
        if unclassified:
            missing.append(f"{unclassified} deal(s) await review")

    return BrokerSide(
        logins=tuple(logins),
        opening_balance=navmod.money(opening),
        capital_in=await _sum_class(session, logins, CAPITAL_IN, on),
        capital_out=await _sum_class(session, logins, CAPITAL_OUT, on),
        trade_result=await _sum_class(session, logins, TRADE_RESULT, on),
        costs=await _sum_class(session, logins, COST, on),
        adjustments=await _sum_class(session, logins, ADJUSTMENT, on),
        unclassified_count=unclassified,
        anchored_at=anchored_at,
        # `complete` describes the comparison that was ATTEMPTED. With no
        # account flagged there are no broker lines at all, which is a
        # configuration gap for the caller to report — not a failed check.
        complete=not missing,
        configured=bool(accounts),
        missing=tuple(missing),
    )


async def unfunded_capital(session) -> Decimal:
    """Confirmed deposits that have not reached a broker account.

    Fund cash held somewhere other than the broker. It is an ASSET: the
    investor paid it, we confirmed it, and it is in a bank account — so the
    units it bought are not worth less for it being in transit.

    This is the figure `reconcile.py`'s docstring has always called the one
    legitimate non-zero difference. It is now computed instead of assumed.
    """
    from backend.investor.models import DEPOSIT_CONFIRMED, Deposit

    side = await broker_side(session)
    confirmed = navmod.money(D(str((await session.execute(
        select(func.coalesce(func.sum(Deposit.amount_confirmed), 0))
        .where(Deposit.state == DEPOSIT_CONFIRMED))).scalar_one())))
    # Never negative: more at the broker than was ever confirmed in means
    # capital arrived from somewhere else, which the reconciliation lines
    # report. It is not a deduction from the fund's value.
    return navmod.money(max(confirmed - side.capital_in, D("0.00")))


async def pool_value(session) -> tuple[Decimal | None, str]:
    """What the fund is worth: cash at the broker PLUS cash on its way there.

    Valuing at broker equity alone was the first version of this, and a test
    caught what it does: with $2,269 of confirmed deposits not yet transferred,
    the NAV marked down 41% and told investors they had lost money that was
    sitting in a bank account.
    """
    side = await broker_side(session)
    if not side.logins:
        return None, "none"
    return navmod.money(side.derived_equity + await unfunded_capital(session)), "ledger"


async def live_equity(session) -> tuple[Decimal | None, str]:
    """Kept as the name the reconciliation route already calls."""
    return await pool_value(session)


async def prop_allocation(session) -> Decimal:
    """Money in our accounts that belongs to a prop firm, not to investors."""
    rows = (await session.execute(
        select(func.coalesce(func.sum(MT5Account.prop_allocation), 0.0)))).scalar_one()
    return navmod.money(D(str(rows or 0)))
