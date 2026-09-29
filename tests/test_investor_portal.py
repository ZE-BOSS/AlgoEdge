"""
tests/test_investor_portal.py

Phase 3 — the investor's own API, over HTTP, with the REAL admin auth
dependency in the same app. Only get_db is replaced.

Pinned here:
  * an investor token is refused by admin routes and an admin token by investor
    routes — by signature, not by a role check someone could forget
  * invite links are single-use, expire, and a newer link retires older ones
  * guessing is braked; changing the password ends every other session
  * an investor sees only their own position and never the fund's size
  * a deposit claim credits nothing; a withdrawal pays only the account on file
  * the agreed one-month lock-up is enforced, under the terms committed to
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import date, timedelta
from decimal import Decimal

import httpx
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from backend.api.routes import admin_investors, investor_portal
from backend.api.routes.auth import create_token
from backend.data.database import get_db
from backend.notify import outbox
from backend.data.models import Base, Trade, User
from backend.investor import auth as authmod
from backend.investor import nav as navmod
from backend.investor.models import InvestorToken, UnitTransaction

D = Decimal
ADMIN_BASE = "/api/admin/investors"
INV = "/api/investor"
PASSWORD = "correct horse battery"


def run(coro):
    return asyncio.run(coro)


class App:
    async def __aenter__(self):
        authmod.reset_rate_limit()
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
        app.include_router(admin_investors.router)
        app.include_router(investor_portal.router)
        app.dependency_overrides[get_db] = _db
        self.http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                      base_url="http://test")
        async with self.Session() as s:
            admin = User(id=str(uuid.uuid4()), email="admin@x.com", name="Admin",
                         password_hash="x", is_admin=True, is_active=True)
            s.add(admin)
            await s.commit()
        self.admin_token = create_token(admin.id, "access")
        return self

    async def __aexit__(self, *exc):
        await outbox.drain()
        outbox.use_session_factory(None)
        await self.http.aclose()
        await self.engine.dispose()

    # admin side
    async def admin(self, method, path, json=None, **kw):
        return await self.http.request(method, ADMIN_BASE + path, json=json,
                                       headers={"Authorization": f"Bearer {self.admin_token}"}, **kw)

    async def new_investor(self, name="Ada", deposit=None, on=None, payout=True):
        r = await self.admin("POST", "", {"name": name, "email": f"{name.lower()}@x.com"})
        iid = r.json()["id"]
        if payout:
            await self.admin("PATCH", f"/{iid}", {"payout_bank_name": "GTBank",
                                                  "payout_account_number": "0123456789",
                                                  "payout_account_name": name, "reason": "onboarding"})
        if deposit:
            body = {"amount": str(deposit)}
            if on:
                body["on"] = on.isoformat()
            assert (await self.admin("POST", f"/{iid}/deposits", body)).status_code == 201
        return iid

    async def link(self, iid, purpose="invite"):
        r = await self.admin("POST", f"/{iid}/login-link", {"purpose": purpose})
        assert r.status_code == 201, r.text
        return r.json()["url"].split("token=")[1]

    async def onboard(self, name="Ada", **kw):
        iid = await self.new_investor(name, **kw)
        token = await self.link(iid)
        r = await self.http.post(f"{INV}/auth/accept", json={"token": token, "password": PASSWORD})
        assert r.status_code == 200, r.text
        return iid, r.json()

    # investor side
    async def inv(self, method, path, session, json=None, **kw):
        return await self.http.request(method, INV + path, json=json,
                                       headers={"Authorization": f"Bearer {session['access_token']}"},
                                       **kw)


# ── the audiences are separate ───────────────────────────────────────────────

def test_an_investor_token_is_refused_by_admin_routes_and_vice_versa():
    async def go():
        async with App() as a:
            _, sess = await a.onboard()
            r = await a.http.get(ADMIN_BASE + "/overview",
                                 headers={"Authorization": f"Bearer {sess['access_token']}"})
            assert r.status_code == 401
            r = await a.http.get(INV + "/me", headers={"Authorization": f"Bearer {a.admin_token}"})
            assert r.status_code == 401
            # a refresh token is not an access token
            r = await a.http.get(INV + "/me",
                                 headers={"Authorization": f"Bearer {sess['refresh_token']}"})
            assert r.status_code == 401
            assert (await a.http.get(INV + "/me")).status_code == 401
    run(go())


# ── invitation links ─────────────────────────────────────────────────────────

def test_an_invite_link_works_once_and_a_newer_one_retires_it():
    async def go():
        async with App() as a:
            iid = await a.new_investor()
            first = await a.link(iid)
            second = await a.link(iid)
            r = await a.http.post(f"{INV}/auth/accept", json={"token": first, "password": PASSWORD})
            assert r.status_code == 400 and "expired or was already used" in r.json()["detail"]
            r = await a.http.post(f"{INV}/auth/accept", json={"token": second, "password": "short"})
            assert r.status_code == 400 and "at least 10" in r.json()["detail"]
            r = await a.http.post(f"{INV}/auth/accept", json={"token": second, "password": PASSWORD})
            assert r.status_code == 200
            again = await a.http.post(f"{INV}/auth/accept", json={"token": second, "password": PASSWORD})
            assert again.status_code == 400
            # only the hash is stored
            async with a.Session() as s:
                hashes = (await s.execute(select(InvestorToken.token_hash))).scalars().all()
            assert second not in hashes and first not in hashes
    run(go())


def test_an_expired_invite_is_refused():
    async def go():
        async with App() as a:
            iid = await a.new_investor()
            token = await a.link(iid)
            async with a.Session() as s:
                row = (await s.execute(select(InvestorToken))).scalars().first()
                row.expires_at = row.expires_at - timedelta(days=10)
                await s.commit()
            r = await a.http.post(f"{INV}/auth/accept", json={"token": token, "password": PASSWORD})
            assert r.status_code == 400
    run(go())


# ── signing in ───────────────────────────────────────────────────────────────

def test_login_is_braked_after_five_wrong_passwords():
    async def go():
        async with App() as a:
            await a.onboard()
            ok = await a.http.post(f"{INV}/auth/login", json={"email": "ADA@x.com ", "password": PASSWORD})
            assert ok.status_code == 200
            for _ in range(5):
                r = await a.http.post(f"{INV}/auth/login", json={"email": "ada@x.com", "password": "nope-nope-nope"})
                assert r.status_code == 401
            r = await a.http.post(f"{INV}/auth/login", json={"email": "ada@x.com", "password": PASSWORD})
            assert r.status_code == 401 and "too many attempts" in r.json()["detail"]
            # an unknown address gets the same answer as a wrong password
            r = await a.http.post(f"{INV}/auth/login", json={"email": "who@x.com", "password": PASSWORD})
            assert r.json()["detail"] == "email or password is not right"
    run(go())


def test_changing_the_password_ends_every_other_session():
    async def go():
        async with App() as a:
            _, old = await a.onboard()
            r = await a.inv("POST", "/auth/password", old,
                            {"current_password": "wrong password!", "new_password": "another good one"})
            assert r.status_code == 400
            r = await a.inv("POST", "/auth/password", old,
                            {"current_password": PASSWORD, "new_password": "another good one"})
            assert r.status_code == 200
            new = r.json()
            assert (await a.inv("GET", "/me", old)).status_code == 401
            refreshed = await a.http.post(f"{INV}/auth/refresh", json={"refresh_token": old["refresh_token"]})
            assert refreshed.status_code == 401
            assert (await a.inv("GET", "/me", new)).status_code == 200
    run(go())


# ── what an investor can see ─────────────────────────────────────────────────

def test_an_investor_sees_their_own_position_and_not_the_funds_size():
    async def go():
        async with App() as a:
            _, ada = await a.onboard("Ada", deposit=D("1000"))
            await a.new_investor("Bea", deposit=D("3000"))
            await a.admin("POST", "/nav/snapshot", {"pool_equity": "4400"})
            me = (await a.inv("GET", "/me", ada)).json()
            assert me["investor"]["name"] == "Ada"
            assert me["statement"]["current_value"] == "1100.00"
            assert me["statement"]["share_of_pool_pct"] == "25.00"
            nav = (await a.inv("GET", "/nav", ada)).json()
            assert nav[-1]["nav_per_unit"] == "110.00000000"
            assert nav[-1]["value"] == "1100.00"          # their holding, in dollars
            for body in (me, nav, (await a.inv("GET", "/activity", ada)).json()):
                text = str(body)
                assert "pool_equity" not in text and "4400" not in text and "Bea" not in text
    run(go())


def test_activity_shows_every_movement_but_not_operator_notes():
    async def go():
        async with App() as a:
            iid, ada = await a.onboard("Ada", deposit=D("1000"))
            await a.admin("POST", f"/{iid}/corrections", {"unit_delta": "-1", "reason": "SECRET-NOTE"})
            act = (await a.inv("GET", "/activity", ada)).json()
            assert [row["label"] for row in act["ledger"]] == ["Correction", "Units bought"]
            assert "SECRET-NOTE" not in str(act) and "created_by" not in str(act)
    run(go())


def test_the_trade_list_is_published_trades_only_and_stripped():
    async def go():
        async with App() as a:
            _, ada = await a.onboard()
            async with a.Session() as s:
                from datetime import datetime
                for i in range(2):
                    s.add(Trade(id=100 + i, user_id="op", strategy_id="SECRET_v9", symbol="XAUUSD",
                                direction="BUY", status="CLOSED", pnl=10.0 + i,
                                exit_time=datetime(2026, 9, 20 + i, 12)))
                await s.commit()
            await a.admin("POST", "/disclosures/100/publish", {})
            await a.admin("POST", "/disclosures/101/hide", {})
            rows = (await a.inv("GET", "/trades", ada)).json()
            assert [r["id"] for r in rows] != [] and len(rows) == 1
            assert "SECRET" not in str(rows) and "strategy" not in str(rows)
    run(go())


# ── money in ────────────────────────────────────────────────────────────────

def test_a_deposit_claim_credits_nothing_and_lands_in_the_admin_queue():
    async def go():
        async with App() as a:
            iid, ada = await a.onboard()
            await a.admin("POST", "/settings", {"bank_name": "Zenith", "bank_account_number": "1010101010",
                                                "bank_account_name": "Alphavantiq Capital"})
            info = (await a.inv("GET", "/deposits/instructions", ada)).json()
            assert info["configured"] and info["reference_code"].startswith("AVQ-")

            assert (await a.inv("POST", "/deposits", ada, {"amount": "50"})).status_code == 400
            r = await a.inv("POST", "/deposits", ada, {"amount": "1000"})
            assert r.status_code == 201 and r.json()["reference_code"] == info["reference_code"]
            async with a.Session() as s:
                assert (await s.execute(select(func.count(UnitTransaction.id)))).scalar_one() == 0
            queue = (await a.admin("GET", "/queues/deposits")).json()
            assert queue[0]["investor_id"] == iid and queue[0]["amount_claimed"] == "1000.00"

            for _ in range(2):
                await a.inv("POST", "/deposits", ada, {"amount": "500"})
            fourth = await a.inv("POST", "/deposits", ada, {"amount": "500"})
            assert fourth.status_code == 400 and "waiting to be confirmed" in fourth.json()["detail"]
    run(go())


# ── money out ───────────────────────────────────────────────────────────────

def _month_start(day: date) -> date:
    return day.replace(day=1)


async def _funded_long_ago_with_profit(a, **kw):
    """Funded 90 days ago (past the lock-up), then +10% since the month began."""
    today = navmod.accounting_date()
    iid, sess = await a.onboard(deposit=D("1000"), on=today - timedelta(days=90), **kw)
    await a.admin("POST", "/nav/snapshot", {"pool_equity": "1000",
                                            "on": (_month_start(today) - timedelta(days=1)).isoformat()})
    await a.admin("POST", "/nav/snapshot", {"pool_equity": "1100", "on": today.isoformat()})
    return iid, sess


def test_a_withdrawal_pays_only_the_account_on_file():
    async def go():
        async with App() as a:
            iid, ada = await _funded_long_ago_with_profit(a)
            q = (await a.inv("GET", "/withdrawals/quote", ada, params={"amount": "20"})).json()
            assert q["standard_limit"] == "30.00" and q["needs_reason"] is False and not q["in_lockup"]
            r = await a.inv("POST", "/withdrawals", ada,
                            {"amount": "20", "destination_account_number": "9999999999"})
            assert r.status_code == 201 and r.json()["state"] == "requested"
            w = (await a.admin("GET", "/queues/withdrawals")).json()[0]
            assert w["destination_account_number"] == "0123456789"
    run(go())


def test_no_payout_account_means_no_withdrawal():
    async def go():
        async with App() as a:
            _, ada = await _funded_long_ago_with_profit(a, payout=False)
            r = await a.inv("POST", "/withdrawals", ada, {"amount": "20"})
            assert r.status_code == 400 and "no payout account" in r.json()["detail"]
    run(go())


def test_inside_the_lockup_a_withdrawal_needs_a_reason_and_is_an_exception():
    async def go():
        async with App() as a:
            today = navmod.accounting_date()
            _, ada = await a.onboard(deposit=D("1000"), on=today - timedelta(days=5))
            q = (await a.inv("GET", "/withdrawals/quote", ada, params={"amount": "10"})).json()
            assert q["in_lockup"] is True and q["needs_reason"] is True
            assert q["lockup_until"] == (today + timedelta(days=25)).isoformat()
            r = await a.inv("POST", "/withdrawals", ada, {"amount": "10"})
            assert r.status_code == 400 and "lock-up period" in r.json()["detail"]
            r = await a.inv("POST", "/withdrawals", ada, {"amount": "10", "justification": "medical"})
            assert r.status_code == 201 and r.json()["state"] == "exception_pending"
    run(go())


def test_a_later_longer_lockup_does_not_extend_money_already_committed():
    async def go():
        async with App() as a:
            today = navmod.accounting_date()
            iid, ada = await a.onboard(deposit=D("1000"), on=today - timedelta(days=40))
            await a.admin("POST", "/settings", {"lockup_days": 90})
            me = (await a.inv("GET", "/me", ada)).json()
            assert me["terms"]["lockup_days"] == 30 and me["terms"]["version"] == 1
            q = (await a.inv("GET", "/withdrawals/quote", ada)).json()
            assert q["in_lockup"] is False
    run(go())


# ── leaving ─────────────────────────────────────────────────────────────────

def test_requesting_closure_stops_new_withdrawals_and_reaches_the_admin():
    async def go():
        async with App() as a:
            iid, ada = await _funded_long_ago_with_profit(a)
            r = await a.inv("POST", "/closure", ada, {"reason": "moving abroad"})
            quote = r.json()["quote"]
            assert r.status_code == 200 and quote["gross_value"] == "1100.00"
            # this month's fees come off, and last month's must be charged first
            assert D(quote["fees_owed"]) > 0 and D(quote["net_payable"]) < D("1100")
            assert any("have not been charged" in b for b in quote["blockers"])
            last = navmod.accounting_date().replace(day=1) - timedelta(days=1)
            closed = await a.admin("POST", "/fees/close", {"year": last.year, "month": last.month})
            assert closed.status_code == 201, closed.text
            assert (await a.inv("POST", "/closure", ada, {})).status_code == 400
            assert (await a.inv("POST", "/withdrawals", ada, {"amount": "10"})).status_code == 400
            assert len((await a.admin("GET", "/queues/closures")).json()) == 1

            await a.admin("POST", f"/{iid}/closure/approve", {"payment_reference": "T1"})
            assert (await a.inv("GET", "/me", ada)).status_code == 401
            r = await a.http.post(f"{INV}/auth/login", json={"email": "ada@x.com", "password": PASSWORD})
            assert r.status_code == 401
    run(go())


def test_the_lockup_applies_even_when_the_amount_is_within_the_cap():
    """The other lock-up tests have no profit, so the cap alone forces a reason.
    Here the amount is inside the cap and only the lock-up stands in the way."""
    async def go():
        async with App() as a:
            await a.admin("POST", "/settings", {"lockup_days": 120})   # committed under v2
            iid, ada = await _funded_long_ago_with_profit(a)          # funded 90 days ago
            q = (await a.inv("GET", "/withdrawals/quote", ada, params={"amount": "20"})).json()
            assert q["standard_limit"] == "30.00", q
            assert q["in_lockup"] is True and q["needs_reason"] is True
            r = await a.inv("POST", "/withdrawals", ada, {"amount": "20"})
            assert r.status_code == 400 and "lock-up period" in r.json()["detail"]
    run(go())


def test_terms_read_as_people_write_percentages():
    async def go():
        async with App() as a:
            _, ada = await a.onboard(deposit=D("1000"))
            terms = (await a.inv("GET", "/me", ada)).json()["terms"]
            assert (terms["performance_fee_pct"], terms["management_fee_pct"],
                    terms["withdrawal_cap_pct"]) == ("20", "2", "30")
    run(go())
