"""
backend/api/routes/public.py

What the public website may read and write. No authentication, so nothing here
touches an individual's data, and the one write — an application — is braked.

PERFORMANCE IS THE UNIT PRICE, AND IS GROSS OF FEES. Fees are charged by
cancelling each investor's units, which leaves the price per unit unchanged —
so the NAV series is the fund's return BEFORE fees, and an investor's own
return is lower by their fees. The response says so, and the website prints it
next to the numbers. Return is never shown without the drawdown beside it.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from datetime import date, datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.data.database import get_db
from backend.investor import fund as fundmod
from backend.investor import nav as navmod
from backend.investor.models import Application, NavSnapshot
from backend.notify import outbox
from backend.notify import templates as mail

router = APIRouter(prefix="/api/public", tags=["public"])
D = Decimal


def performance_from(points: list[tuple]) -> dict:
    """Monthly returns, return since inception and maximum drawdown from
    (date, nav) pairs, oldest first. Pure, so it is tested directly."""
    if len(points) < 2:
        return {"enough_history": False, "months": [], "since": points[0][0].isoformat() if points else None}
    first_d, first_nav = points[0]
    last_d, last_nav = points[-1]

    # month-end price = last snapshot in each month; a month's return is from the
    # previous month-end (or the first snapshot, for the first month)
    month_end: dict[tuple, tuple] = {}
    for d, n in points:
        month_end[(d.year, d.month)] = (d, n)
    months, prev = [], first_nav
    for (y, m), (d, n) in sorted(month_end.items()):
        if (y, m) == (first_d.year, first_d.month) and d == first_d:
            continue
        months.append({"month": f"{y}-{m:02d}", "return_pct": _pct(n / prev - 1), "partial": d == last_d
                       and (last_d.month, last_d.year) == (m, y) and not _is_month_end(d)})
        prev = n

    peak, worst, worst_at = first_nav, D("0"), None
    for d, n in points:
        peak = max(peak, n)
        dd = n / peak - 1
        if dd < worst:
            worst, worst_at = dd, d
    days = (last_d - first_d).days
    return {
        "enough_history": True,
        "since": first_d.isoformat(), "as_of": last_d.isoformat(), "days": days,
        "return_pct": _pct(last_nav / first_nav - 1),
        "max_drawdown_pct": _pct(worst),
        "max_drawdown_on": worst_at.isoformat() if worst_at else None,
        "months": months,
        "basis": "live",
        "gross_of_fees": True,
        "note": ("Percentage change in the value of money invested at launch, from live trading, "
                 "before fees. An investor's own return is lower by the fees they pay. "
                 "Past performance does not guarantee future results."),
    }


def _pct(x: Decimal) -> str:
    return navmod.text((x * 100).quantize(D("0.01")))


def _is_month_end(d) -> bool:
    from datetime import timedelta
    return (d + timedelta(days=1)).day == 1


@router.get("/performance")
async def performance(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(NavSnapshot.as_of_date, NavSnapshot.nav_per_unit)
                             .order_by(NavSnapshot.as_of_date))).all()
    series = [(d, D(str(n))) for d, n in rows if n and D(str(n)) > 0]
    out = performance_from(series)
    # a sparse series for a chart; the price, not the fund's size
    out["series"] = [{"date": d.isoformat(), "nav": navmod.text(n)} for d, n in series[-400:]]
    return out


@router.get("/terms")
async def terms(db: AsyncSession = Depends(get_db)):
    s = await fundmod.current_settings(db)
    return {
        "min_investment": navmod.text(navmod.money(s.min_investment)),
        "currency": s.base_currency,
        "management_fee_pct": navmod.text(D(str(s.management_fee_pct)).normalize()),
        "performance_fee_pct": navmod.text(D(str(s.performance_fee_pct)).normalize()),
        "withdrawal_cap_pct": navmod.text(D(str(s.withdrawal_cap_pct)).normalize()),
        "lockup_days": s.lockup_days, "notice_days": s.notice_days,
    }


# ── applications ────────────────────────────────────────────────────────────

class Apply(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=255)
    phone: str | None = Field(default=None, max_length=40)
    country: str | None = Field(default=None, max_length=80)
    amount_band: str | None = Field(default=None, max_length=40)
    message: str | None = Field(default=None, max_length=2000)
    consent: bool
    # Either is accepted; a date of birth is preferred because it can be
    # verified later against an identity document, while a tickbox cannot.
    date_of_birth: date | None = None
    confirms_18: bool | None = None
    website: str | None = None           # honeypot: people never see this field; bots fill it


# ── the age gate ─────────────────────────────────────────────────────────────

MINIMUM_AGE = 18


def _age_on(dob: date, today: date) -> int:
    """Whole years, counting the birthday itself as reaching that age."""
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


def _check_age(body: "Apply") -> int | None:
    """Reject anyone under `MINIMUM_AGE`, or anyone who declines to say.

    terms.html S2 promises "You must be at least 18". Returning the age (or
    None for a tickbox-only answer) rather than a bool so the caller can store
    what was actually established.

    Raises 400 rather than 422 so the website can show the message as written.
    """
    today = datetime.now(timezone.utc).date()
    if body.date_of_birth is not None:
        if body.date_of_birth > today:
            raise HTTPException(status_code=400, detail="that date of birth is in the future")
        age = _age_on(body.date_of_birth, today)
        if age > 120:
            raise HTTPException(status_code=400, detail="please check your date of birth")
        if age < MINIMUM_AGE:
            raise HTTPException(
                status_code=400,
                detail=f"you must be at least {MINIMUM_AGE} to open an account")
        return age
    if body.confirms_18:
        return None
    raise HTTPException(
        status_code=400,
        detail=f"please confirm you are at least {MINIMUM_AGE}")


_WINDOW, _LIMIT = 60 * 60, 5
_seen: dict[str, deque] = defaultdict(deque)


def _braked(key: str) -> bool:
    q = _seen[key]
    cutoff = time.monotonic() - _WINDOW
    while q and q[0] < cutoff:
        q.popleft()
    if len(q) >= _LIMIT:
        return True
    q.append(time.monotonic())
    return False


def reset_rate_limit() -> None:
    _seen.clear()


@router.post("/apply", status_code=201)
async def apply(body: Apply, request: Request, db: AsyncSession = Depends(get_db)):
    if not body.consent:
        raise HTTPException(status_code=400, detail="please confirm you have read the risk notice")
    ip = request.client.host if request.client else "?"
    thanks = {"ok": True, "message": "Thank you. We will be in touch within two working days."}
    if body.website:                      # a bot: say thanks, keep nothing
        return thanks
    # The age gate is enforced HERE, not in the form. A checkbox in the browser
    # is a suggestion; a direct POST skips it entirely, and "we had a checkbox"
    # is not a record of anything.
    #
    # AFTER the honeypot on purpose: a bot that trips it should learn nothing,
    # and a validation error tells it which field to fix next time.
    _check_age(body)
    if _braked(f"ip:{ip}") or _braked(f"email:{body.email.lower()}"):
        raise HTTPException(status_code=429, detail="too many applications, please try again later")
    row = Application(name=body.name.strip(), email=body.email.strip().lower(), phone=body.phone,
                      country=body.country, amount_band=body.amount_band, message=body.message, ip=ip,
                      date_of_birth=body.date_of_birth,
                      age_confirmed_at=datetime.now(timezone.utc))
    db.add(row)
    await db.flush()
    outbox.queue(db, mail.admin_application(row))
    return thanks


# ── the legal identity, for the published policies ──────────────────────────

@router.get("/legal")
async def legal_identity(db: AsyncSession = Depends(get_db)):
    """What privacy.html and terms.html fill themselves in with.

    The policies are static files served by the website, and the entity name,
    registered address and governing jurisdiction live in `company_profile`
    where an admin can change them. Rather than regenerate the HTML on every
    edit, the pages carry `data-legal` placeholders and fill them from here.

    Everything returned is already public — it is printed on the policies
    themselves — so this needs no authentication. Fields that are not set come
    back empty, and the page leaves its own wording in place rather than
    showing a blank, so a policy never degrades into half a sentence.
    """
    from backend.notify import company as companymod

    row = await companymod.current_row(db)
    if row is None:
        return {"has_profile": False, "legal_entity_name": "", "trading_name": "",
                "postal_address": "", "jurisdiction": "", "registration_number": "",
                "support_email": "", "privacy_email": "", "policy_version": "",
                "policy_updated_at": None}
    return {
        "has_profile": True,
        "legal_entity_name": row.legal_entity_name or "",
        "trading_name": row.trading_name or "",
        "postal_address": row.postal_address,
        "jurisdiction": row.jurisdiction or "",
        "registration_number": row.registration_number or "",
        "support_email": row.support_email or "",
        "privacy_email": row.privacy_email or row.support_email or "",
        "policy_version": row.policy_version or "",
        "policy_updated_at": (row.policy_updated_at.isoformat()
                              if row.policy_updated_at else None),
    }


# ── unsubscribe ─────────────────────────────────────────────────────────────
#
# Two methods on one path, and both are required:
#
#   POST  RFC 8058 one-click. Gmail and Yahoo issue this themselves, with no
#         cookies and no session, when the reader presses their own unsubscribe
#         button. It must act and return 200 without asking anything.
#   GET   a person clicking the link in the email. Acts and says so in words,
#         because a blank page is indistinguishable from a failure.
#
# Deliberately NOT authenticated. The token is a signature over
# (investor_id, setting) and can do nothing else — see
# backend/investor/unsubscribe.py for why it is not the investor's session
# token. Braked per token so the endpoint cannot be used to probe for valid
# ones at speed.

_UNSUB_DONE = ("You will no longer receive these emails. Your statements and "
               "anything about your money still reach you, and you can turn "
               "individual emails back on in Settings.")


async def _unsubscribe(token: str, db: AsyncSession) -> dict:
    from backend.investor import unsubscribe as unsubmod
    from backend.investor.models import Investor

    if _braked(f"unsub:{(token or '')[:24]}"):
        raise HTTPException(status_code=429, detail="too many attempts, please try again later")
    try:
        investor_id, setting = unsubmod.read_token(token)
    except unsubmod.BadToken as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    investor = await db.get(Investor, investor_id)
    # Same answer whether or not the id exists: the endpoint is public, and
    # "that is not a real investor" is something a prober would like to learn.
    if investor is not None:
        investor.preferences = unsubmod.apply_to(investor.preferences, setting)
        await fundmod.audit(db, actor_id=investor_id, actor_kind="investor",
                            action=f"email.unsubscribed.{setting}",
                            entity_type="investor", entity_id=investor_id)
    return {"ok": True, "setting": setting, "message": _UNSUB_DONE}


@router.post("/unsubscribe")
async def unsubscribe_one_click(t: str = Query(..., max_length=512),
                                db: AsyncSession = Depends(get_db)):
    """RFC 8058 one-click, called by the reader's mail provider."""
    return await _unsubscribe(t, db)


@router.get("/unsubscribe")
async def unsubscribe_link(t: str = Query(..., max_length=512),
                           db: AsyncSession = Depends(get_db)):
    """The same thing, for a person who clicked the link themselves."""
    return await _unsubscribe(t, db)


# ── the Android app ─────────────────────────────────────────────────────────

@router.get("/app")
async def current_app(request: Request, db: AsyncSession = Depends(get_db)):
    """The build the website's download button and the app's update check use."""
    from backend.investor.models import AppRelease
    row = (await db.execute(select(AppRelease).where(AppRelease.platform == "android",
                                                     AppRelease.is_current.is_(True)))).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="no release published yet")
    base = str(request.base_url).rstrip("/")
    return {"platform": "android", "version": row.version_name, "version_code": row.version_code,
            "size_bytes": row.size_bytes, "sha256": row.sha256, "notes": row.notes,
            "released": row.created_at.isoformat() if row.created_at else None,
            "url": f"{base}/api/public/app/download/{row.id}"}


@router.get("/app/download/{release_id}")
async def download_app(release_id: int, db: AsyncSession = Depends(get_db)):
    from backend.api.routes.admin_investors import release_dir
    from backend.investor.models import AppRelease
    row = await db.get(AppRelease, release_id)
    path = release_dir() / row.filename if row else None
    if row is None or not path.exists():
        raise HTTPException(status_code=404, detail="no such release")
    return FileResponse(path, media_type="application/vnd.android.package-archive",
                        filename=row.filename, headers={"X-Content-SHA256": row.sha256})
