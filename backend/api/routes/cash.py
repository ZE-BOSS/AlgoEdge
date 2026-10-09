"""
backend/api/routes/cash.py

The Journal's money tab: what the ACCOUNT did, as opposed to what the bot did.

The journal has always shown trades. It has never shown deposits, withdrawals,
the prop firm's allocation, commission, swap, or whether any of it adds up —
so a profitable-looking journal could sit beside a balance that said otherwise
and nothing connected the two. These endpoints are that connection.

Every figure here is derived from `mt5_cash_flows`, which is written only by
`backend/mt5/history.py`. Nothing in this module reads the terminal except
`/sync`, which is the one action that is explicitly "go and look".
"""

from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.routes.auth import get_current_user
from backend.data.database import get_db
from backend.data.models import User
from backend.mt5 import history as histmod
from backend.mt5.cash_models import (
    CAPITAL_IN,
    CAPITAL_OUT,
    CLASSES,
    COST,
    DEAL_TYPE_NAMES,
    TRADE_RESULT,
    UNKNOWN,
    MT5Account,
    MT5CashFlow,
)
from backend.utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/api/cash", tags=["cash"])


def _flow(row: MT5CashFlow) -> dict:
    return {
        "id": str(row.id), "ticket": row.deal_ticket,
        "time": row.deal_time_utc.isoformat() if row.deal_time_utc else None,
        "type": row.deal_type, "type_name": DEAL_TYPE_NAMES.get(row.deal_type, "?"),
        "symbol": row.symbol, "comment": row.comment,
        "profit": row.profit, "commission": row.commission, "swap": row.swap,
        "fee": row.fee, "amount": round(row.amount, 2),
        "flow_class": row.flow_class, "classified_by": row.classified_by,
        "review_reason": row.review_reason,
    }


# ── accounts ────────────────────────────────────────────────────────────────

@router.get("/accounts")
async def list_accounts(db: AsyncSession = Depends(get_db),
                        _: User = Depends(get_current_user)):
    rows = (await db.execute(select(MT5Account).order_by(MT5Account.login))).scalars().all()
    out = []
    for a in rows:
        anchor = await histmod.get_anchor(db, a.login)
        out.append({
            "login": a.login, "label": a.label, "broker": a.broker, "server": a.server,
            "currency": a.currency,
            "holds_investor_capital": bool(a.holds_investor_capital),
            "prop_allocation": a.prop_allocation or 0.0,
            "profit_share_pct": a.profit_share_pct,
            "last_synced_at": a.last_synced_at.isoformat() if a.last_synced_at else None,
            "anchored_at": anchor.as_of.isoformat() if anchor else None,
            "anchor_source": anchor.source if anchor else None,
        })
    return {"accounts": out}


class AccountSettings(BaseModel):
    label: str | None = Field(default=None, max_length=120)
    holds_investor_capital: bool | None = None
    prop_allocation: float | None = Field(default=None, ge=0)
    profit_share_pct: float | None = Field(default=None, ge=0, le=100)
    # {"4": "CAPITAL_IN"} — how THIS broker books capital.
    deal_class_overrides: dict[str, str] | None = None
    adjustment_comment_patterns: list[str] | None = None


@router.patch("/accounts/{login}")
async def update_account(login: int, body: AccountSettings,
                         db: AsyncSession = Depends(get_db),
                         _: User = Depends(get_current_user)):
    import json

    row = await histmod.get_account(db, login)
    if row is None:
        raise HTTPException(status_code=404, detail=f"account {login} is not known yet — sync it first")
    changes = body.model_dump(exclude_unset=True)
    if "deal_class_overrides" in changes and changes["deal_class_overrides"] is not None:
        bad = [v for v in changes["deal_class_overrides"].values() if v not in CLASSES]
        if bad:
            raise HTTPException(status_code=400,
                                detail=f"not a known classification: {', '.join(sorted(set(bad)))}")
        row.deal_class_overrides = json.dumps(changes.pop("deal_class_overrides"))
    if "adjustment_comment_patterns" in changes and changes["adjustment_comment_patterns"] is not None:
        row.adjustment_comment_patterns = json.dumps(
            [str(p).lower() for p in changes.pop("adjustment_comment_patterns")])
    for k, v in changes.items():
        if v is not None:
            setattr(row, k, v)
    await db.flush()
    return {"ok": True, "login": row.login}


# ── sync ────────────────────────────────────────────────────────────────────

@router.post("/sync")
async def sync_now(login: int | None = Query(None),
                   db: AsyncSession = Depends(get_db),
                   _: User = Depends(get_current_user)):
    """Read the attached terminal's deal history into the ledger.

    Idempotent on `(account_login, deal_ticket)`, so pressing this twice does
    nothing the second time. Refuses when MT5 is attached to a different
    account than the one asked for, rather than writing one account's deals
    into another's ledger.
    """
    result = await histmod.sync(db, login=login)
    if not result.get("ok"):
        raise HTTPException(status_code=409, detail=result.get("reason", "sync failed"))
    anchor = await histmod.anchor_from_journal(db, result["login"])
    result["anchored_at"] = anchor.as_of.isoformat() if anchor else None
    result["anchor_note"] = anchor.note if anchor else None
    # Attribute whatever historical journal rows this account's own tickets
    # prove belong to it. Never guesses — see backfill_trade_accounts.
    result["backfill"] = await histmod.backfill_trade_accounts(db, result["login"])
    return result


# ── the ledger ──────────────────────────────────────────────────────────────

@router.get("/ledger")
async def ledger(login: int = Query(...), days: int | None = Query(None, ge=1),
                 flow_class: str | None = Query(None),
                 limit: int = Query(200, ge=1, le=2000),
                 db: AsyncSession = Depends(get_db),
                 _: User = Depends(get_current_user)):
    q = select(MT5CashFlow).where(MT5CashFlow.account_login == login)
    if days:
        q = q.where(MT5CashFlow.deal_time_utc >= datetime.utcnow() - timedelta(days=days))
    if flow_class:
        q = q.where(MT5CashFlow.flow_class == flow_class)
    rows = (await db.execute(
        q.order_by(MT5CashFlow.deal_time_utc.desc()).limit(limit))).scalars().all()
    return {"flows": [_flow(r) for r in rows], "count": len(rows)}


@router.get("/summary")
async def summary(login: int = Query(...), db: AsyncSession = Depends(get_db),
                  _: User = Depends(get_current_user)):
    """Capital, profit and costs as three separate lines — the separation the
    journal never had, and the reason costs were being counted as deposits."""
    sums = await histmod.totals(db, login)
    anchor = await histmod.get_anchor(db, login)

    # Profit split into its parts, because "profit" that quietly includes the
    # swap is the figure that stops matching the balance.
    gross = (await db.execute(
        select(func.coalesce(func.sum(MT5CashFlow.profit), 0.0))
        .where(MT5CashFlow.account_login == login,
               MT5CashFlow.flow_class == TRADE_RESULT,
               MT5CashFlow.profit > 0))).scalar_one()
    gross_loss = (await db.execute(
        select(func.coalesce(func.sum(MT5CashFlow.profit), 0.0))
        .where(MT5CashFlow.account_login == login,
               MT5CashFlow.flow_class == TRADE_RESULT,
               MT5CashFlow.profit < 0))).scalar_one()
    commission, swap, fee = (await db.execute(
        select(func.coalesce(func.sum(MT5CashFlow.commission), 0.0),
               func.coalesce(func.sum(MT5CashFlow.swap), 0.0),
               func.coalesce(func.sum(MT5CashFlow.fee), 0.0))
        .where(MT5CashFlow.account_login == login))).one()

    return {
        "account_login": login,
        "anchored_at": anchor.as_of.isoformat() if anchor else None,
        "anchor_balance": anchor.balance if anchor else None,
        "capital": {
            "in": round(sums.get(CAPITAL_IN, 0.0), 2),
            "out": round(sums.get(CAPITAL_OUT, 0.0), 2),
            "net": round(sums.get(CAPITAL_IN, 0.0) + sums.get(CAPITAL_OUT, 0.0), 2),
        },
        "profit": {
            "gross_profit": round(float(gross), 2),
            "gross_loss": round(float(gross_loss), 2),
            "net": round(sums.get(TRADE_RESULT, 0.0), 2),
            **profit_split(sums.get(TRADE_RESULT, 0.0), sums.get(CAPITAL_OUT, 0.0)),
        },
        "costs": {
            "commission": round(float(commission), 2),
            "swap": round(float(swap), 2),
            "fee": round(float(fee), 2),
            "other": round(sums.get(COST, 0.0), 2),
        },
        "by_class": {k: round(v, 2) for k, v in sums.items()},
    }


def profit_split(net_profit: float, capital_out: float) -> dict:
    """How much of the realised profit is still in the account.

        profit_remaining = realised profit - profit withdrawn

    THE ATTRIBUTION CHOICE, STATED
    ------------------------------
    A withdrawal is just money leaving; nothing in the MT5 ledger says whether
    it was profit or returned capital. Two conventions are possible and they
    give different answers:

      profit first    withdrawals come out of profit until the profit is gone,
                      then out of capital. `profit_remaining` falls to zero
                      before any capital is counted as withdrawn.
      capital first   withdrawals return capital until it is gone, and only
                      then touch profit.

    This uses PROFIT FIRST, and the reason is that it is the conservative one
    for the question people actually ask of this number - "how much of what I
    made is still here". Profit-first says "less than you think" when both
    readings are available, and a figure about your own money should err that
    way rather than the other.

    Both are reported, so the choice is visible rather than assumed:
    `profit_withdrawn` is what this convention attributes, and
    `capital_withdrawn` is the remainder of the same withdrawals.

    A LOSING account has no profit to withdraw. `profit_remaining` is then the
    loss itself - negative - and `profit_withdrawn` is zero, because you cannot
    withdraw a profit you never made.
    """
    # CAPITAL_OUT is stored signed (negative = money left), so the amount
    # withdrawn is its magnitude.
    withdrawn = abs(float(capital_out or 0.0))
    profit = float(net_profit or 0.0)
    if profit <= 0:
        return {"profit_withdrawn": 0.0,
                "capital_withdrawn": round(withdrawn, 2),
                "profit_remaining": round(profit, 2)}
    from_profit = min(profit, withdrawn)
    return {
        "profit_withdrawn": round(from_profit, 2),
        "capital_withdrawn": round(withdrawn - from_profit, 2),
        "profit_remaining": round(profit - from_profit, 2),
    }


@router.get("/periods")
async def periods(login: int = Query(...), grain: str = Query("day"),
                  db: AsyncSession = Depends(get_db),
                  _: User = Depends(get_current_user)):
    """Daily, weekly or monthly rollups on the accounting boundary.

    The boundary is the one the risk engine already uses
    (`RiskParams.accounting_utc_offset_hours`), so a day in the journal is the
    same day the circuit breaker halted on. Two definitions of "today" in one
    product is a support question nobody can answer.
    """
    if grain not in ("day", "week", "month"):
        raise HTTPException(status_code=400, detail="grain must be day, week or month")

    from backend.risk.circuit_breaker import DEFAULT_ACCOUNTING_UTC_OFFSET_HOURS

    offset = DEFAULT_ACCOUNTING_UTC_OFFSET_HOURS
    rows = (await db.execute(
        select(MT5CashFlow).where(MT5CashFlow.account_login == login)
        .order_by(MT5CashFlow.deal_time_utc))).scalars().all()

    buckets: dict[str, dict] = {}
    for r in rows:
        local = r.deal_time_utc + timedelta(hours=offset)
        if grain == "day":
            key = local.date().isoformat()
        elif grain == "week":
            iso = local.isocalendar()
            key = f"{iso[0]}-W{iso[1]:02d}"
        else:
            key = f"{local.year}-{local.month:02d}"
        b = buckets.setdefault(key, {"period": key, "capital_in": 0.0, "capital_out": 0.0,
                                     "trade_result": 0.0, "costs": 0.0, "other": 0.0,
                                     "deals": 0})
        b["deals"] += 1
        if r.flow_class == CAPITAL_IN:
            b["capital_in"] += r.amount
        elif r.flow_class == CAPITAL_OUT:
            b["capital_out"] += r.amount
        elif r.flow_class == TRADE_RESULT:
            b["trade_result"] += r.amount
        elif r.flow_class == COST:
            b["costs"] += r.amount
        else:
            b["other"] += r.amount

    out = [{k: (round(v, 2) if isinstance(v, float) else v) for k, v in b.items()}
           for b in buckets.values()]
    return {"grain": grain, "accounting_offset_hours": offset, "periods": out}


# ── curves ──────────────────────────────────────────────────────────────────

@router.get("/curves")
async def curves(login: int | None = Query(None),
                 db: AsyncSession = Depends(get_db),
                 _: User = Depends(get_current_user)):
    """Daily series, aligned on one date axis, for the Analytics charts.

    One response rather than four endpoints, because the whole point is to see
    them against each other: an account balance that falls while investor
    claims hold flat is a different story from both falling together, and
    fetching them separately invites charting two series from different days.

    Every series is cumulative from the anchor, so the first point is the
    opening balance rather than zero — a curve that starts at zero implies the
    account did, and on a funded account that is a lie.
    """
    from backend.investor import nav as navmod
    from backend.investor import sources as srcmod
    from backend.investor.models import InvestorUnits, NavSnapshot

    logins = ([int(login)] if login is not None
              else [a.login for a in await srcmod.capital_accounts(db)])
    if not logins:
        return {"days": [], "series": {}, "accounts": [],
                "note": "no account selected and none is marked as holding investor capital"}

    rows = (await db.execute(
        select(MT5CashFlow).where(MT5CashFlow.account_login.in_(logins))
        .order_by(MT5CashFlow.deal_time_utc))).scalars().all()

    opening = 0.0
    for lg in logins:
        anchor_row = await histmod.get_anchor(db, lg)
        if anchor_row:
            opening += float(anchor_row.balance)

    # one bucket per day that actually had activity; a gap means nothing moved
    by_day: dict[str, dict] = {}
    for r in rows:
        key = r.deal_time_utc.date().isoformat()
        b = by_day.setdefault(key, {"capital": 0.0, "result": 0.0, "cost": 0.0})
        if r.flow_class in (CAPITAL_IN, CAPITAL_OUT):
            b["capital"] += r.amount
        elif r.flow_class == TRADE_RESULT:
            b["result"] += r.amount
        else:
            b["cost"] += r.amount

    days = sorted(by_day)
    balance, capital, result = opening, 0.0, 0.0
    series = {"balance": [], "capital": [], "trade_result": []}
    for d in days:
        b = by_day[d]
        balance += b["capital"] + b["result"] + b["cost"]
        capital += b["capital"]
        result += b["result"] + b["cost"]
        series["balance"].append(round(balance, 2))
        series["capital"].append(round(capital, 2))
        series["trade_result"].append(round(result, 2))

    # What investors were told, on the same axis. Carried forward between
    # snapshots rather than interpolated: a NAV is a statement made on a day,
    # and drawing a slope between two of them invents prices nobody was quoted.
    snaps = (await db.execute(
        select(NavSnapshot).order_by(NavSnapshot.as_of_date))).scalars().all()
    units = sum((navmod.units(r.units) for r in
                 (await db.execute(select(InvestorUnits))).scalars()), navmod.units(0))
    claims, nav_line, last_nav = [], [], None
    for d in days:
        for s in snaps:
            if s.as_of_date.isoformat() <= d:
                last_nav = s
        nav_line.append(float(last_nav.nav_per_unit) if last_nav else None)
        claims.append(round(float(last_nav.nav_per_unit) * float(units), 2)
                      if last_nav else None)
    series["nav_per_unit"] = nav_line
    series["investor_claims"] = claims
    # The gap, on the same axis, so "is it closing" is a shape rather than a
    # number somebody has to remember from last week.
    series["shortfall"] = [
        None if c is None else round(c - b, 2)
        for c, b in zip(claims, series["balance"])]

    return {"days": days, "series": series, "accounts": logins,
            "opening_balance": round(opening, 2),
            "units_in_issue": str(units)}


# ── reconciliation and the review queue ────────────────────────────────────

@router.get("/reconciliation")
async def reconciliation(login: int = Query(...),
                         balance: float | None = Query(None),
                         db: AsyncSession = Depends(get_db),
                         _: User = Depends(get_current_user)):
    """Does the ledger add up to the balance? Answered either way.

    `balance` is the broker's figure. Without it the live terminal is asked —
    but only when it is attached to THIS account, because comparing one
    account's ledger against another's balance produces a confident wrong
    number, which is worse than no number.
    """
    actual, source = balance, "given"
    if actual is None:
        snap = await histmod.account_snapshot()
        if snap and int(snap["login"]) == int(login):
            actual, source = snap["balance"], "mt5_live"
        else:
            source = "unavailable"
    report = await histmod.reconcile(db, login, actual_balance=actual)
    report["balance_source"] = source
    return report


@router.get("/review")
async def review_queue(login: int | None = Query(None),
                       db: AsyncSession = Depends(get_db),
                       _: User = Depends(get_current_user)):
    """Deals the classifier refused to guess at.

    These block `healthy` on purpose. A ledger that balances while something
    in it is unexplained is not reconciled.
    """
    q = select(MT5CashFlow).where(MT5CashFlow.flow_class == UNKNOWN)
    if login:
        q = q.where(MT5CashFlow.account_login == login)
    rows = (await db.execute(q.order_by(MT5CashFlow.deal_time_utc.desc()).limit(500))).scalars().all()
    return {"flows": [_flow(r) for r in rows], "count": len(rows)}


class Reclassify(BaseModel):
    flow_class: str
    note: str | None = Field(default=None, max_length=500)


@router.post("/review/{flow_id}")
async def reclassify(flow_id: int, body: Reclassify,
                     db: AsyncSession = Depends(get_db),
                     user: User = Depends(get_current_user)):
    """Place one deal by hand. The raw figures are never touched."""
    if body.flow_class not in CLASSES:
        raise HTTPException(status_code=400, detail=f"not a known classification: {body.flow_class}")
    row = await db.get(MT5CashFlow, flow_id)
    if row is None:
        raise HTTPException(status_code=404, detail="no such cash flow")
    row.flow_class = body.flow_class
    row.classified_by = str(user.id)
    row.classified_at = datetime.utcnow()
    row.review_reason = body.note
    await db.flush()
    logger.info(f"[CASH] deal {row.deal_ticket} reclassified as {body.flow_class} by {user.id}")
    return _flow(row)
