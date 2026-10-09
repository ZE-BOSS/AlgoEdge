"""
backend/investor/jobs.py

The investor platform's scheduled work, run by one asyncio loop in the API
process:

  * ledger_check — nightly: every investor's cached units must equal a replay
    of the ledger. A mismatch emails the admin. The ledger is the truth; this
    is how a bug is found that night rather than at withdrawal time.
  * broker_valuation — the day's NAV taken from the BROKER's books rather
    than from booked trades. Without it the price is an accumulation that has
    never been checked against an account, which is how a $2,207 difference
    stayed invisible. Off with INVESTOR_BROKER_VALUATION=off.
  * daily_digest — after the WAT day rolls: yesterday's money movements and the
    open queues, to ADMIN_ALERT_EMAIL. Off with INVESTOR_DAILY_DIGEST=off.

Each job runs once per WAT day however many worker processes are up: it claims
the day by inserting a JobRun row, and a second worker's insert hits the unique
constraint and backs off. Off entirely with INVESTOR_JOBS=off.
"""

from __future__ import annotations

import asyncio
import os
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from backend.investor import fund as fundmod
from backend.investor import nav as navmod
from backend.investor import units as unitsmod
from backend.investor.models import (
    DEPOSIT_CONFIRMED,
    Deposit,
    Investor,
    JobRun,
    Withdrawal,
)
from backend.utils.logger import get_logger

logger = get_logger(__name__)
TICK_SECONDS = 15 * 60
RUN_AFTER_WAT_HOUR = 1          # give the day's last snapshot time to land


async def _claim(session, job: str, key: str) -> JobRun | None:
    row = JobRun(job=job, run_key=key)
    session.add(row)
    try:
        await session.flush()
        return row
    except IntegrityError:
        await session.rollback()
        return None


async def ledger_check(session, day: date) -> dict | None:
    from backend.notify import outbox
    from backend.notify import templates as mail
    run = await _claim(session, "ledger_check", day.isoformat())
    if run is None:
        return None
    drift = await unitsmod.assert_cache_matches_ledger(session)
    run.result = {"drift": [[i, navmod.text(w), navmod.text(g)] for i, w, g in drift]}
    if drift:
        logger.error(f"[JOBS] ledger check FAILED for {len(drift)} investor(s): {drift}")
        outbox.queue(session, mail.admin_ledger_drift(drift))
    else:
        logger.info("[JOBS] ledger check clean")
    await session.commit()
    return run.result


async def broker_valuation(session, day: date) -> dict | None:
    """Take the day's NAV from what the BROKER holds, not from bookings.

    THIS IS THE JOB WHOSE ABSENCE HID A $2,207 GAP.

    Every `nav_snapshots` row on the VPS carries `source='TRADES'`, meaning the
    price investors see had only ever been moved by `booking.py` adding
    published results to a seeded figure — never once measured against an
    account. `/reconciliation` then fell back to that same snapshot, so it was
    comparing the ledger with itself and could not disagree.

    This closes the loop: once a day the pool's value is read from the cash
    ledger, which is the brokers' own deal history summed, and written as a
    snapshot whose source is NOT "TRADES". From then on the reconciliation has
    independent evidence every single day.

    Refuses rather than guesses:
      * no account marked as holding investor capital -> nothing to value;
      * an account missing its anchor, or a deal still unclassified -> the
        figure would be wrong in an unknown direction, so none is written and
        the admin is told what is blocking it.
    """
    from backend.investor import sources as srcmod

    if os.getenv("INVESTOR_BROKER_VALUATION", "on").lower() == "off":
        return None
    run = await _claim(session, "broker_valuation", day.isoformat())
    if run is None:
        return None

    side = await srcmod.broker_side(session, day)
    if not side.configured:
        run.result = {"skipped": "no MT5 account is marked as holding investor capital"}
        await session.commit()
        return run.result
    if not side.complete:
        # A valuation taken over an incomplete ledger is a confident wrong
        # number, which is worse than no number: it would overwrite the one
        # honest signal the admin has.
        run.result = {"blocked": list(side.missing)}
        logger.error(f"[JOBS] broker valuation blocked: {'; '.join(side.missing)}")
        await session.commit()
        return run.result

    # Cash at the broker PLUS cash confirmed in but not yet transferred there.
    # Broker equity alone would mark every unit down by the amount in transit,
    # which tells investors they lost money that is in a bank account.
    at_broker = side.derived_equity
    in_transit = await srcmod.unfunded_capital(session)
    equity = navmod.money(at_broker + in_transit)
    owed = await fundmod.owed_on(session, day)
    snap = await fundmod.take_snapshot(session, pool_equity=equity, liabilities=owed,
                                       on=day, source="MT5")
    run.result = {
        "pool_equity": navmod.text(equity),
        "at_broker": navmod.text(at_broker),
        "confirmed_not_yet_funded": navmod.text(in_transit),
        "liabilities": navmod.text(owed),
        "nav_per_unit": navmod.text(snap.nav_per_unit) if snap else None,
        "accounts": list(side.logins),
    }
    logger.info(f"[JOBS] broker valuation {day}: {equity} "
                f"({at_broker} at the broker, {in_transit} in transit) "
                f"across {side.logins}")
    await session.commit()
    return run.result


async def digest_stats(session, day: date) -> dict:
    start = datetime(day.year, day.month, day.day) - timedelta(hours=1)   # WAT midnight in UTC
    end = start + timedelta(days=1)

    async def count(model, *where):
        return (await session.execute(select(func.count()).select_from(model).where(*where))).scalar_one()

    confirmed = (await session.execute(
        select(func.coalesce(func.sum(Deposit.amount_confirmed), 0))
        .where(Deposit.state == DEPOSIT_CONFIRMED, Deposit.confirmed_at >= start,
               Deposit.confirmed_at < end))).scalar_one()
    paid = (await session.execute(
        select(func.coalesce(func.sum(Withdrawal.amount_paid), 0))
        .where(Withdrawal.paid_at >= start, Withdrawal.paid_at < end))).scalar_one()
    price = await fundmod.latest_nav(session, day)
    outstanding = await unitsmod.units_in_issue(session)
    return {
        "Day": day.strftime("%a %d %b %Y"),
        "Deposits confirmed": f"${navmod.money(confirmed):,}",
        "Withdrawals paid": f"${navmod.money(paid):,}",
        "New withdrawal requests": await count(Withdrawal, Withdrawal.created_at >= start,
                                               Withdrawal.created_at < end),
        "Deposits waiting to confirm": await count(Deposit, Deposit.state.in_(fundmod.OPEN_DEPOSIT_STATES)),
        "Withdrawals waiting": await count(Withdrawal, Withdrawal.state.in_(fundmod.OPEN_WITHDRAWAL_STATES)),
        "Closures waiting": await count(Investor, Investor.status == "closing"),
        "NAV per unit": navmod.text(price),
        "Assets under management": f"${navmod.amount_for_units(outstanding, price):,}",
    }


async def daily_digest(session, day: date) -> dict | None:
    from backend.notify import outbox
    from backend.notify import templates as mail
    if os.getenv("INVESTOR_DAILY_DIGEST", "on").lower() == "off" or not os.getenv("ADMIN_ALERT_EMAIL"):
        return None
    run = await _claim(session, "daily_digest", day.isoformat())
    if run is None:
        return None
    stats = await digest_stats(session, day)
    run.result = stats
    outbox.queue(session, mail.admin_digest(stats))
    await session.commit()
    return stats


async def tick(now: datetime | None = None) -> None:
    """One pass: do whatever is due. Safe to call as often as you like."""
    from backend.data.database import async_session
    now = now or datetime.now(timezone.utc)
    try:
        # published trades not yet in balances (e.g. published before booking existed)
        from backend.investor import booking
        async with async_session() as s:
            if await booking.book_all(s):
                await s.commit()
    except Exception as exc:
        logger.error(f"[JOBS] trade booking failed: {exc}")
    try:
        # An RL agent that drifted off its charter is already suspended
        # in-process; this is what makes it survive a restart and what tells
        # the admin. Every tick, not once a day: an agent trading against a
        # strategy nobody approved should not wait for the overnight pass.
        from backend.rl import suspension as rl_suspension
        async with async_session() as s:
            if await rl_suspension.drain(s):
                await s.commit()
    except Exception as exc:
        logger.error(f"[JOBS] RL suspension drain failed: {exc}")
    wat = now + timedelta(hours=1)
    if wat.hour < RUN_AFTER_WAT_HOUR:
        return
    yesterday = wat.date() - timedelta(days=1)
    # Valuation first: the digest quotes the NAV, and the ledger check is
    # more useful once the day's real figure is in.
    for job in (broker_valuation, ledger_check, daily_digest):
        try:
            async with async_session() as s:
                await job(s, yesterday)
        except Exception as exc:
            logger.error(f"[JOBS] {job.__name__} failed: {exc}")


async def watch_live() -> int:
    """Announce newly opened trades (live.py). Runs every WATCH_SECONDS."""
    from backend.data.database import async_session
    from backend.investor import live as livemod
    try:
        async with async_session() as s:
            n = await livemod.announce_new(s)
            if n:
                await s.commit()
            return n
    except Exception as exc:
        logger.error(f"[JOBS] live-trade watch failed: {exc}")
        return 0


async def run_forever() -> None:
    from backend.investor import live as livemod
    logger.info("[JOBS] investor jobs loop started")
    last_tick = None
    while True:
        now = asyncio.get_running_loop().time()
        if last_tick is None or now - last_tick >= TICK_SECONDS:
            await tick()
            last_tick = now
        await watch_live()
        await asyncio.sleep(livemod.WATCH_SECONDS)


def start() -> asyncio.Task | None:
    if os.getenv("INVESTOR_JOBS", "on").lower() == "off":
        logger.info("[JOBS] INVESTOR_JOBS=off — scheduled investor jobs disabled")
        return None
    return asyncio.get_running_loop().create_task(run_forever())
