"""
backend/investor/split.py

How a published trade's result divides between investors, and what an
investor's own money has done: capital put in versus profit or loss.

THE SPLIT
---------
Every investor's money moves by the same percentage, so a trade's dollar
result belongs to investors in proportion to the fund they held when it
closed. "When it closed" is the start of the close day: the units each
investor held at the end of the PREVIOUS day. Money that arrived on the close
day was not in the fund while the trade ran, and money that left that day was.

  share      = investor's units / all units, at the end of the day before
  amount     = trade result x share

Amounts are rounded to the cent by the largest-remainder method, so the
shares add up to the published result exactly: nobody is told a figure that
does not reconcile with the trade. The split is the trade's result before
fees; fees are charged every two months on the whole period, not per trade.

An investor only ever sees their own line. The admin sees every line.

CAPITAL AND PROFIT
------------------
  net invested = money paid in - money paid out
  profit       = value now - net invested
                 (the same figure as value + paid out - paid in)

When profit is positive it has been added on top of the capital. When it is
negative the difference is eating into the capital, and the dashboard says so
in dollars and as a percentage of the net invested. Withdrawals are counted
against capital first, which is what "net invested" means on any statement.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import ROUND_DOWN, Decimal

from sqlalchemy import func, select

from backend.investor import nav as navmod
from backend.investor.models import (
    DISCLOSURE_PUBLISHED,
    KIND_FEE,
    KIND_REDEEM,
    KIND_SUBSCRIBE,
    Investor,
    TradeDisclosure,
    UnitTransaction,
)

D = Decimal
CENT = D("0.01")


async def holdings_before(session, day: date) -> dict[str, Decimal]:
    """Units each investor held at the end of the day before `day`. Only
    positive holdings; an empty dict when nobody was invested."""
    cutoff = day - timedelta(days=1)
    rows = (await session.execute(
        select(UnitTransaction.investor_id, func.coalesce(func.sum(UnitTransaction.units), 0))
        .where(UnitTransaction.effective_date <= cutoff)
        .group_by(UnitTransaction.investor_id))).all()
    return {iid: navmod.units(u) for iid, u in rows if navmod.units(u) > 0}


def allocate(total: Decimal, weights: dict[str, Decimal]) -> dict[str, Decimal]:
    """Split `total` (to the cent) in proportion to `weights`, summing exactly.

    Largest remainder: everyone gets their share rounded toward zero, then the
    leftover cents go one each to the largest fractional parts. Works for a
    loss the same way, with the signs flipped.
    """
    if not weights:
        return {}
    whole = sum(weights.values())
    if whole <= 0:
        return {k: D("0.00") for k in weights}
    sign = D("-1") if total < 0 else D("1")
    cents = int((abs(total) / CENT).to_integral_value())
    exact = {k: D(cents) * w / whole for k, w in weights.items()}
    base = {k: int(v.to_integral_value(rounding=ROUND_DOWN)) for k, v in exact.items()}
    left = cents - sum(base.values())
    for k in sorted(exact, key=lambda k: (exact[k] - base[k], k), reverse=True)[:left]:
        base[k] += 1
    return {k: sign * D(v) * CENT for k, v in base.items()}


async def split_trade(session, row: TradeDisclosure) -> dict:
    """Every investor's line for one published trade (admin view)."""
    held = await holdings_before(session, row.closed_on) if row.closed_on else {}
    total_units = sum(held.values(), D("0"))
    result = navmod.money(row.result_amount) if row.result_amount is not None else None
    amounts = allocate(result, held) if result is not None else {}
    names = {}
    if held:
        names = dict((await session.execute(
            select(Investor.id, Investor.name).where(Investor.id.in_(list(held))))).all())
    lines = sorted((
        {"investor_id": iid, "name": names.get(iid),
         "share_pct": navmod.text((u / total_units * 100).quantize(D("0.0001"))) if total_units else "0",
         "amount": navmod.text(amounts[iid]) if iid in amounts else None}
        for iid, u in held.items()), key=lambda r: -D(r["share_pct"]))
    return {"trade_id": row.trade_id, "symbol": row.symbol, "direction": row.direction,
            "closed_on": row.closed_on.isoformat() if row.closed_on else None,
            "result_amount": navmod.text(result) if result is not None else None,
            "result_pct": None if row.result_pct is None else navmod.text(D(str(row.result_pct))),
            "investors": len(lines), "lines": lines,
            "allocated": navmod.text(sum((amounts.values()), D("0.00")))}


async def my_trades(session, investor_id: str, *, limit: int = 200) -> dict:
    """Published trades as ONE investor sees them: their own share only.

    The fund-wide dollar result is deliberately not returned; the percentage
    is, because it is the same for everyone's money.
    """
    rows = (await session.execute(
        select(TradeDisclosure).where(TradeDisclosure.state == DISCLOSURE_PUBLISHED)
        .order_by(TradeDisclosure.closed_on.desc(), TradeDisclosure.trade_id.desc())
        .limit(limit))).scalars().all()
    cache: dict[date, dict[str, Decimal]] = {}
    out, wins, losses, total = [], 0, 0, D("0.00")
    by_symbol: dict[str, Decimal] = {}
    for r in rows:
        mine = share = None
        if r.closed_on is not None and r.result_amount is not None:
            held = cache.get(r.closed_on)
            if held is None:
                held = cache[r.closed_on] = await holdings_before(session, r.closed_on)
            if investor_id in held:
                amounts = allocate(navmod.money(r.result_amount), held)
                mine = amounts[investor_id]
                share = held[investor_id] / sum(held.values()) * 100
        if mine is not None:
            total += mine
            by_symbol[r.symbol] = by_symbol.get(r.symbol, D("0.00")) + mine
            if mine > 0:
                wins += 1
            elif mine < 0:
                losses += 1
        out.append({
            "id": str(r.trade_id), "symbol": r.symbol, "direction": r.direction,
            "closed_on": r.closed_on.isoformat() if r.closed_on else None,
            "result_pct": None if r.result_pct is None else navmod.text(D(str(r.result_pct))),
            "note": r.note,
            # null when they were not invested when it closed
            "your_amount": None if mine is None else navmod.text(mine),
            "your_share_pct": None if share is None else navmod.text(share.quantize(D("0.01"))),
        })
    taken = wins + losses
    return {
        "trades": out,
        "summary": {
            "your_total": navmod.text(total), "wins": wins, "losses": losses,
            "win_rate_pct": navmod.text((D(wins) / D(taken) * 100).quantize(D("0.1"))) if taken else None,
            "by_symbol": [{"symbol": k, "amount": navmod.text(v)}
                          for k, v in sorted(by_symbol.items(), key=lambda kv: kv[1], reverse=True)],
        },
    }


async def capital_position(session, investor_id: str, value: Decimal) -> dict:
    """Capital against profit for one investor, as the dashboard explains it."""
    async def total(kind):
        return navmod.money((await session.execute(
            select(func.coalesce(func.sum(UnitTransaction.amount), 0))
            .where(UnitTransaction.investor_id == investor_id, UnitTransaction.kind == kind))).scalar_one())
    paid_in, paid_out, fees = await total(KIND_SUBSCRIBE), await total(KIND_REDEEM), await total(KIND_FEE)
    net = navmod.money(paid_in - paid_out)
    value = navmod.money(value)
    profit = navmod.money(value - net)
    capital_base = max(net, D("0.00"))
    if profit >= 0:
        state, capital_now, eroded = "profit", capital_base, D("0.00")
    else:
        state = "capital_loss"
        capital_now = max(value, D("0.00"))
        eroded = navmod.money(capital_base - capital_now)
    pct = (profit / capital_base * 100).quantize(D("0.01")) if capital_base > 0 else None
    return {
        "paid_in": navmod.text(paid_in), "paid_out": navmod.text(paid_out),
        "net_invested": navmod.text(net), "value": navmod.text(value),
        "profit": navmod.text(profit), "profit_pct_of_capital": None if pct is None else navmod.text(pct),
        "state": state if paid_in > 0 else "not_invested",
        "capital_intact": navmod.text(capital_now), "capital_eroded": navmod.text(eroded),
        "profit_on_top": navmod.text(max(profit, D("0.00"))),
        "fees_paid": navmod.text(fees),
    }


async def published_count(session) -> int:
    return (await session.execute(select(func.count(TradeDisclosure.id))
                                  .where(TradeDisclosure.state == DISCLOSURE_PUBLISHED))).scalar_one()

