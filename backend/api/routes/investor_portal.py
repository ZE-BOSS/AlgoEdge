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

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
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
from backend.notify import outbox
from backend.notify import templates as mail
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


def _pct(v):
    """A NUMERIC(9,4) percentage as a person writes it: 20 -> "20", 2.5 -> "2.5"."""
    return None if v is None else navmod.text(Decimal(str(v)).normalize())


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


class Forgot(BaseModel):
    email: str = Field(max_length=255)


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


@router.post("/auth/forgot")
async def forgot_password(body: Forgot, request: Request, db: AsyncSession = Depends(get_db)):
    """Email a reset link. Always answers the same way, so the form cannot be
    used to find out who is a client; braked per address so it cannot be used
    to flood someone's inbox."""
    import os
    try:
        found = await authmod.request_reset(db, email=body.email, ip=_ip(request))
    except authmod.AuthError:
        found = None                       # rate-limited: same answer, nothing sent
    if found:
        inv, raw = found
        base = os.getenv("INVESTOR_APP_URL", "http://localhost:5174").rstrip("/")
        outbox.queue(db, mail.reset(inv, f"{base}/accept?token={raw}"))
    return {"ok": True, "message": "If that address has an account, a reset link is on its way. "
                                   "It works once, for 30 minutes."}


class Signup(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=255)
    phone: str | None = Field(default=None, max_length=40)
    country: str | None = Field(default=None, max_length=80)
    consent: bool
    website: str | None = None           # honeypot: people never see this field; bots fill it


@router.post("/auth/signup", status_code=201)
async def signup(body: Signup, request: Request, db: AsyncSession = Depends(get_db)):
    """Open an account from the app's sign-in page.

    Nothing is usable until the person proves the address is theirs: the
    account is created without a password and a single-use link to choose one
    is emailed. The answer is the same whether or not the address already has
    an account, so the form cannot be used to find out who is a client; an
    existing open account is simply sent a fresh reset link instead.
    """
    import os
    import uuid

    from backend.api.routes.public import _braked
    from backend.investor.models import Application

    if not body.consent:
        raise HTTPException(status_code=400, detail="please confirm you have read the risk notice")
    done = {"ok": True, "message": "Check your email: we have sent a link to confirm your address "
                                   "and choose a password."}
    if body.website:
        return done
    email, ip = body.email.strip().lower(), _ip(request) or "?"
    if _braked(f"signup-ip:{ip}") or _braked(f"signup:{email}"):
        raise HTTPException(status_code=429, detail="too many attempts — please try again later")
    base = os.getenv("INVESTOR_APP_URL", "http://localhost:5174").rstrip("/")

    existing = (await db.execute(select(Investor).where(Investor.email == email))).scalar_one_or_none()
    if existing is not None:
        if existing.status != "closed":
            purpose = "reset" if existing.password_hash else "invite"
            raw, expires = await authmod.create_link(db, investor_id=existing.id, purpose=purpose,
                                                     actor_id=None)
            url = f"{base}/accept?token={raw}"
            outbox.queue(db, mail.reset(existing, url) if existing.password_hash
                         else mail.signup(existing, url, expires))
        return done

    inv = Investor(id=str(uuid.uuid4()), name=body.name.strip(), email=email,
                   phone=body.phone, country=body.country, status="pending")
    db.add(inv)
    app_row = Application(name=inv.name, email=email, phone=body.phone, country=body.country,
                          message="Signed up in the investor app.", status="accepted",
                          investor_id=inv.id, ip=ip)
    db.add(app_row)
    await db.flush()
    await fundmod.audit(db, actor_id=None, actor_kind="investor", action="investor.signed_up",
                        entity_type="investor", entity_id=inv.id)
    raw, expires = await authmod.create_link(db, investor_id=inv.id, purpose="invite", actor_id=None)
    outbox.queue(db, mail.signup(inv, f"{base}/accept?token={raw}", expires))
    outbox.queue(db, mail.admin_application(app_row))
    return done


@router.post("/auth/password")
async def change_password(body: ChangePassword, inv: Investor = Depends(current_investor),
                          db: AsyncSession = Depends(get_db)):
    """Returns fresh tokens: the old ones stop working the moment this commits."""
    try:
        await authmod.change_password(db, investor=inv, current=body.current_password,
                                      new=body.new_password)
    except authmod.AuthError as exc:
        _refuse(exc)
    outbox.queue(db, mail.password_changed(inv))
    return _session(inv)


# ── my position ──────────────────────────────────────────────────────────────

@router.get("/me")
async def me(inv: Investor = Depends(current_investor), db: AsyncSession = Depends(get_db)):
    from backend.investor import prefs as prefsmod
    from backend.investor import split as splitmod
    statement = await recmod.investor_statement(db, inv.id)
    terms = await fundmod.terms_for(db, inv.id)
    live = await fundmod.current_settings(db)
    until = await fundmod.lockup_until(db, inv.id)
    return {
        # capital against profit: is money being added on top, or eating in?
        "capital": await splitmod.capital_position(db, inv.id, Decimal(statement["current_value"])),
        "preferences": prefsmod.load(inv.preferences),
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
            "performance_fee_pct": _pct(terms.performance_fee_pct),
            "management_fee_pct": _pct(terms.management_fee_pct),
            "withdrawal_cap_pct": _pct(terms.withdrawal_cap_pct),
            "lockup_days": terms.lockup_days,
            "notice_days": terms.notice_days,
            "min_investment": _s(navmod.money(terms.min_investment)),
            # how the management fee works: a share of each two-month period's profit
            "management_fee_basis": "period_profit",
            "fee_period_months": 2,
            "base_currency": live.base_currency,
        },
    }


@router.get("/nav")
async def nav_history(limit: int = Query(730, le=5000), db: AsyncSession = Depends(get_db),
                      inv: Investor = Depends(current_investor)):
    """The fund's price over time, and what THIS investor's holding was worth
    in dollars on each of those days (null before they held anything).

    `value` is what the app charts: people read dollars, not unit prices. The
    unit price is still returned, because the fund's own growth ("$100 at
    launch is now worth…") is the price itself — the fund opens at 100.
    Never the size of the fund.
    """
    rows = list(reversed((await db.execute(
        select(NavSnapshot.as_of_date, NavSnapshot.nav_per_unit)
        .order_by(desc(NavSnapshot.as_of_date)).limit(limit))).all()))
    moves = (await db.execute(
        select(UnitTransaction.effective_date, UnitTransaction.units, UnitTransaction.kind,
               UnitTransaction.amount)
        .where(UnitTransaction.investor_id == inv.id)
        .order_by(UnitTransaction.effective_date, UnitTransaction.id))).all()
    out, held, net, i = [], Decimal("0"), Decimal("0"), 0
    for d, n in rows:
        while i < len(moves) and moves[i][0] <= d:
            held += Decimal(str(moves[i][1]))
            if moves[i][2] == "SUBSCRIBE":
                net += Decimal(str(moves[i][3]))
            elif moves[i][2] == "REDEEM":
                net -= Decimal(str(moves[i][3]))
            i += 1
        value = navmod.amount_for_units(held, n) if held > 0 else None
        out.append({"date": d.isoformat(), "nav_per_unit": _s(n), "value": _s(value),
                    # money paid in less money paid out, to that day: the line the
                    # balance is above (profit) or below (capital being lost)
                    "net_invested": _s(navmod.money(net)) if value is not None else None})
    return out


@router.get("/performance")
async def performance(inv: Investor = Depends(current_investor), db: AsyncSession = Depends(get_db)):
    """This investor's own month-by-month result and the numbers behind it.

    A month's profit = value at its end - value at the previous month's end -
    money paid in during it + money paid out during it. The percentage is the
    fund's own change that month (the same for every investor's money, before
    fees). Only months they were invested in.
    """
    rows = (await db.execute(select(NavSnapshot.as_of_date, NavSnapshot.nav_per_unit)
                             .order_by(NavSnapshot.as_of_date))).all()
    moves = (await db.execute(
        select(UnitTransaction.effective_date, UnitTransaction.units, UnitTransaction.kind,
               UnitTransaction.amount)
        .where(UnitTransaction.investor_id == inv.id)
        .order_by(UnitTransaction.effective_date, UnitTransaction.id))).all()
    if not moves:
        return {"months": [], "stats": None}
    first = moves[0][0]
    # month-end value, flows and price for every month from the first deposit
    month_end: dict[str, dict] = {}
    held, i = Decimal("0"), 0
    flows: dict[str, Decimal] = {}
    for d, u, kind, amount in moves:
        key = d.strftime("%Y-%m")
        if kind == "SUBSCRIBE":
            flows[key] = flows.get(key, Decimal("0")) + Decimal(str(amount))
        elif kind == "REDEEM":
            flows[key] = flows.get(key, Decimal("0")) - Decimal(str(amount))
    peak_nav, worst_dd, before_nav = None, Decimal("0"), None
    for d, n in rows:
        while i < len(moves) and moves[i][0] <= d:
            held += Decimal(str(moves[i][1]))
            i += 1
        if d < first:
            before_nav = Decimal(str(n))        # the price they bought in near
            continue
        n = Decimal(str(n))
        peak_nav = n if peak_nav is None else max(peak_nav, n)
        worst_dd = min(worst_dd, n / peak_nav - 1)
        month_end[d.strftime("%Y-%m")] = {"value": navmod.amount_for_units(held, n), "nav": n}
    months, prev_value = [], Decimal("0")
    prev_nav = before_nav or navmod.INITIAL_NAV
    for key in sorted(set(month_end) | set(flows)):
        end = month_end.get(key)
        if end is None:
            continue
        flow = flows.get(key, Decimal("0"))
        profit = navmod.money(end["value"] - prev_value - flow)
        ret = (end["nav"] / prev_nav - 1) * 100 if prev_nav else None
        months.append({"month": key, "start_value": _s(navmod.money(prev_value)),
                       "money_in_out": _s(navmod.money(flow)), "end_value": _s(navmod.money(end["value"])),
                       "profit": _s(profit),
                       "fund_return_pct": None if ret is None else _s(ret.quantize(Decimal("0.01")))})
        prev_value, prev_nav = end["value"], end["nav"]
    stats = None
    if months:
        best = max(months, key=lambda m: Decimal(m["profit"]))
        worst = min(months, key=lambda m: Decimal(m["profit"]))
        stats = {"days_invested": (navmod.accounting_date() - first).days,
                 "first_deposit": first.isoformat(),
                 "best_month": best, "worst_month": worst,
                 "months_up": sum(1 for m in months if Decimal(m["profit"]) > 0),
                 "months_down": sum(1 for m in months if Decimal(m["profit"]) < 0),
                 "largest_fall_pct": _s((worst_dd * 100).quantize(Decimal("0.01")))}
    return {"months": months, "stats": stats}


class Preferences(BaseModel):
    hide_balances: bool | None = None
    chart_range: str | None = None
    compact_numbers: bool | None = None
    email: dict | None = None
    push: dict | None = None


@router.get("/preferences")
async def get_preferences(inv: Investor = Depends(current_investor)):
    from backend.investor import prefs as prefsmod
    return prefsmod.load(inv.preferences)


@router.put("/preferences")
async def set_preferences(body: Preferences, inv: Investor = Depends(current_investor),
                          db: AsyncSession = Depends(get_db)):
    from backend.investor import prefs as prefsmod
    try:
        inv.preferences = prefsmod.merge(inv.preferences, body.model_dump(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await db.flush()
    return prefsmod.load(inv.preferences)


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
    """Published trades, each with THIS investor's share of its result.

    The fund-wide dollar result is not returned: an investor sees their own
    money, never the size of the book. See investor/split.py.
    """
    from backend.investor import split as splitmod
    return await splitmod.my_trades(db, inv.id, limit=limit + offset)


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
        # a recent change of payout account sends every request to review
        "cooling_off": await fundmod.payout_changed_recently(db, inv.id),
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
    review = None
    if await fundmod.payout_changed_recently(db, inv.id):
        review = (f"automatic review: payout account changed in the last "
                  f"{fundmod.PAYOUT_COOLING_HOURS} hours")
    try:
        w = await fundmod.request_withdrawal(
            db, investor_id=inv.id, amount=body.amount, justification=body.justification,
            destination={"bank_name": inv.payout_bank_name,
                         "account_number": inv.payout_account_number,
                         "account_name": inv.payout_account_name},
            force_review=review)
    except (fundmod.FundError, unitsmod.LedgerError) as exc:
        _refuse(exc)
    terms = await fundmod.terms_for(db, inv.id)
    outbox.queue(db, mail.withdrawal_received(inv, w, terms.notice_days))
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
    quote = await fundmod.closure_quote(db, inv.id)
    outbox.queue(db, mail.closure_requested(inv, quote))
    return {"status": "closing", "quote": quote}


# ── statements ──────────────────────────────────────────────────────────────

@router.get("/statements")
async def statements_list(inv: Investor = Depends(current_investor), db: AsyncSession = Depends(get_db)):
    """Every complete month the investor held units in, newest first."""
    from sqlalchemy import func as sfunc
    first = (await db.execute(select(sfunc.min(UnitTransaction.effective_date))
                              .where(UnitTransaction.investor_id == inv.id))).scalar_one()
    if first is None:
        return []
    today = navmod.accounting_date()
    out, y, m = [], first.year, first.month
    while (y, m) < (today.year, today.month):
        out.append({"year": y, "month": m, "label": f"{y}-{m:02d}"})
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return list(reversed(out))


@router.get("/statements/{year}-{month}.pdf")
async def statement_pdf(year: int, month: int, inv: Investor = Depends(current_investor),
                        db: AsyncSession = Depends(get_db)):
    from backend.investor import fees as feesmod
    from backend.investor import statements as stmtmod
    if not (2020 <= year <= 2100 and 1 <= month <= 12):
        raise HTTPException(status_code=404, detail="no such month")
    start, end = feesmod.month_period(year, month)
    if end >= navmod.accounting_date():
        raise HTTPException(status_code=400, detail="a month's statement is ready once the month has ended")
    _, pdf = await stmtmod.pdf_for(db, inv.id, start, end)
    return Response(pdf, media_type="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="alphavantiq-statement-{year}-{month:02d}.pdf"',
        "Cache-Control": "private, no-store"})


# ── phones (push notifications) ─────────────────────────────────────────────

class Device(BaseModel):
    push_token: str = Field(min_length=10, max_length=200, pattern=r"^ExponentPushToken\[[^\]]+\]$")
    platform: str | None = Field(default=None, max_length=16)
    app_version: str | None = Field(default=None, max_length=32)


@router.post("/devices", status_code=201)
async def register_device(body: Device, inv: Investor = Depends(current_investor),
                          db: AsyncSession = Depends(get_db)):
    """Remember this phone for notifications. A token moves with the phone: if
    someone else signs in on it, it is theirs now, not the previous person's."""
    from datetime import datetime, timezone

    from backend.investor.models import InvestorDevice
    row = (await db.execute(select(InvestorDevice)
                            .where(InvestorDevice.push_token == body.push_token))).scalar_one_or_none()
    if row is None:
        row = InvestorDevice(push_token=body.push_token, investor_id=inv.id)
        db.add(row)
    row.investor_id, row.platform, row.app_version = inv.id, body.platform, body.app_version
    row.last_seen = datetime.now(timezone.utc)
    return {"ok": True}


@router.delete("/devices")
async def forget_device(push_token: str = Query(..., max_length=200),
                        inv: Investor = Depends(current_investor), db: AsyncSession = Depends(get_db)):
    """On sign-out: stop notifying this phone."""
    from sqlalchemy import delete

    from backend.investor.models import InvestorDevice
    await db.execute(delete(InvestorDevice).where(InvestorDevice.push_token == push_token,
                                                  InvestorDevice.investor_id == inv.id))
    return {"ok": True}
