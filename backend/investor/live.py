"""
backend/investor/live.py

Trades that are open right now, as investors may see them, and the "a trade is
live" notification.

What an investor sees of a live trade is the market, the side and when it
opened. Never the strategy, the size, the entry, stop or target: those are the
fund's edge, and the same boundary the closed-trade disclosure keeps (see
disclosure.py). The result comes later, when the admin publishes the closed
trade, and it is booked into balances then (booking.py).

The watcher runs every WATCH_SECONDS in the API process. Each trade is
announced once: the claim is a unique TradeAlert row, so a second worker, or
a restart, does not announce it again. Trades that opened more than
FRESH_HOURS ago are not announced (a late "just opened" would be wrong, and it
keeps the first deploy from announcing old positions). Off with
INVESTOR_LIVE_TRADE_ALERTS=off.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from backend.data.models import Trade
from backend.investor import nav as navmod
from backend.investor import split as splitmod
from backend.investor.models import Investor, TradeAlert
from backend.utils.logger import get_logger

logger = get_logger(__name__)
WATCH_SECONDS = 30
FRESH_HOURS = 6


def enabled() -> bool:
    return os.getenv("INVESTOR_LIVE_TRADE_ALERTS", "on").strip().lower() not in ("off", "0", "false", "no")


def _side(direction: str | None) -> str:
    return "Buy" if (direction or "").upper() == "BUY" else "Sell"


async def holders(session) -> dict:
    """Investors with units right now (end of today)."""
    return await splitmod.holdings_before(session, navmod.accounting_date() + timedelta(days=1))


async def open_for(session, investor_id: str) -> dict:
    """The investor's view of the fund's open trades."""
    invested = investor_id in await holders(session)
    if not invested:
        return {"invested": False, "trades": []}
    rows = (await session.execute(
        select(Trade.id, Trade.symbol, Trade.direction, Trade.entry_time)
        .where(Trade.status == "OPEN").order_by(Trade.entry_time.desc()).limit(50))).all()
    return {"invested": True, "trades": [
        {"id": str(i), "symbol": s, "direction": d, "side": _side(d),
         "opened_at": t.replace(tzinfo=timezone.utc).isoformat() if t else None}
        for i, s, d, t in rows]}


async def announce_new(session, now: datetime | None = None) -> int:
    """Queue a notification for each newly opened trade. Caller commits."""
    from backend.notify import outbox
    from backend.notify import templates as mail
    if not enabled():
        return 0
    now = (now or datetime.now(timezone.utc)).replace(tzinfo=None)
    fresh = now - timedelta(hours=FRESH_HOURS)
    rows = (await session.execute(
        select(Trade).outerjoin(TradeAlert, TradeAlert.trade_id == Trade.id)
        .where(Trade.status == "OPEN", Trade.entry_time >= fresh, TradeAlert.id.is_(None))
        .order_by(Trade.entry_time))).scalars().all()
    if not rows:
        return 0
    held = await holders(session)
    investors = (await session.execute(
        select(Investor).where(Investor.id.in_(list(held) or [""])))).scalars().all()
    n = 0
    for t in rows:
        try:
            async with session.begin_nested():
                session.add(TradeAlert(trade_id=t.id))
        except IntegrityError:
            continue                            # another worker announced it
        for inv in investors:
            outbox.queue(session, mail.trade_opened(inv, t.symbol, _side(t.direction)))
        n += 1
    if n:
        logger.info(f"[LIVE] announced {n} new trade(s) to {len(held)} investor(s)")
    return n

