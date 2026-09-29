"""
backend/api/routes/investor_portal.py

Phase 3 — what an investor's own app talks to. Everything under /api/investor.

An investor sees their own position and nothing else. Every route resolves the
investor from the token and takes no investor id from the request, so there is
no parameter to change to read somebody else's account.

What is deliberately NOT here: pool equity, other investors, strategy identity,
admin notes on ledger rows, or who approved what. The fund's NAV per unit is
shown (it is the price of their units); the size of the fund is not.

Money goes over the wire as strings, as on the admin side.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.data.database import get_db
from backend.investor import auth as authmod
from backend.investor import disclosure as discmod
from backend.investor import fund as fundmod
from backend.investor import nav as navmod
from backend.investor import reconcile as recmod
from backend.investor import units as unitsmod
from backend.investor.models import (
    Deposit,
    Investor,
    NavSnapshot,
    UnitTransaction,
    Withdrawal,
)

router = APIRouter(prefix="/api/investor", tags=["investor"])
_bearer = OAuth2PasswordBearer(tokenUrl="/api/investor/auth/login", auto_error=False)


def _ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _refuse(exc: fundmod.FundError | unitsmod.LedgerError, status: int = 400):
    raise HTTPException(status_code=status, detail=str(exc)) from exc


async def current_investor(token: str | None = Depends(_bearer),
                           db: AsyncSession = Depends(get_db)) -> Investor:
    try:
        return await authmod.resolve(db, token, "access")
    except authmod.AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc),
                            headers={"WWW-Authenticate": "Bearer"}) from exc


def _s(v):
    return navmod.text(v) if isinstance(v, Decimal) else (None if v is None else str(v))


def _iso(v):
    return v.isoformat() if v is not None else None


# ── auth ─────────────────────────────────────────────────────────────────────

class Login(BaseModel):
    email: str = Field(max_length=255)
    password: str = Field(max_length=200)


class Refresh(BaseModel):
    refresh_token: str


class AcceptLink(BaseModel):
    token: str = Field(min_length=10, max_length=200)
    password: str = Field(max_length=200)


class ChangePassword(BaseModel):
    current_password: str = Field(max_length=200)
    new_password: str = Field(max_length=200)


def _session(inv: Investor) -> dict:
    return {"access_token": authmod.issue(inv, "access"),
            "refresh_token": authmod.issue(inv, "refresh"),
            "token_type": "bearer",
            "investor": {"id": inv.id, "name": inv.name, "email": inv.email}}


@router.post("/auth/login")
async def login(body: Login, request: Request, db: AsyncSession = Depends(get_db)):
    try:
        inv = await authmod.login(db, email=body.email, password=body.password, ip=_ip(request))
    except authmod.AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    return _session(inv)


@router.post("/auth/refresh")
async def refresh(body: Refresh, db: AsyncSession = Depends(get_db)):
    try:
        inv = await authmod.resolve(db, body.refresh_token, "refresh")
    except authmod.AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    return _session(inv)


@router.post("/auth/accept")
async def accept_link(body: AcceptLink, db: AsyncSession = Depends(get_db)):
    """Set a password from an invitation or reset link, and sign in."""
    try:
        inv = await authmod.use_link(db, token=body.token, password=body.password)
    except authmod.AuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _session(inv)


@router.post("/auth/password")
async def change_password(body: ChangePassword, inv: Investor = Depends(current_investor),
                          db: AsyncSession = Depends(get_db)):
    """Returns fresh tokens: the old ones stop working the moment this commits."""
    try:
        await authmod.change_password(db, investor=inv, current=body.current_password,
                                      new=body.new_password)
    except authmod.AuthError as exc:
        _refuse(exc)
    return _session(inv)


# ── my position ──────────────────────────────────────────────────────────────

@router.get("/me")
async def me(inv: Investor = Depends(current_investor), db: AsyncSession = Depends(get_db)):
    statement = await recmod.investor_statement(db, inv.id)
    terms = await fundmod.terms_for(db, inv.id)
    live = await fundmod.current_settings(db)
    until = await fundmod.lockup_until(db, inv.id)
    return {
        "investor": {
            "id": inv.id, "name": inv.name, "email": inv.email, "status": inv.status,
            "phone": inv.phone, "country": inv.country,
            "joined": _iso(inv.activated_at or inv.created_at),
            "payout": {
                "bank_name": inv.payout_bank_name,
                "account_number": inv.payout_account_number,
                "account_name": inv.payout_account_name,
            },
        },
        "statement": statement,
        "lockup_until": _iso(until),
        # the terms their money is held under, which may be older than today's
        "terms": {
            "version": terms.version,
            "performance_fee_pct": _s(terms.performance_fee_pct),
            "management_fee_pct": _s(terms.management_fee_pct),
            "withdrawal_cap_pct": _s(terms.withdrawal_cap_pct),
            "lockup_days": terms.lockup_days,
            "notice_days": terms.notice_days,
            "min_investment": _s(live.min_investment),
            "base_currency": live.base_currency,
        },
    }


@router.get("/nav")
async def nav_history(limit: int = Query(730, le=5000), db: AsyncSession = Depends(get_db),
                      inv: Investor = Depends(current_investor)):
    """The unit price over time. Price only — not the size of the fund."""
    rows = (await db.execute(
        select(NavSnapshot.as_of_date, NavSnapshot.nav_per_unit)
        .order_by(desc(NavSnapshot.as_of_date)).limit(limit))).all()
    return [{"date": d.isoformat(), "nav_per_unit": _s(n)} for d, n in reversed(rows)]


KIND_LABEL = {"SUBSCRIBE": "Units bought", "REDEEM": "Units sold",
              "FEE": "Fee", "CORRECTION": "Correction"}


@router.get("/activity")
async def activity(inv: Investor = Depends(current_investor), db: AsyncSession = Depends(get_db)):
    """Their ledger, deposits and withdrawals.

    Ledger notes and who-approved are left out: they are the operator's working
    notes. A correction is still shown, labelled as one, with its units and
    price — an investor must be able to see every movement of their units.
    """
    terms = await fundmod.terms_for(db, inv.id)
    ledger = (await db.execute(
        select(UnitTransaction).where(UnitTransaction.investor_id == inv.id)
        .order_by(desc(UnitTransaction.effective_date), desc(UnitTransaction.id)))).scalars().all()
    deposits = (await db.execute(
        select(Deposit).where(Deposit.investor_id == inv.id)
        .order_by(desc(Deposit.created_at), desc(Deposit.id)))).scalars().all()
    withdrawals = (await db.execute(
        select(Withdrawal).where(Withdrawal.investor_id == inv.id)
        .order_by(desc(Withdrawal.created_at), desc(Withdrawal.id)))).scalars().all()
    return {
        "ledger": [{"id": str(t.id), "date": t.effective_date.isoformat(), "kind": t.kind,
                    "label": KIND_LABEL.get(t.kind, t.kind), "units": _s(t.units),
                    "nav_per_unit": _s(t.nav_per_unit), "amount": _s(t.amount)} for t in ledger],
        "deposits": [{"id": str(d.id), "state": d.state, "amount_claimed": _s(d.amount_claimed),
                      "amount_confirmed": _s(d.amount_confirmed), "currency": d.currency,
                      "priced_on": _iso(d.effective_date), "reason": d.rejected_reason,
                      "created_at": _iso(d.created_at)} for d in deposits],
        "withdrawals": [{
            "id": str(w.id), "state": w.state, "amount_requested": _s(w.amount_requested),
            "amount_paid": _s(w.amount_paid), "is_exception": w.is_exception,
            "reason": w.declined_reason, "payment_reference": w.payment_reference,
            "created_at": _iso(w.created_at), "paid_at": _iso(w.paid_at),
            # the notice period is a promise about WHEN, so show the date it implies
            "expected_by": _iso((w.created_at + timedelta(days=terms.notice_days)).date())
            if w.created_at and w.state not in ("paid", "declined") else None,
        } for w in withdrawals],
    }


@router.get("/trades")
async def trades(limit: int = Query(100, le=500), offset: int = 0,
                 inv: Investor = Depends(current_investor), db: AsyncSession = Depends(get_db)):
    """Published trades only, in the stripped public shape."""
    return await discmod.published(db, limit=limit, offset=offset)


# ── money in ────────────────────────────────────────────────────────────────

class Claim(BaseModel):
    amount: Decimal = Field(gt=0)
    note: str | None = Field(default=None, max_length=500)


@router.get("/deposits/instructions")
async def deposit_instructions(inv: Investor = Depends(current_investor),
                               db: AsyncSession = Depends(get_db)):
    s = await fundmod.current_settings(db)
    return {
        "bank_name": s.bank_name, "account_number": s.bank_account_number,
        "account_name": s.bank_account_name, "instructions": s.bank_instructions,
        "reference_code": fundmod.reference_code(inv.id),
        "min_investment": _s(s.min_investment), "currency": s.base_currency,
        "configured": bool(s.bank_account_number),
    }


@router.post("/deposits", status_code=201)
async def claim_deposit(body: Claim, request: Request, inv: Investor = Depends(current_investor),
                        db: AsyncSession = Depends(get_db)):
    try:
        d = await fundmod.claim_deposit(db, investor_id=inv.id, amount=body.amount,
                                        note=body.note, ip=_ip(request))
    except fundmod.FundError as exc:
        _refuse(exc)
    return {"id": str(d.id), "state": d.state, "amount_claimed": _s(d.amount_claimed),
            "reference_code": d.reference_code}


# ── money out ───────────────────────────────────────────────────────────────

class WithdrawalRequest(BaseModel):
    amount: Decimal = Field(gt=0)
    justification: str | None = Field(default=None, max_length=2000)


@router.get("/withdrawals/quote")
async def withdrawal_quote(amount: Decimal | None = Query(None, gt=0),
                           inv: Investor = Depends(current_investor),
                           db: AsyncSession = Depends(get_db)):
    """What the form shows while the investor types: the limit, why, and whether
    this amount would go through as standard or as an exception.

    Computed by the same functions request_withdrawal uses, so the preview and
    the outcome cannot disagree.
    """
    day = navmod.accounting_date()
    price = await fundmod.latest_nav(db, day)
    value = navmod.amount_for_units(await unitsmod.ledger_units(db, inv.id), price)
    cap = await fundmod.withdrawal_cap(db, inv.id, day)
    until = await fundmod.lockup_until(db, inv.id)
    locked = until is not None and day < until
    out = {
        "holding_value": _s(value), "standard_limit": _s(cap.cap),
        "explanation": cap.explanation, "cap_pct": _s(cap.cap_pct.normalize()),
        "month_profit": _s(cap.month_profit),
        "lockup_until": _iso(until), "in_lockup": locked,
        "payout_on_file": bool(inv.payout_account_number),
    }
    if amount is not None:
        cash = navmod.money(amount)
        out.update({
            "amount": _s(cash),
            "exceeds_holding": cash > value,
            "needs_reason": locked or cash > cap.cap,
        })
    return out


@router.post("/withdrawals", status_code=201)
async def request_withdrawal(body: WithdrawalRequest, inv: Investor = Depends(current_investor),
                             db: AsyncSession = Depends(get_db)):
    """Paid to the payout account on file, never to one typed into this form.

    Taking a destination from the request would make a stolen session enough to
    send the money anywhere. The account on file can only be changed by the
    fund, with a recorded reason.
    """
    if inv.status == "closing":
        raise HTTPException(status_code=400,
                            detail="your account is being closed; the closing payment covers everything")
    if not inv.payout_account_number:
        raise HTTPException(status_code=400,
                            detail="we have no payout account for you yet — contact us to add one")
    try:
        w = await fundmod.request_withdrawal(
            db, investor_id=inv.id, amount=body.amount, justification=body.justification,
            destination={"bank_name": inv.payout_bank_name,
                         "account_number": inv.payout_account_number,
                         "account_name": inv.payout_account_name})
    except (fundmod.FundError, unitsmod.LedgerError) as exc:
        _refuse(exc)
    return {"id": str(w.id), "state": w.state, "amount_requested": _s(w.amount_requested),
            "is_exception": w.is_exception}


# ── leaving ─────────────────────────────────────────────────────────────────

class Closure(BaseModel):
    reason: str | None = Field(default=None, max_length=2000)


@router.post("/closure")
async def request_closure(body: Closure, request: Request, inv: Investor = Depends(current_investor),
                          db: AsyncSession = Depends(get_db)):
    """Ask to close the account. The fund pays out and approves; nothing is
    erased until then, and the request can be withdrawn by contacting the fund."""
    try:
        await fundmod.request_closure(db, investor_id=inv.id, actor_id=inv.id,
                                      actor_kind="investor", reason=body.reason)
    except fundmod.FundError as exc:
        _refuse(exc)
    return {"status": "closing", "quote": await fundmod.closure_quote(db, inv.id)}
