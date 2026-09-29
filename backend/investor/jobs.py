"""
backend/investor/jobs.py

The investor platform's scheduled work, run by one asyncio loop in the API
process:

  * ledger_check — nightly: every investor's cached units must equal a replay
    of the ledger. A mismatch emails the admin. The ledger is the truth; this
    is how a bug is found that night rather than at withdrawal time.
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
    wat = now + timedelta(hours=1)
    if wat.hour < RUN_AFTER_WAT_HOUR:
        return
    yesterday = wat.date() - timedelta(days=1)
    for job in (ledger_check, daily_digest):
        try:
            async with async_session() as s:
                await job(s, yesterday)
        except Exception as exc:
            logger.error(f"[JOBS] {job.__name__} failed: {exc}")


async def run_forever() -> None:
    logger.info("[JOBS] investor jobs loop started")
    while True:
        await tick()
        await asyncio.sleep(TICK_SECONDS)


def start() -> asyncio.Task | None:
    if os.getenv("INVESTOR_JOBS", "on").lower() == "off":
        logger.info("[JOBS] INVESTOR_JOBS=off — scheduled investor jobs disabled")
        return None
    return asyncio.get_running_loop().create_task(run_forever())
