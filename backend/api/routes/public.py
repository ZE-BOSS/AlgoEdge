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
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request
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
    website: str | None = None           # honeypot: people never see this field; bots fill it


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
    if _braked(f"ip:{ip}") or _braked(f"email:{body.email.lower()}"):
        raise HTTPException(status_code=429, detail="too many applications, please try again later")
    row = Application(name=body.name.strip(), email=body.email.strip().lower(), phone=body.phone,
                      country=body.country, amount_band=body.amount_band, message=body.message, ip=ip)
    db.add(row)
    await db.flush()
    outbox.queue(db, mail.admin_application(row))
    return thanks


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
