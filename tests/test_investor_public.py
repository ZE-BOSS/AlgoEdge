"""
tests/test_investor_public.py

Phase 5 — what the public website reads and writes.

  * performance: monthly returns, return since inception and maximum drawdown
    from the unit price; labelled gross of fees, and never without drawdown
  * the price per unit really is unchanged by charging a fee (which is WHY the
    series is gross of fees) — pinned rather than asserted in a comment
  * applications: consent required, honeypot, braked, admin notified, and an
    accepted application becomes a pending investor with no money moved
"""

from __future__ import annotations

import asyncio
from datetime import date
from decimal import Decimal

from fastapi import FastAPI
import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from backend.api.routes import public
from backend.data.database import get_db
from backend.data.models import Base
from backend.investor import fees as feesmod
from backend.investor import fund as fundmod
from backend.investor.models import Application, EmailLog, Investor
from backend.notify import outbox
from test_investor_portal import App

D = Decimal


def run(coro):
    return asyncio.run(coro)


def test_performance_months_return_and_drawdown():
    pts = [(date(2026, 6, 1), D("100")), (date(2026, 6, 30), D("110")),
           (date(2026, 7, 15), D("99")), (date(2026, 7, 31), D("104.5")),
           (date(2026, 8, 31), D("115"))]
    p = public.performance_from(pts)
    assert [m["month"] for m in p["months"]] == ["2026-06", "2026-07", "2026-08"]
    assert [m["return_pct"] for m in p["months"]] == ["10.00", "-5.00", "10.05"]
    assert p["return_pct"] == "15.00"
    assert p["max_drawdown_pct"] == "-10.00" and p["max_drawdown_on"] == "2026-07-15"
    assert p["gross_of_fees"] is True and "before fees" in p["note"]


def test_performance_with_too_little_history_says_so():
    assert public.performance_from([])["enough_history"] is False
    assert public.performance_from([(date(2026, 9, 1), D("100"))])["enough_history"] is False


def test_charging_a_fee_leaves_the_price_per_unit_unchanged():
    """The reason the public series is gross of fees, pinned."""
    async def go():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
                                     connect_args={"check_same_thread": False})
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with async_sessionmaker(engine, expire_on_commit=False)() as s:
            s.add(Investor(id="a", email="a@x", name="A"))
            await s.flush()
            await fundmod.record_admin_deposit(s, investor_id="a", amount=1000, actor_id="x",
                                               on=date(2026, 5, 31))
            before = await fundmod.take_snapshot(s, pool_equity=1200, on=date(2026, 6, 30))
            await feesmod.close_period(s, date(2026, 6, 1), date(2026, 6, 30), actor_id="x")
            after = await fundmod.take_snapshot(s, pool_equity=1200, on=date(2026, 7, 1))
            # unchanged, but for the sub-unit residue, which stays with the pool:
            # a cancellation rounds up, so the price can only tick UP by a hair
            assert before.nav_per_unit <= after.nav_per_unit <= before.nav_per_unit + D("0.000001")
        await engine.dispose()
    run(go())


class Site:
    """The public router alone, as the website sees it."""

    async def __aenter__(self):
        public.reset_rate_limit()
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
                                          connect_args={"check_same_thread": False})
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        outbox.use_session_factory(self.Session)

        async def _db():
            async with self.Session() as s:
                try:
                    yield s
                    await s.commit()
                except Exception:
                    await s.rollback()
                    raise
        app = FastAPI()
        app.include_router(public.router)
        app.dependency_overrides[get_db] = _db
        self.http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")
        return self

    async def __aexit__(self, *exc):
        await outbox.drain()
        outbox.use_session_factory(None)
        await self.http.aclose()
        await self.engine.dispose()


FORM = {"name": "Chidi Obi", "email": "chidi@x.com", "country": "Nigeria",
        "amount_band": "$1,000 – $5,000", "message": "Interested", "consent": True}


def test_an_application_needs_consent_is_braked_and_notifies_the_admin(monkeypatch):
    monkeypatch.setenv("EMAIL_MODE", "log")
    monkeypatch.setenv("ADMIN_ALERT_EMAIL", "ops@x.com")

    async def go():
        async with Site() as site:
            assert (await site.http.post("/api/public/apply", json={**FORM, "consent": False})).status_code == 400
            assert (await site.http.post("/api/public/apply", json=FORM)).status_code == 201
            # a bot fills the hidden field: thanked, nothing kept
            assert (await site.http.post("/api/public/apply", json={**FORM, "website": "x"})).status_code == 201
            for _ in range(4):
                await site.http.post("/api/public/apply", json=FORM)
            assert (await site.http.post("/api/public/apply", json=FORM)).status_code == 429
            await outbox.drain()
            async with site.Session() as s:
                apps = (await s.execute(select(Application))).scalars().all()
                mails = (await s.execute(select(EmailLog))).scalars().all()
            assert len(apps) == 5 and all(a.email == "chidi@x.com" for a in apps)
            assert {m.to_address for m in mails} == {"ops@x.com"}
            t = (await site.http.get("/api/public/terms")).json()
            assert t["min_investment"] == "200.00" and t["performance_fee_pct"] == "20"
    run(go())


def test_an_accepted_application_becomes_a_pending_investor(monkeypatch):
    monkeypatch.setenv("EMAIL_MODE", "log")

    async def go():
        async with App() as a:
            async with a.Session() as s:
                s.add(Application(name="Chidi Obi", email="chidi@x.com", country="Nigeria"))
                await s.commit()
            listed = (await a.admin("GET", "/applications/list")).json()
            assert listed[0]["status"] == "new"
            assert (await a.admin("GET", "/overview")).json()["queues"]["applications"] == 1
            r = await a.admin("POST", f"/applications/{listed[0]['id']}/accept")
            assert r.status_code == 201
            inv = (await a.admin("GET", f"/{r.json()['investor_id']}")).json()
            assert inv["investor"]["status"] == "pending" and inv["ledger"] == []
            assert (await a.admin("POST", f"/applications/{listed[0]['id']}/accept")).status_code == 400
    run(go())
