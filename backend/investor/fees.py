"""
backend/investor/fees.py

Management and performance fees, charged per investor every two months
(Jan-Feb, Mar-Apr, ... Nov-Dec, WAT calendar).

THE ARITHMETIC
--------------
For one investor over one period:

  value         = their units at period end x the period-end NAV
  value_before  = their units the day before the period x that day's NAV
  period profit = value - value_before + paid out to them in the period
                  - paid in by them in the period
  management    = management_pct / 100 x max(0, period profit)
                  (a share of the period's returns; nothing in a losing period)
  profit        = value - management + paid out to them - paid in by them
                  (lifetime, from their own ledger, up to the period end)
  performance   = performance_pct / 100 x max(0, profit - high_water_mark)
  new mark      = max(old mark, profit - performance)

The management fee comes off first and the performance fee is worked out on
what is left, so no dollar of profit is charged twice.

THE HIGH-WATER MARK is kept in MONEY (lifetime profit, net of fees), per
investor, on each performance accrual. An investor is charged only on profit
above the best net position they have already paid a fee on, so a loss has to be
earned back before any performance fee is charged again. Setting the mark
after the fee (profit - performance) rather than before is what stops the
investor paying twice: if the mark were set at the pre-fee peak, they would have
to re-earn the fee itself before being charged again.

Profit is measured with the management fee already taken off. Both fees are
charged only on gains.

Rates come from the terms the investor is held under (fund.terms_for): the
terms they committed under, with any per-investor rates the admin set. A fee
is part of the promise, like the lock-up.

CHARGING
--------
A charged fee cancels units at the period-end NAV (units.charge_fee, kind FEE).
The money is still in the broker account until the manager takes it out, so
until then it is a liability: fund.owed_on() includes it, which keeps it out of
the NAV every other investor is priced at. `mark_paid` records it leaving.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import desc, func, select

from backend.investor import fund as fundmod
from backend.investor import nav as navmod
from backend.investor import units as unitsmod
from backend.investor.models import (
    FEE_MANAGEMENT,
    FEE_PERFORMANCE,
    KIND_REDEEM,
    KIND_SUBSCRIBE,
    FeeAccrual,
    Investor,
    NavSnapshot,
    UnitTransaction,
)

D = Decimal
STALE_SNAPSHOT_DAYS = 7


@dataclass
class FeeLine:
    investor_id: str
    name: str | None
    units: Decimal
    nav: Decimal
    value: Decimal
    value_before: Decimal
    period_profit: Decimal
    days: int
    management_pct: Decimal
    management: Decimal
    profit: Decimal
    high_water_mark: Decimal
    performance_pct: Decimal
    performance: Decimal
    new_high_water_mark: Decimal

    @property
    def total(self) -> Decimal:
        return navmod.money(self.management + self.performance)

    def as_dict(self) -> dict:
        t = navmod.text
        return {
            "investor_id": self.investor_id, "name": self.name, "units": t(self.units),
            "nav_per_unit": t(self.nav), "value": t(self.value), "days": self.days,
            "value_before": t(self.value_before), "period_profit": t(self.period_profit),
            "management_pct": t(self.management_pct.normalize()), "management": t(self.management),
            "profit": t(self.profit), "high_water_mark": t(self.high_water_mark),
            "performance_pct": t(self.performance_pct.normalize()), "performance": t(self.performance),
            "new_high_water_mark": t(self.new_high_water_mark), "total": t(self.total),
        }


async def _ledger_sum(session, investor_id, kind, until: date, column) -> Decimal:
    return D(str((await session.execute(
        select(func.coalesce(func.sum(column), 0))
        .where(UnitTransaction.investor_id == investor_id, UnitTransaction.kind == kind,
               UnitTransaction.effective_date <= until))).scalar_one()))


async def _units_at(session, investor_id: str, until: date) -> Decimal:
    return navmod.units((await session.execute(
        select(func.coalesce(func.sum(UnitTransaction.units), 0))
        .where(UnitTransaction.investor_id == investor_id,
               UnitTransaction.effective_date <= until))).scalar_one())


async def high_water_mark(session, investor_id: str) -> Decimal:
    """The latest mark, or zero: before any fee, the mark is "no profit yet"."""
    row = (await session.execute(
        select(FeeAccrual.high_water_mark)
        .where(FeeAccrual.investor_id == investor_id, FeeAccrual.kind == FEE_PERFORMANCE,
               FeeAccrual.high_water_mark.is_not(None))
        .order_by(desc(FeeAccrual.period_end), desc(FeeAccrual.id)).limit(1)
    )).scalar_one_or_none()
    return navmod.money(row) if row is not None else D("0.00")


async def compute(session, investor_id: str, period_start: date, period_end: date,
                  *, price: Decimal | None = None) -> FeeLine | None:
    """What this investor owes for the period. Writes nothing. None if they
    held no units at the period end."""
    held = await _units_at(session, investor_id, period_end)
    if held <= 0:
        return None
    price = navmod.nav(price if price is not None else await fundmod.latest_nav(session, period_end))
    value = navmod.amount_for_units(held, price)
    terms = await fundmod.terms_for(session, investor_id)
    mgmt_pct = D(str(terms.management_fee_pct))
    perf_pct = D(str(terms.performance_fee_pct))

    first = (await session.execute(
        select(func.min(UnitTransaction.effective_date))
        .where(UnitTransaction.investor_id == investor_id, UnitTransaction.kind == KIND_SUBSCRIBE)
    )).scalar_one()
    start = max(period_start, first) if first else period_start
    days = max(0, (period_end - start).days + 1)

    paid_in = await _ledger_sum(session, investor_id, KIND_SUBSCRIBE, period_end, UnitTransaction.amount)
    paid_out = await _ledger_sum(session, investor_id, KIND_REDEEM, period_end, UnitTransaction.amount)

    # the period's own result: what the holding gained, net of money in and out
    day_before = period_start - timedelta(days=1)
    held_before = await _units_at(session, investor_id, day_before)
    value_before = (navmod.amount_for_units(held_before, await fundmod.latest_nav(session, day_before))
                    if held_before > 0 else D("0.00"))
    in_before = await _ledger_sum(session, investor_id, KIND_SUBSCRIBE, day_before, UnitTransaction.amount)
    out_before = await _ledger_sum(session, investor_id, KIND_REDEEM, day_before, UnitTransaction.amount)
    period_profit = navmod.money(value - value_before + (paid_out - out_before) - (paid_in - in_before))
    management = navmod.money(max(D("0"), period_profit) * mgmt_pct / D("100"))
    profit = navmod.money(value - management + paid_out - paid_in)
    mark = await high_water_mark(session, investor_id)
    performance = navmod.money(max(D("0"), profit - mark) * perf_pct / D("100"))
    new_mark = navmod.money(max(mark, profit - performance))

    investor = await session.get(Investor, investor_id)
    return FeeLine(investor_id, getattr(investor, "name", None), held, price, value, value_before,
                   period_profit, days, mgmt_pct, management, profit, mark, perf_pct, performance, new_mark)


def month_period(year: int, month: int) -> tuple[date, date]:
    first = date(year, month, 1)
    return first, navmod.month_bounds(first)[1]


def fee_period(year: int, month: int) -> tuple[date, date]:
    """The two-month fee period that contains this month: Jan-Feb, Mar-Apr, ..."""
    start_month = month if month % 2 == 1 else month - 1
    return date(year, start_month, 1), month_period(year, start_month + 1)[1]


async def _check_price(session, period_end: date) -> NavSnapshot:
    snap = (await session.execute(
        select(NavSnapshot).where(NavSnapshot.as_of_date <= period_end)
        .order_by(desc(NavSnapshot.as_of_date)).limit(1))).scalar_one_or_none()
    if snap is None or (period_end - snap.as_of_date).days > STALE_SNAPSHOT_DAYS:
        raise fundmod.FundError(
            f"take a NAV snapshot within {STALE_SNAPSHOT_DAYS} days before {period_end} first — "
            f"fees are charged at the period-end price"
            + (f" (the latest is {snap.as_of_date})" if snap else ""))
    return snap


async def already_closed(session, period_start: date, period_end: date) -> bool:
    return (await session.execute(
        select(func.count(FeeAccrual.id)).where(FeeAccrual.period_start == period_start,
                                                FeeAccrual.period_end == period_end)
    )).scalar_one() > 0


async def charged_lines(session, period_start: date, period_end: date) -> list[dict]:
    """What WAS charged for a closed period, from the accrual rows.

    Never a recalculation: after charging, the units are fewer and the mark has
    moved, so computing again gives different — wrong — figures for a month
    whose fees are already on the ledger. Caught in the browser.
    """
    rows = (await session.execute(
        select(FeeAccrual, Investor.name).join(Investor, Investor.id == FeeAccrual.investor_id)
        .where(FeeAccrual.period_start == period_start, FeeAccrual.period_end == period_end)
        .order_by(Investor.name, FeeAccrual.kind))).all()
    by: dict[str, dict] = {}
    for acc, name in rows:
        line = by.setdefault(acc.investor_id, {
            "investor_id": acc.investor_id, "name": name, "value": None, "days": None,
            "period_profit": None,
            "management_pct": None, "management": "0.00", "profit": None,
            "high_water_mark": None, "performance_pct": None, "performance": "0.00",
            "state": acc.state})
        if acc.kind == FEE_MANAGEMENT:
            line.update(period_profit=navmod.text(acc.basis_amount), days=acc.days,
                        management_pct=navmod.text(D(str(acc.rate_pct)).normalize()),
                        management=navmod.text(acc.fee_amount))
        else:
            line.update(new_high_water_mark=navmod.text(acc.high_water_mark),
                        high_water_mark=None, above_mark=navmod.text(acc.basis_amount),
                        performance_pct=navmod.text(D(str(acc.rate_pct)).normalize()),
                        performance=navmod.text(acc.fee_amount))
        if acc.state == "waived":
            line["state"] = "waived"
    for line in by.values():
        line["total"] = navmod.text(navmod.money(D(line["management"]) + D(line["performance"])))
    return list(by.values())


async def preview(session, period_start: date, period_end: date) -> dict:
    if await already_closed(session, period_start, period_end):
        lines = await charged_lines(session, period_start, period_end)
        return {"period_start": period_start.isoformat(), "period_end": period_end.isoformat(),
                "priced_on": None, "nav_per_unit": None, "already_closed": True, "lines": lines,
                "total": navmod.text(navmod.money(sum((D(ln["total"]) for ln in lines
                                                       if ln["state"] != "waived"), D("0"))))}
    snap = await _check_price(session, period_end)
    ids = (await session.execute(select(UnitTransaction.investor_id).distinct())).scalars().all()
    lines = [ln for ln in [await compute(session, i, period_start, period_end,
                                          price=snap.nav_per_unit) for i in ids] if ln]
    lines.sort(key=lambda ln: (ln.name or "", ln.investor_id))
    return {
        "period_start": period_start.isoformat(), "period_end": period_end.isoformat(),
        "priced_on": snap.as_of_date.isoformat(), "nav_per_unit": navmod.text(snap.nav_per_unit),
        "already_closed": await already_closed(session, period_start, period_end),
        "lines": [ln.as_dict() for ln in lines],
        "total": navmod.text(navmod.money(sum((ln.total for ln in lines), D("0")))),
    }


async def _record(session, line: FeeLine, kind: str, amount: Decimal, *, period_start, period_end,
                  waived: bool, actor_id: str, basis: Decimal, rate: Decimal,
                  mark: Decimal | None = None, days: int | None = None) -> FeeAccrual | None:
    if amount <= 0 and kind == FEE_MANAGEMENT:
        return None
    now = datetime.now(timezone.utc)
    row = FeeAccrual(investor_id=line.investor_id, kind=kind, period_start=period_start,
                     period_end=period_end, basis_amount=navmod.money(basis), rate_pct=rate,
                     fee_amount=navmod.money(amount), high_water_mark=mark, days=days,
                     state="waived" if waived else ("charged" if amount > 0 else "none"),
                     charged_at=None if waived or amount <= 0 else now, decided_by=actor_id)
    session.add(row)
    await session.flush()
    if not waived and amount > 0:
        await unitsmod.charge_fee(session, investor_id=line.investor_id, amount=amount,
                                  price=line.nav, effective=period_end, source_id=row.id,
                                  created_by=actor_id, note=f"{kind.lower()} fee {period_start}..{period_end}")
    return row


async def charge(session, line: FeeLine, *, period_start: date, period_end: date,
                 actor_id: str, waive: bool = False) -> list[FeeAccrual]:
    """Record and (unless waived) charge one investor's fees for a period.

    A performance row is written even when it is zero, because it carries the
    high-water mark forward. A waived performance fee still moves the mark: the
    investor was let off that fee, not given the right to be charged it later.
    """
    out = []
    m = await _record(session, line, FEE_MANAGEMENT, line.management, period_start=period_start,
                      period_end=period_end, waived=waive, actor_id=actor_id,
                      basis=max(D("0"), line.period_profit), rate=line.management_pct, days=line.days)
    if m:
        out.append(m)
    out.append(await _record(session, line, FEE_PERFORMANCE, line.performance,
                             period_start=period_start, period_end=period_end, waived=waive,
                             actor_id=actor_id, basis=max(D("0"), line.profit - line.high_water_mark),
                             rate=line.performance_pct, mark=line.new_high_water_mark))
    return out


async def close_period(session, period_start: date, period_end: date, *, actor_id: str,
                       waive: set[str] | None = None) -> dict:
    """Charge every investor's fees for a period. Once per period.

    `waive` lists investor ids to record but not charge. A waiver after the
    fact is a unit correction with a reason, like any other refund.
    """
    if await already_closed(session, period_start, period_end):
        raise fundmod.FundError(f"fees for {period_start}..{period_end} were already charged")
    result = await preview(session, period_start, period_end)
    waive = set(waive or ())
    snap = await _check_price(session, period_end)
    ids = [ln["investor_id"] for ln in result["lines"]]
    charged = D("0")
    for iid in ids:
        line = await compute(session, iid, period_start, period_end, price=snap.nav_per_unit)
        await charge(session, line, period_start=period_start, period_end=period_end,
                     actor_id=actor_id, waive=iid in waive)
        if iid not in waive:
            charged += line.total
    await fundmod.audit(session, actor_id=actor_id, action="fees.charged", entity_type="fee_period",
                        entity_id=f"{period_start}..{period_end}",
                        detail={"investors": len(ids), "charged": navmod.text(navmod.money(charged)),
                                "waived": sorted(waive)})
    return {**result, "charged": navmod.text(navmod.money(charged)), "waived": sorted(waive),
            "already_closed": True}


async def mark_paid(session, period_start: date, period_end: date, *, actor_id: str,
                    reference: str | None = None, on: date | None = None) -> int:
    """The manager has taken this period's fees out of the broker account, so
    they stop being a liability of the fund."""
    rows = (await session.execute(
        select(FeeAccrual).where(FeeAccrual.period_start == period_start,
                                 FeeAccrual.period_end == period_end,
                                 FeeAccrual.state == "charged"))).scalars().all()
    if not rows:
        raise fundmod.FundError("no charged, unpaid fees for that period")
    when = fundmod.paid_moment(on)
    for r in rows:
        r.state = "paid"
        r.paid_at = when
    await fundmod.audit(session, actor_id=actor_id, action="fees.paid", entity_type="fee_period",
                        entity_id=f"{period_start}..{period_end}",
                        detail={"rows": len(rows), "reference": reference,
                                "amount": navmod.text(navmod.money(sum((D(str(r.fee_amount)) for r in rows), D("0"))))})
    return len(rows)


async def periods(session) -> list[dict]:
    rows = (await session.execute(
        select(FeeAccrual.period_start, FeeAccrual.period_end, FeeAccrual.kind, FeeAccrual.state,
               func.count(FeeAccrual.id), func.coalesce(func.sum(FeeAccrual.fee_amount), 0))
        .group_by(FeeAccrual.period_start, FeeAccrual.period_end, FeeAccrual.kind, FeeAccrual.state)
        .order_by(desc(FeeAccrual.period_end)))).all()
    out: dict[tuple, dict] = {}
    for start, end, kind, state, n, amount in rows:
        p = out.setdefault((start, end), {"period_start": start.isoformat(), "period_end": end.isoformat(),
                                          "management": D("0"), "performance": D("0"),
                                          "charged": D("0"), "paid": D("0"), "waived": D("0")})
        amt = D(str(amount))
        p["management" if kind == FEE_MANAGEMENT else "performance"] += amt
        if state in ("charged", "paid", "waived"):
            p[state] += amt
    return [{k: (navmod.text(navmod.money(v)) if isinstance(v, D) else v) for k, v in p.items()}
            for p in out.values()]


def previous_fee_period(today: date | None = None) -> tuple[date, date]:
    """The last two-month period that has fully ended."""
    start, _ = fee_period(*((today or navmod.accounting_date()).timetuple()[:2]))
    return fee_period(*((start - timedelta(days=1)).timetuple()[:2]))


def previous_month(today: date | None = None) -> tuple[date, date]:
    first = (today or navmod.accounting_date()).replace(day=1)
    last_prev = first - timedelta(days=1)
    return month_period(last_prev.year, last_prev.month)
