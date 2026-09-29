"""
backend/api/routes/admin_investors.py

Phase 2 — the admin console's Investors section.

Every route here is reads and state transitions over backend/investor/. None of
them writes a ledger row directly and none of them does arithmetic on money:
that all lives in the investor module, where it is tested. A route's job is to
authorise, parse, call one function, and turn a refusal into a readable 400.

Every route is admin-only, enforced by `require_admin` on the router itself so a
route added later cannot forget it.

Money and units go over the wire as STRINGS. A JSON number is a float in every
browser, and a float is how a statement ends up a cent out.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import require_admin
from backend.data.database import get_db
from backend.data.models import User
from backend.investor import disclosure as discmod
from backend.investor import fund as fundmod
from backend.investor import nav as navmod
from backend.investor import reconcile as recmod
from backend.investor import units as unitsmod
from backend.investor.models import (
    DEPOSIT_CONFIRMED,
    WITHDRAWAL_APPROVED,
    WITHDRAWAL_EXCEPTION,
    WITHDRAWAL_PAID,
    WITHDRAWAL_REQUESTED,
    Adjustment,
    Deposit,
    FundSettings,
    Investor,
    InvestorAuditLog,
    NavSnapshot,
    UnitTransaction,
    Withdrawal,
)
from backend.utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/api/admin/investors", tags=["admin-investors"],
                   dependencies=[Depends(require_admin)])

D = Decimal


@contextmanager
def refusals():
    """Turn the investor module's refusals into a 400 the admin can read.

    FundError / LedgerError are business outcomes ("the holding no longer
    covers this"), not crashes; a 500 would make the queue look broken. A
    uniqueness violation is a duplicate (a double click, a reused email) and is
    a 409. The HTTPException propagates out of get_db, which rolls back — so a
    refused action leaves nothing half-written.
    """
    try:
        yield
    except (fundmod.FundError, unitsmod.LedgerError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail="that already exists") from exc


def _s(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return navmod.text(value)
    return str(value)


def _iso(value) -> str | None:
    return value.isoformat() if value is not None else None


# ── request bodies ───────────────────────────────────────────────────────────

class CreateInvestor(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    # A shape check only (email-validator is not a dependency); Phase 4's
    # verification email is what proves the address is real.
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=255)
    phone: str | None = None
    country: str | None = None
    payout_bank_name: str | None = None
    payout_account_number: str | None = None
    payout_account_name: str | None = None


class UpdateInvestor(BaseModel):
    name: str | None = None
    phone: str | None = None
    country: str | None = None
    kyc_status: str | None = None
    payout_bank_name: str | None = None
    payout_account_number: str | None = None
    payout_account_name: str | None = None
    reason: str | None = None


class AdminDeposit(BaseModel):
    amount: Decimal = Field(gt=0)
    on: date | None = None           # historic onboarding: the day they actually paid
    note: str | None = None


class Correction(BaseModel):
    unit_delta: Decimal
    reason: str = Field(min_length=1)
    on: date | None = None


class ConfirmDeposit(BaseModel):
    amount_confirmed: Decimal = Field(gt=0)
    on: date | None = None


class Reason(BaseModel):
    reason: str = Field(min_length=1)


class OptionalReason(BaseModel):
    reason: str | None = None


class ApproveWithdrawal(BaseModel):
    on: date | None = None


class PayWithdrawal(BaseModel):
    reference: str = Field(min_length=1)


class Snapshot(BaseModel):
    pool_equity: Decimal = Field(ge=0)
    liabilities: Decimal = Field(default=D("0"), ge=0)
    on: date | None = None
    reason: str | None = None        # required when it overwrites an existing day


class SettingsChange(BaseModel):
    performance_fee_pct: Decimal | None = Field(default=None, ge=0, le=100)
    management_fee_pct: Decimal | None = Field(default=None, ge=0, le=100)
    withdrawal_cap_pct: Decimal | None = Field(default=None, ge=0, le=100)
    min_investment: Decimal | None = Field(default=None, ge=0)
    lockup_days: int | None = Field(default=None, ge=0)
    notice_days: int | None = Field(default=None, ge=0)
    bank_name: str | None = None
    bank_account_number: str | None = None
    bank_account_name: str | None = None
    bank_instructions: str | None = None


class Publish(BaseModel):
    symbol: str | None = None
    direction: str | None = None
    closed_on: date | None = None
    result_amount: Decimal | None = None
    result_pct: Decimal | None = None
    note: str | None = None
    reason: str | None = None


class CloseAccount(BaseModel):
    payment_reference: str = Field(min_length=1)
    on: date | None = None


# ── overview ─────────────────────────────────────────────────────────────────

async def _count(db, model, *where) -> int:
    return (await db.execute(select(func.count()).select_from(model).where(*where))).scalar_one()


@router.get("/overview")
async def overview(db: AsyncSession = Depends(get_db)):
    """The numbers at the top of the section and the queue badges."""
    price = await fundmod.latest_nav(db)
    outstanding = await unitsmod.units_in_issue(db)
    snap = (await db.execute(
        select(NavSnapshot).order_by(desc(NavSnapshot.as_of_date)).limit(1)
    )).scalar_one_or_none()
    by_status = dict((await db.execute(
        select(Investor.status, func.count(Investor.id)).group_by(Investor.status)
    )).all())
    return {
        "as_of": navmod.accounting_date().isoformat(),
        "nav_per_unit": _s(price),
        "units_in_issue": _s(outstanding),
        "aum": _s(navmod.amount_for_units(outstanding, price)),
        "last_snapshot": _s(snap.as_of_date) if snap else None,
        "investors": by_status,
        "queues": {
            "deposits": await _count(db, Deposit, Deposit.state.in_(fundmod.OPEN_DEPOSIT_STATES)),
            "withdrawals": await _count(db, Withdrawal, Withdrawal.state == WITHDRAWAL_REQUESTED),
            "exceptions": await _count(db, Withdrawal, Withdrawal.state == WITHDRAWAL_EXCEPTION),
            "to_pay": await _count(db, Withdrawal, Withdrawal.state == WITHDRAWAL_APPROVED),
            "closures": by_status.get("closing", 0),
            "disclosures": await discmod.undisclosed_count(db),
        },
    }


# ── investors ────────────────────────────────────────────────────────────────

def _investor_row(inv: Investor) -> dict:
    return {
        "id": inv.id, "name": inv.name, "email": inv.email, "phone": inv.phone,
        "country": inv.country, "status": inv.status, "kyc_status": inv.kyc_status,
        "payout_bank_name": inv.payout_bank_name,
        "payout_account_number": inv.payout_account_number,
        "payout_account_name": inv.payout_account_name,
        "terms_version": inv.terms_version,
        "created_at": _iso(inv.created_at), "activated_at": _iso(inv.activated_at),
        "closed_at": _iso(inv.closed_at),
    }


@router.get("")
async def list_investors(status: str | None = None, db: AsyncSession = Depends(get_db)):
    """Every investor with units, value, capital in, profit and share.

    Three grouped queries rather than a statement per investor, so the list stays
    one round-trip regardless of how many investors there are. The figures use
    the same definitions as reconcile.investor_statement — profit is value plus
    what they have been paid, less what they put in.
    """
    q = select(Investor).order_by(Investor.created_at)
    if status:
        q = q.where(Investor.status == status)
    investors = (await db.execute(q)).scalars().all()

    held = dict((await db.execute(
        select(UnitTransaction.investor_id, func.coalesce(func.sum(UnitTransaction.units), 0))
        .group_by(UnitTransaction.investor_id))).all())
    paid_in = dict((await db.execute(
        select(Deposit.investor_id, func.coalesce(func.sum(Deposit.amount_confirmed), 0))
        .where(Deposit.state == DEPOSIT_CONFIRMED).group_by(Deposit.investor_id))).all())
    taken = dict((await db.execute(
        select(Withdrawal.investor_id, func.coalesce(func.sum(Withdrawal.amount_paid), 0))
        .group_by(Withdrawal.investor_id))).all())

    price = await fundmod.latest_nav(db)
    outstanding = await unitsmod.units_in_issue(db)
    out = []
    for inv in investors:
        units = navmod.units(held.get(inv.id, 0))
        value = navmod.amount_for_units(units, price)
        cap_in = navmod.money(paid_in.get(inv.id, 0))
        out_ = navmod.money(taken.get(inv.id, 0))
        share = units / outstanding * D("100") if outstanding > 0 else D("0")
        out.append({
            **_investor_row(inv),
            "units": _s(units), "current_value": _s(value),
            "capital_in": _s(cap_in), "withdrawn": _s(out_),
            "profit": _s(navmod.money(value + out_ - cap_in)),
            "share_of_pool_pct": _s(navmod.money(share)),
        })
    return {"nav_per_unit": _s(price), "investors": out}


@router.post("", status_code=201)
async def create_investor(body: CreateInvestor, admin: User = Depends(require_admin),
                          db: AsyncSession = Depends(get_db)):
    """Create an investor record. No login — Phase 3 adds credentials."""
    inv = Investor(id=str(uuid.uuid4()), name=body.name.strip(), email=body.email.lower(),
                   phone=body.phone, country=body.country, status="pending",
                   payout_bank_name=body.payout_bank_name,
                   payout_account_number=body.payout_account_number,
                   payout_account_name=body.payout_account_name)
    with refusals():
        exists = (await db.execute(select(Investor.id).where(Investor.email == inv.email))).first()
        if exists:
            raise HTTPException(status_code=409, detail="an investor with that email already exists")
        db.add(inv)
        await db.flush()
        await fundmod.audit(db, actor_id=admin.id, action="investor.created",
                            entity_type="investor", entity_id=inv.id)
    return _investor_row(inv)


async def _get_investor(db, investor_id: str) -> Investor:
    inv = await db.get(Investor, investor_id)
    if inv is None:
        raise HTTPException(status_code=404, detail="investor not found")
    return inv


async def get_investor(investor_id: str, db: AsyncSession = Depends(get_db)):
    """Everything about one investor: statement, ledger, money in and out, and
    every adjustment made to them."""
    inv = await _get_investor(db, investor_id)
    ledger = (await db.execute(
        select(UnitTransaction).where(UnitTransaction.investor_id == investor_id)
        .order_by(UnitTransaction.effective_date, UnitTransaction.created_at,
                  UnitTransaction.id)
    )).scalars().all()
    deposits = (await db.execute(
        select(Deposit).where(Deposit.investor_id == investor_id).order_by(desc(Deposit.created_at), desc(Deposit.id))
    )).scalars().all()
    withdrawals = (await db.execute(
        select(Withdrawal).where(Withdrawal.investor_id == investor_id)
        .order_by(desc(Withdrawal.created_at), desc(Withdrawal.id))
    )).scalars().all()
    adjustments = (await db.execute(
        select(Adjustment).where(Adjustment.entity_type == "investor",
                                 Adjustment.entity_id == investor_id)
        .order_by(desc(Adjustment.created_at), desc(Adjustment.id))
    )).scalars().all()

    return {
        "investor": _investor_row(inv),
        "statement": await recmod.investor_statement(db, investor_id),
        "ledger": [_ledger_row(t) for t in ledger],
        "deposits": [_deposit_row(d) for d in deposits],
        "withdrawals": [_withdrawal_row(w) for w in withdrawals],
        "adjustments": [_adjustment_row(a) for a in adjustments],
    }


@router.patch("/{investor_id}")
async def update_investor(investor_id: str, body: UpdateInvestor,
                          admin: User = Depends(require_admin),
                          db: AsyncSession = Depends(get_db)):
    await _get_investor(db, investor_id)
    changes = body.model_dump(exclude_unset=True)
    reason = changes.pop("reason", None)
    with refusals():
        inv = await fundmod.update_investor(db, investor_id=investor_id, actor_id=admin.id,
                                            changes=changes, reason=reason)
    return _investor_row(inv)


@router.post("/{investor_id}/deposits", status_code=201)
async def record_deposit(investor_id: str, body: AdminDeposit,
                         admin: User = Depends(require_admin),
                         db: AsyncSession = Depends(get_db)):
    """Record money that has already arrived — including historic deposits,
    priced at the NAV of the day they were actually paid."""
    await _get_investor(db, investor_id)
    with refusals():
        dep = await fundmod.record_admin_deposit(db, investor_id=investor_id, amount=body.amount,
                                                 actor_id=admin.id, on=body.on, note=body.note)
    return _deposit_row(dep)


@router.post("/{investor_id}/corrections", status_code=201)
async def correct_units(investor_id: str, body: Correction,
                        admin: User = Depends(require_admin),
                        db: AsyncSession = Depends(get_db)):
    """A compensating ledger row. History is never edited; this is how a
    mistake is fixed, and it is recorded as an adjustment with its reason."""
    await _get_investor(db, investor_id)
    day = body.on or navmod.accounting_date()
    with refusals():
        before = await unitsmod.ledger_units(db, investor_id)
        price = await fundmod.latest_nav(db, day)
        move = await unitsmod.correct(db, investor_id=investor_id, unit_delta=body.unit_delta,
                                      price=price, effective=day, reason=body.reason,
                                      created_by=admin.id)
        db.add(Adjustment(entity_type="investor", entity_id=investor_id, field="units",
                          old_value=_s(before), new_value=_s(before + move.units),
                          reason=body.reason, actor_id=admin.id))
        await fundmod.audit(db, actor_id=admin.id, action="ledger.corrected",
                            entity_type="investor", entity_id=investor_id,
                            detail={"unit_delta": _s(move.units), "nav": _s(price),
                                    "reason": body.reason})
    return {"investor_id": investor_id, "unit_delta": _s(move.units),
            "nav_per_unit": _s(price), "units_after": _s(before + move.units)}


# ── investor login links ─────────────────────────────────────────────────────

class LinkRequest(BaseModel):
    purpose: str = Field(default="invite", pattern="^(invite|reset)$")


@router.post("/{investor_id}/login-link", status_code=201)
async def login_link(investor_id: str, body: LinkRequest, admin: User = Depends(require_admin),
                     db: AsyncSession = Depends(get_db)):
    """A single-use link for the investor to set their password.

    Returned ONCE — only its hash is stored — and any earlier unused link of the
    same kind stops working. Until Phase 4 emails it, the admin sends it by hand.
    """
    import os

    from backend.investor import auth as authmod
    await _get_investor(db, investor_id)
    with refusals():
        raw, expires = await authmod.create_link(db, investor_id=investor_id,
                                                 purpose=body.purpose, actor_id=admin.id)
    base = os.getenv("INVESTOR_APP_URL", "http://localhost:5174").rstrip("/")
    return {"url": f"{base}/accept?token={raw}", "expires_at": _iso(expires),
            "purpose": body.purpose}


# ── closure ──────────────────────────────────────────────────────────────────

@router.get("/{investor_id}/closure")
async def closure_quote(investor_id: str, db: AsyncSession = Depends(get_db)):
    await _get_investor(db, investor_id)
    return await fundmod.closure_quote(db, investor_id)


@router.post("/{investor_id}/closure/request")
async def request_closure(investor_id: str, body: OptionalReason,
                          admin: User = Depends(require_admin),
                          db: AsyncSession = Depends(get_db)):
    await _get_investor(db, investor_id)
    with refusals():
        inv = await fundmod.request_closure(db, investor_id=investor_id, actor_id=admin.id,
                                            reason=body.reason)
    return _investor_row(inv)


@router.post("/{investor_id}/closure/cancel")
async def cancel_closure(investor_id: str, admin: User = Depends(require_admin),
                         db: AsyncSession = Depends(get_db)):
    await _get_investor(db, investor_id)
    with refusals():
        inv = await fundmod.cancel_closure(db, investor_id=investor_id, actor_id=admin.id)
    return _investor_row(inv)


@router.post("/{investor_id}/closure/approve")
async def approve_closure(investor_id: str, body: CloseAccount,
                          admin: User = Depends(require_admin),
                          db: AsyncSession = Depends(get_db)):
    """Irreversible: redeems every unit and erases the person's details."""
    await _get_investor(db, investor_id)
    with refusals():
        return await fundmod.approve_closure(db, investor_id=investor_id, actor_id=admin.id,
                                             payment_reference=body.payment_reference,
                                             on=body.on)


# ── deposit queue ────────────────────────────────────────────────────────────

def _deposit_row(d: Deposit, name: str | None = None) -> dict:
    return {
        "id": str(d.id), "investor_id": d.investor_id, "investor_name": name,
        "method": d.method, "amount_claimed": _s(d.amount_claimed),
        "amount_confirmed": _s(d.amount_confirmed), "currency": d.currency,
        "fx_rate_to_usd": _s(d.fx_rate_to_usd), "reference_code": d.reference_code,
        "has_proof": bool(d.proof_path), "state": d.state,
        "effective_date": _s(d.effective_date), "rejected_reason": d.rejected_reason,
        "confirmed_by": d.confirmed_by, "confirmed_at": _iso(d.confirmed_at),
        "created_at": _iso(d.created_at),
    }


def _states(state: str | None, default: tuple) -> tuple:
    return tuple(s for s in state.split(",") if s) if state else default


@router.get("/queues/deposits")
async def deposit_queue(state: str | None = Query(None, description="comma-separated"),
                        limit: int = Query(200, le=1000),
                        db: AsyncSession = Depends(get_db)):
    states = _states(state, fundmod.OPEN_DEPOSIT_STATES)
    rows = (await db.execute(
        select(Deposit, Investor.name).join(Investor, Investor.id == Deposit.investor_id)
        .where(Deposit.state.in_(states)).order_by(Deposit.created_at).limit(limit)
    )).all()
    return [_deposit_row(d, n) for d, n in rows]


@router.post("/deposits/{deposit_id}/confirm")
async def confirm_deposit(deposit_id: int, body: ConfirmDeposit,
                          admin: User = Depends(require_admin),
                          db: AsyncSession = Depends(get_db)):
    with refusals():
        dep = await fundmod.confirm_deposit(db, deposit_id=deposit_id,
                                            amount_confirmed=body.amount_confirmed,
                                            actor_id=admin.id, on=body.on)
    return _deposit_row(dep)


@router.post("/deposits/{deposit_id}/reject")
async def reject_deposit(deposit_id: int, body: Reason, admin: User = Depends(require_admin),
                         db: AsyncSession = Depends(get_db)):
    with refusals():
        dep = await fundmod.reject_deposit(db, deposit_id=deposit_id, reason=body.reason,
                                           actor_id=admin.id)
    return _deposit_row(dep)


# ── withdrawal queues ────────────────────────────────────────────────────────

def _withdrawal_row(w: Withdrawal, name: str | None = None) -> dict:
    return {
        "id": str(w.id), "investor_id": w.investor_id, "investor_name": name,
        "amount_requested": _s(w.amount_requested), "amount_paid": _s(w.amount_paid),
        "destination_bank_name": w.destination_bank_name,
        "destination_account_number": w.destination_account_number,
        "destination_account_name": w.destination_account_name,
        "state": w.state, "is_exception": w.is_exception, "justification": w.justification,
        "cap_at_request": _s(w.cap_at_request), "declined_reason": w.declined_reason,
        "effective_date": _s(w.effective_date), "approved_by": w.approved_by,
        "approved_at": _iso(w.approved_at), "paid_at": _iso(w.paid_at),
        "payment_reference": w.payment_reference, "created_at": _iso(w.created_at),
    }


@router.get("/queues/withdrawals")
async def withdrawal_queue(state: str | None = Query(None, description="comma-separated"),
                           limit: int = Query(200, le=1000),
                           db: AsyncSession = Depends(get_db)):
    """Each row carries what the holding is worth NOW next to what was asked.

    The NAV can fall between request and approval; showing the current value
    beside the request is what lets the admin see an unaffordable one before
    clicking approve, rather than after.
    """
    states = _states(state, (WITHDRAWAL_REQUESTED, WITHDRAWAL_EXCEPTION, WITHDRAWAL_APPROVED))
    rows = (await db.execute(
        select(Withdrawal, Investor.name).join(Investor, Investor.id == Withdrawal.investor_id)
        .where(Withdrawal.state.in_(states)).order_by(Withdrawal.created_at).limit(limit)
    )).all()
    price = await fundmod.latest_nav(db)
    out = []
    for w, n in rows:
        row = _withdrawal_row(w, n)
        if w.state in (WITHDRAWAL_REQUESTED, WITHDRAWAL_EXCEPTION):
            held = await unitsmod.ledger_units(db, w.investor_id)
            value = navmod.amount_for_units(held, price)
            cap = await fundmod.withdrawal_cap(db, w.investor_id)
            row.update({"holding_value_now": _s(value),
                        "affordable_now": value >= navmod.money(w.amount_requested),
                        "cap_now": _s(cap.cap), "cap_explanation": cap.explanation})
        out.append(row)
    return out


@router.post("/withdrawals/{withdrawal_id}/approve")
async def approve_withdrawal(withdrawal_id: int, body: ApproveWithdrawal,
                             admin: User = Depends(require_admin),
                             db: AsyncSession = Depends(get_db)):
    with refusals():
        w = await fundmod.approve_withdrawal(db, withdrawal_id=withdrawal_id,
                                             actor_id=admin.id, on=body.on)
    return _withdrawal_row(w)


@router.post("/withdrawals/{withdrawal_id}/pay")
async def pay_withdrawal(withdrawal_id: int, body: PayWithdrawal,
                         admin: User = Depends(require_admin),
                         db: AsyncSession = Depends(get_db)):
    with refusals():
        w = await fundmod.mark_withdrawal_paid(db, withdrawal_id=withdrawal_id,
                                               actor_id=admin.id, reference=body.reference)
    return _withdrawal_row(w)


@router.post("/withdrawals/{withdrawal_id}/decline")
async def decline_withdrawal(withdrawal_id: int, body: Reason,
                             admin: User = Depends(require_admin),
                             db: AsyncSession = Depends(get_db)):
    with refusals():
        w = await fundmod.decline_withdrawal(db, withdrawal_id=withdrawal_id,
                                             reason=body.reason, actor_id=admin.id)
    return _withdrawal_row(w)


@router.get("/queues/closures")
async def closure_queue(db: AsyncSession = Depends(get_db)):
    investors = (await db.execute(
        select(Investor).where(Investor.status == "closing").order_by(Investor.created_at)
    )).scalars().all()
    return [{**_investor_row(i), "quote": await fundmod.closure_quote(db, i.id)}
            for i in investors]


# ── NAV ──────────────────────────────────────────────────────────────────────

def _snapshot_row(s: NavSnapshot) -> dict:
    return {"as_of_date": _s(s.as_of_date), "pool_equity": _s(s.pool_equity),
            "liabilities": _s(s.liabilities), "units_in_issue": _s(s.units_in_issue),
            "nav_per_unit": _s(s.nav_per_unit), "source": s.source,
            "created_at": _iso(s.created_at)}


@router.get("/nav/history")
async def nav_history(limit: int = Query(365, le=5000), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(NavSnapshot).order_by(desc(NavSnapshot.as_of_date)).limit(limit)
    )).scalars().all()
    return [_snapshot_row(s) for s in rows]


@router.post("/nav/snapshot", status_code=201)
async def take_snapshot(body: Snapshot, admin: User = Depends(require_admin),
                        db: AsyncSession = Depends(get_db)):
    """Price the fund for a day from an admin-entered pool equity.

    Overwriting a day that already has a price changes what every investor was
    shown for that day, so it needs a reason and is recorded as an adjustment
    with the old NAV. (Ledger rows that already executed keep the price they
    executed at — that is unaffected either way.)
    """
    day = body.on or navmod.accounting_date()
    existing = (await db.execute(
        select(NavSnapshot).where(NavSnapshot.as_of_date == day)
    )).scalar_one_or_none()
    old_nav = _s(existing.nav_per_unit) if existing else None
    if existing and not (body.reason or "").strip():
        raise HTTPException(status_code=400,
                            detail=f"{day} already has a NAV of {old_nav}; "
                                   f"replacing it needs a reason")
    with refusals():
        snap = await fundmod.take_snapshot(db, pool_equity=body.pool_equity,
                                           liabilities=body.liabilities, on=day,
                                           source="MANUAL")
        if existing:
            db.add(Adjustment(entity_type="nav_snapshot", entity_id=day.isoformat(),
                              field="nav_per_unit", old_value=old_nav,
                              new_value=_s(snap.nav_per_unit), reason=body.reason,
                              actor_id=admin.id))
        await fundmod.audit(db, actor_id=admin.id, action="nav.snapshot",
                            entity_type="nav_snapshot", entity_id=day.isoformat(),
                            detail={"pool_equity": _s(body.pool_equity),
                                    "liabilities": _s(body.liabilities),
                                    "nav": _s(snap.nav_per_unit), "replaced": old_nav,
                                    "reason": body.reason})
    return _snapshot_row(snap)


# ── fund terms ───────────────────────────────────────────────────────────────

def _settings_row(s: FundSettings) -> dict:
    return {c.name: _s(getattr(s, c.name)) if c.name not in ("lockup_days", "notice_days",
                                                              "version")
            else getattr(s, c.name)
            for c in FundSettings.__table__.columns if c.name != "id"}


@router.get("/settings/current")
async def get_settings(db: AsyncSession = Depends(get_db)):
    live = await fundmod.current_settings(db)
    history = (await db.execute(
        select(FundSettings).order_by(desc(FundSettings.version)).limit(50)
    )).scalars().all()
    return {"current": _settings_row(live), "history": [_settings_row(s) for s in history]}


@router.post("/settings", status_code=201)
async def change_settings(body: SettingsChange, admin: User = Depends(require_admin),
                          db: AsyncSession = Depends(get_db)):
    """Writes a NEW version. Terms already committed keep the version they
    were made under."""
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=400, detail="nothing to change")
    with refusals():
        row = await fundmod.new_settings_version(db, actor_id=admin.id, **changes)
    return _settings_row(row)


# ── trade disclosure ─────────────────────────────────────────────────────────

@router.get("/disclosures")
async def disclosure_queue(state: str = "undisclosed", limit: int = Query(100, le=500),
                           offset: int = 0, db: AsyncSession = Depends(get_db)):
    with refusals():
        return await discmod.queue(db, state=state, limit=limit, offset=offset)


@router.post("/disclosures/{trade_id}/publish")
async def publish_trade(trade_id: int, body: Publish, admin: User = Depends(require_admin),
                        db: AsyncSession = Depends(get_db)):
    edits = body.model_dump(exclude_unset=True)
    reason = edits.pop("reason", None)
    with refusals():
        row = await discmod.publish(db, trade_id=trade_id, actor_id=admin.id,
                                    edits=edits, reason=reason)
    return discmod.public_view(row)


@router.post("/disclosures/{trade_id}/hide")
async def hide_trade(trade_id: int, body: OptionalReason, admin: User = Depends(require_admin),
                     db: AsyncSession = Depends(get_db)):
    with refusals():
        row = await discmod.hide(db, trade_id=trade_id, actor_id=admin.id, reason=body.reason)
    return {"trade_id": trade_id, "state": row.state}


@router.get("/disclosures/preview")
async def disclosure_preview(db: AsyncSession = Depends(get_db)):
    """Exactly what an investor's trade list will return — the stripped shape."""
    return await discmod.published(db)


# ── reconciliation ───────────────────────────────────────────────────────────

@router.get("/reconciliation")
async def reconciliation(pool_equity: Decimal | None = Query(None, ge=0),
                         db: AsyncSession = Depends(get_db)):
    """Raw vs investor-facing.

    `pool_equity` is the broker's figure. Without it, the latest snapshot's
    equity is used and the response says so — the investor side does not reach
    into MT5 for it (see backend/investor/__init__.py), so a live figure is
    typed in by the admin or supplied by the snapshot job.
    """
    source = "entered"
    if pool_equity is None:
        snap = (await db.execute(
            select(NavSnapshot).order_by(desc(NavSnapshot.as_of_date)).limit(1)
        )).scalar_one_or_none()
        pool_equity = snap.pool_equity if snap else D("0")
        source = f"snapshot {snap.as_of_date.isoformat()}" if snap else "none"
    report = await recmod.build(db, pool_equity=pool_equity)
    return {**report.as_dict(), "pool_equity": _s(navmod.money(pool_equity)),
            "pool_equity_source": source}


# ── audit trail ──────────────────────────────────────────────────────────────

def _ledger_row(t: UnitTransaction) -> dict:
    return {"id": str(t.id), "kind": t.kind, "units": _s(t.units),
            "nav_per_unit": _s(t.nav_per_unit), "amount": _s(t.amount),
            "effective_date": _s(t.effective_date), "source_kind": t.source_kind,
            "source_id": _s(t.source_id), "terms_version": t.terms_version,
            "note": t.note, "created_by": t.created_by, "created_at": _iso(t.created_at)}


def _adjustment_row(a: Adjustment) -> dict:
    return {"id": str(a.id), "entity_type": a.entity_type, "entity_id": a.entity_id,
            "field": a.field, "old_value": a.old_value, "new_value": a.new_value,
            "reason": a.reason, "actor_id": a.actor_id, "created_at": _iso(a.created_at)}


@router.get("/audit/log")
async def audit_log(action: str | None = None, entity_type: str | None = None,
                    entity_id: str | None = None, limit: int = Query(200, le=1000),
                    offset: int = 0, db: AsyncSession = Depends(get_db)):
    q = select(InvestorAuditLog)
    if action:
        q = q.where(InvestorAuditLog.action.like(f"{action}%"))
    if entity_type:
        q = q.where(InvestorAuditLog.entity_type == entity_type)
    if entity_id:
        q = q.where(InvestorAuditLog.entity_id == entity_id)
    rows = (await db.execute(
        q.order_by(desc(InvestorAuditLog.created_at), desc(InvestorAuditLog.id))
        .limit(limit).offset(offset))).scalars().all()
    return [{"id": str(r.id), "actor_id": r.actor_id, "actor_kind": r.actor_kind,
             "action": r.action, "entity_type": r.entity_type, "entity_id": r.entity_id,
             "detail": r.detail, "ip": r.ip, "created_at": _iso(r.created_at)} for r in rows]


@router.get("/audit/adjustments")
async def adjustments(entity_type: str | None = None, entity_id: str | None = None,
                      limit: int = Query(200, le=1000), offset: int = 0,
                      db: AsyncSession = Depends(get_db)):
    q = select(Adjustment)
    if entity_type:
        q = q.where(Adjustment.entity_type == entity_type)
    if entity_id:
        q = q.where(Adjustment.entity_id == entity_id)
    rows = (await db.execute(
        q.order_by(desc(Adjustment.created_at), desc(Adjustment.id))
        .limit(limit).offset(offset))).scalars().all()
    return [_adjustment_row(a) for a in rows]


# Registered LAST on purpose: FastAPI matches in declaration order, and a
# catch-all "/{investor_id}" declared earlier would swallow "/disclosures",
# "/reconciliation" and every other single-segment route above.
router.add_api_route("/{investor_id}", get_investor, methods=["GET"])
