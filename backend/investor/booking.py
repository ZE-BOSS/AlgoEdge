"""
backend/investor/booking.py

A published trade's result, booked into the fund's price so balances move.

Publishing a trade used to change only the Trades tab: an investor saw their
share of a loss there while their balance, and so the capital-versus-profit
message, waited for the next hand-entered valuation. Now the result is booked
into the NAV on the day it is published:

    new NAV = NAV + result / units in issue

which moves every holder's balance by result x their units / all units, the
same share the Trades tab shows (see split.py) when no one joined or left
between the close and the publish.

Each disclosure remembers what has been booked for it (`booked_amount`), so
this is idempotent: re-publishing with an edited result books only the
difference. Hiding a published trade changes what investors are shown, not
what the account made or lost, so it leaves the booking alone. A trade that
closed before the latest real valuation (MANUAL / MT5) is not booked at all:
that valuation was taken from broker equity that already contained it.

The booking is claimed with a compare-and-set on `booked_amount`, so two
workers (the publish request and the background job) cannot book it twice.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import desc, select, update

from backend.data.models import Trade
from backend.investor import fund as fundmod
from backend.investor import nav as navmod
from backend.investor import units as unitsmod
from backend.investor.models import DISCLOSURE_PUBLISHED, NavSnapshot, TradeDisclosure
from backend.utils.logger import get_logger

logger = get_logger(__name__)
D = Decimal
SOURCE = "TRADES"      # a NAV moved by booked trades rather than read from the broker


def target(row: TradeDisclosure) -> Decimal | None:
    """What should be booked for this disclosure; None to leave it as it is."""
    if row.state != DISCLOSURE_PUBLISHED or row.result_amount is None:
        return None
    return navmod.money(row.result_amount)


async def last_valuation(session) -> NavSnapshot | None:
    """The newest price read from broker equity, not moved by bookings."""
    return (await session.execute(
        select(NavSnapshot).where(NavSnapshot.source != SOURCE)
        .order_by(desc(NavSnapshot.created_at), desc(NavSnapshot.as_of_date)).limit(1)
    )).scalar_one_or_none()


async def _move_nav(session, delta: Decimal) -> NavSnapshot | None:
    """Move today's NAV by `delta` dollars of pool equity."""
    day = navmod.accounting_date()
    outstanding = await unitsmod.units_in_issue(session)
    if outstanding <= 0:
        return None                    # no one holds the fund: nobody's balance to move
    owed = await fundmod.owed_on(session, day)
    row = (await session.execute(
        select(NavSnapshot).where(NavSnapshot.as_of_date == day))).scalar_one_or_none()
    if row is not None:
        # keep whatever else the day's valuation said was owed
        extra = max(navmod.money(row.liabilities) - owed, D("0.00"))
        pool, source = navmod.money(row.pool_equity) + delta, row.source
    else:
        price = await fundmod.latest_nav(session, day)
        extra = D("0.00")
        pool = navmod.money(navmod.units(outstanding) * price) + owed + delta
        source = SOURCE
    return await fundmod.take_snapshot(session, pool_equity=pool, liabilities=extra,
                                       on=day, source=source)


async def book(session, row: TradeDisclosure, *, actor_id: str | None = None) -> Decimal:
    """Bring what is booked for `row` up to date. Returns the dollars moved."""
    want = target(row)
    had = None if row.booked_amount is None else navmod.money(row.booked_amount)
    if want is None or had == want:
        return D("0.00")
    delta = want - (had or D("0.00"))

    covered = False
    if had is None and want != 0:
        exit_time = (await session.execute(
            select(Trade.exit_time).where(Trade.id == row.trade_id))).scalar_one_or_none()
        val = await last_valuation(session)
        covered = bool(val and val.created_at and exit_time and val.created_at >= exit_time)

    # claim it: only one caller gets to move the price for this change
    cond = TradeDisclosure.booked_amount.is_(None) if had is None else TradeDisclosure.booked_amount == had
    claimed = await session.execute(
        update(TradeDisclosure).where(TradeDisclosure.id == row.id, cond)
        .values(booked_amount=want, booked_on=None if covered else navmod.accounting_date())
        .execution_options(synchronize_session=False))
    if claimed.rowcount != 1:
        await session.refresh(row)
        return D("0.00")
    row.booked_amount = want
    row.booked_on = None if covered else navmod.accounting_date()
    if covered or delta == 0:
        await session.flush()
        return D("0.00")

    snap = await _move_nav(session, delta)
    await fundmod.audit(session, actor_id=actor_id, action="nav.trade_booked",
                        entity_type="trade", entity_id=str(row.trade_id),
                        detail={"delta": navmod.text(delta), "booked": navmod.text(want),
                                "nav": None if snap is None else navmod.text(snap.nav_per_unit)})
    logger.info(f"[FUND] trade {row.trade_id} booked {delta:+} into the NAV")
    return delta


async def book_all(session, *, actor_id: str | None = None) -> int:
    """Book every disclosure whose booked amount is out of date, oldest first."""
    rows = (await session.execute(
        select(TradeDisclosure).where(TradeDisclosure.state == DISCLOSURE_PUBLISHED)
        .order_by(TradeDisclosure.closed_on, TradeDisclosure.trade_id)
    )).scalars().all()
    n = 0
    for r in rows:
        had = None if r.booked_amount is None else navmod.money(r.booked_amount)
        if target(r) is not None and had != target(r):
            await book(session, r, actor_id=actor_id)
            n += 1
    return n


def status(row: TradeDisclosure) -> dict:
    """For the admin: has this trade reached balances, and how."""
    if row.booked_amount is None:
        return {"booked": None, "booked_on": None, "in_valuation": False}
    return {"booked": navmod.text(navmod.money(row.booked_amount)),
            "booked_on": row.booked_on.isoformat() if row.booked_on else None,
            "in_valuation": row.booked_on is None and navmod.money(row.booked_amount) != 0}

