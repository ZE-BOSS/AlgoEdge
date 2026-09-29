"""
tests/test_investor_admin_routes.py

Phase 2 — the admin console's Investors routes, executed over HTTP against an
in-memory database. Not source-text checks: every request goes through FastAPI,
the real dependency chain and the real commit/rollback of get_db.

What is pinned here is what the ROUTES add on top of the Phase 1 module:
  * admin-only at the dependency, on every route
  * a refused action returns a readable 400 AND leaves nothing half-written
  * the catch-all /{investor_id} does not swallow the static routes
  * money crosses the wire as strings
  * disclosure strips strategy identity; edits need reasons and are recorded
  * closure pays out, zeroes the holding, and erases the person but not history
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace

import httpx
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from backend.api.deps import get_current_user
from backend.api.routes import admin_investors
from backend.data.database import get_db
from backend.data.models import Base, Trade
from backend.investor import disclosure as discmod
from backend.investor import fund as fundmod
from backend.investor.models import (
    Adjustment,
    Investor,
    InvestorAuditLog,
    UnitTransaction,
    Withdrawal,
)

D = Decimal
BASE = "/api/admin/investors"
ADMIN = SimpleNamespace(id="admin-1", is_admin=True, is_active=True)


def run(coro):
    return asyncio.run(coro)


class Harness:
    """A FastAPI app with only this router, an in-memory database, and a
    get_db that commits on success and rolls back on an exception — the same
    contract as backend.data.database.get_db."""

    def __init__(self, user=ADMIN):
        self.user = user

    async def __aenter__(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
                                          connect_args={"check_same_thread": False})
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)

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
        app.dependency_overrides[get_db] = _db
        app.dependency_overrides[get_current_user] = lambda: self.user
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                        base_url="http://test")
        return self

    async def __aexit__(self, *exc):
        await self.client.aclose()
        await self.engine.dispose()

    async def get(self, path, **kw):
        return await self.client.get(BASE + path, **kw)

    async def post(self, path, json=None):
        return await self.client.post(BASE + path, json=json or {})

    async def patch(self, path, json):
        return await self.client.patch(BASE + path, json=json)

    async def investor(self, name="Ada", email=None, deposit=None, on=None):
        r = await self.post("", {"name": name, "email": email or f"{name.lower()}@x.com"})
        assert r.status_code == 201, r.text
        iid = r.json()["id"]
        if deposit is not None:
            body = {"amount": str(deposit)}
            if on:
                body["on"] = on.isoformat()
            r = await self.post(f"/{iid}/deposits", body)
            assert r.status_code == 201, r.text
        return iid

    async def count(self, model, *where):
        async with self.Session() as s:
            return (await s.execute(select(func.count()).select_from(model).where(*where))).scalar_one()


# ── authorisation and routing ────────────────────────────────────────────────

def test_every_route_refuses_a_non_admin():
    async def go():
        async with Harness(SimpleNamespace(id="u", is_admin=False, is_active=True)) as h:
            for path in ("/overview", "", "/reconciliation", "/disclosures",
                         "/queues/deposits", "/audit/log", "/some-id"):
                assert (await h.get(path)).status_code == 403, path
            assert (await h.post("", {"name": "X", "email": "x@x.com"})).status_code == 403
            assert await h.count(Investor) == 0
    run(go())


def test_static_routes_are_not_swallowed_by_the_investor_catch_all():
    async def go():
        async with Harness() as h:
            for path in ("/overview", "/reconciliation", "/disclosures"):
                r = await h.get(path)
                assert r.status_code == 200, (path, r.text)
            assert (await h.get("/no-such-investor")).status_code == 404
    run(go())


# ── investors and deposits ───────────────────────────────────────────────────

def test_create_list_and_money_as_strings():
    async def go():
        async with Harness() as h:
            iid = await h.investor("Ada", deposit=D("1000"))
            body = (await h.get("")).json()
            row = body["investors"][0]
            assert row["id"] == iid and row["status"] == "active"
            for f in ("units", "current_value", "capital_in", "profit", "share_of_pool_pct"):
                assert isinstance(row[f], str), f
            assert row["capital_in"] == "1000.00" and row["current_value"] == "1000.00"
            assert row["share_of_pool_pct"] == "100.00"

            dup = await h.post("", {"name": "Other", "email": "ADA@x.com"})
            assert dup.status_code == 409
    run(go())


def test_historic_deposit_is_priced_at_the_nav_of_its_own_day():
    async def go():
        async with Harness() as h:
            a = await h.investor("Ada", deposit=D("1000"), on=date(2026, 1, 5))
            # the pool doubles on 1 Feb
            r = await h.post("/nav/snapshot", {"pool_equity": "2000", "on": "2026-02-01"})
            assert r.status_code == 201, r.text
            # a deposit dated BEFORE the doubling buys at the old price
            b = await h.investor("Bea", deposit=D("1000"), on=date(2026, 1, 20))
            a_units = D((await h.get(f"/{a}")).json()["statement"]["units"])
            b_units = D((await h.get(f"/{b}")).json()["statement"]["units"])
            assert a_units == b_units
    run(go())


def test_a_refused_deposit_writes_nothing():
    async def go():
        async with Harness() as h:
            iid = await h.investor("Ada")
            r = await h.post(f"/{iid}/deposits", {"amount": "50"})   # below the $200 minimum
            assert r.status_code == 400 and "minimum" in r.json()["detail"]
            assert await h.count(UnitTransaction) == 0
            assert (await h.get(f"/{iid}")).json()["deposits"] == []
    run(go())


def test_deposit_queue_confirms_the_admins_figure_not_the_claim():
    async def go():
        async with Harness() as h:
            iid = await h.investor("Ada")
            async with h.Session() as s:
                from backend.investor.models import Deposit
                dep = Deposit(investor_id=iid, amount_claimed=D("1000"), state="claimed_sent")
                s.add(dep)
                await s.commit()
                dep_id = dep.id
            queue = (await h.get("/queues/deposits")).json()
            assert [q["id"] for q in queue] == [str(dep_id)]
            assert queue[0]["investor_name"] == "Ada"

            r = await h.post(f"/deposits/{dep_id}/confirm", {"amount_confirmed": "950"})
            assert r.status_code == 200 and r.json()["amount_confirmed"] == "950.00"
            assert (await h.get(f"/{iid}")).json()["statement"]["capital_in"] == "950.00"
            assert (await h.get("/queues/deposits")).json() == []

            again = await h.post(f"/deposits/{dep_id}/confirm", {"amount_confirmed": "950"})
            assert again.status_code == 400
            assert await h.count(UnitTransaction) == 1
    run(go())


def test_rejecting_needs_a_reason():
    async def go():
        async with Harness() as h:
            iid = await h.investor("Ada")
            async with h.Session() as s:
                from backend.investor.models import Deposit
                dep = Deposit(investor_id=iid, amount_claimed=D("500"), state="claimed_sent")
                s.add(dep)
                await s.commit()
            assert (await h.post(f"/deposits/{dep.id}/reject", {"reason": ""})).status_code == 422
            r = await h.post(f"/deposits/{dep.id}/reject", {"reason": "never arrived"})
            assert r.json()["state"] == "rejected"
    run(go())


# ── withdrawals ──────────────────────────────────────────────────────────────

async def _withdrawal(h, iid, amount, justification=None):
    async with h.Session() as s:
        w = await fundmod.request_withdrawal(s, investor_id=iid, amount=amount,
                                             justification=justification)
        await s.commit()
        return w.id


def test_withdrawal_queue_approve_then_pay():
    async def go():
        async with Harness() as h:
            iid = await h.investor("Ada", deposit=D("1000"))
            wid = await _withdrawal(h, iid, D("100"), justification="school fees")
            q = (await h.get("/queues/withdrawals")).json()
            assert q[0]["state"] == "exception_pending"       # no profit yet -> exception
            assert q[0]["affordable_now"] is True
            assert "no profit this month" in q[0]["cap_explanation"]

            assert (await h.post(f"/withdrawals/{wid}/pay", {"reference": "T1"})).status_code == 400
            assert (await h.post(f"/withdrawals/{wid}/approve")).status_code == 200
            assert (await h.post(f"/withdrawals/{wid}/approve")).status_code == 400
            r = await h.post(f"/withdrawals/{wid}/pay", {"reference": "T1"})
            assert r.json()["state"] == "paid" and r.json()["amount_paid"] == "100.00"
            st = (await h.get(f"/{iid}")).json()["statement"]
            assert st["withdrawn"] == "100.00" and st["current_value"] == "900.00"
    run(go())


def test_an_unaffordable_approval_is_a_400_and_changes_nothing():
    async def go():
        async with Harness() as h:
            iid = await h.investor("Ada", deposit=D("1000"))
            wid = await _withdrawal(h, iid, D("900"), justification="house")
            # the pool halves before the admin gets to it
            await h.post("/nav/snapshot", {"pool_equity": "500"})
            q = (await h.get("/queues/withdrawals")).json()
            assert q[0]["affordable_now"] is False
            r = await h.post(f"/withdrawals/{wid}/approve")
            assert r.status_code == 400 and "no longer covers" in r.json()["detail"]
            async with h.Session() as s:
                assert (await s.get(Withdrawal, wid)).state == "exception_pending"
            assert await h.count(UnitTransaction) == 1
    run(go())


# ── NAV, settings, corrections, payout changes ──────────────────────────────

def test_overwriting_a_days_nav_needs_a_reason_and_is_recorded():
    async def go():
        async with Harness() as h:
            await h.investor("Ada", deposit=D("1000"))
            assert (await h.post("/nav/snapshot", {"pool_equity": "1100"})).status_code == 201
            again = await h.post("/nav/snapshot", {"pool_equity": "1200"})
            assert again.status_code == 400 and "needs a reason" in again.json()["detail"]
            ok = await h.post("/nav/snapshot", {"pool_equity": "1200", "reason": "late fill"})
            assert ok.status_code == 201
            adj = (await h.get("/audit/adjustments", params={"entity_type": "nav_snapshot"})).json()
            assert len(adj) == 1 and adj[0]["reason"] == "late fill"
            assert D(adj[0]["old_value"]) == D("110") and D(adj[0]["new_value"]) == D("120")
            assert len((await h.get("/nav/history")).json()) == 1
    run(go())


def test_settings_change_writes_a_new_version():
    async def go():
        async with Harness() as h:
            v1 = (await h.get("/settings/current")).json()["current"]
            assert v1["version"] == 1 and D(v1["withdrawal_cap_pct"]) == 30
            r = await h.post("/settings", {"withdrawal_cap_pct": "25", "notice_days": 14})
            assert r.status_code == 201 and r.json()["version"] == 2
            body = (await h.get("/settings/current")).json()
            assert D(body["current"]["withdrawal_cap_pct"]) == 25
            assert [v["version"] for v in body["history"]] == [2, 1]
            assert D(body["history"][1]["withdrawal_cap_pct"]) == 30   # v1 untouched
            assert (await h.post("/settings", {})).status_code == 400
            assert (await h.post("/settings", {"withdrawal_cap_pct": "150"})).status_code == 422
    run(go())


def test_a_correction_is_a_compensating_row_with_an_adjustment():
    async def go():
        async with Harness() as h:
            iid = await h.investor("Ada", deposit=D("1000"))
            assert (await h.post(f"/{iid}/corrections",
                                 {"unit_delta": "0", "reason": "x"})).status_code == 400
            r = await h.post(f"/{iid}/corrections", {"unit_delta": "-1", "reason": "fat finger"})
            assert r.status_code == 201, r.text
            detail = (await h.get(f"/{iid}")).json()
            assert [t["kind"] for t in detail["ledger"]] == ["SUBSCRIBE", "CORRECTION"]
            assert D(detail["statement"]["units"]) == D("9")
            assert detail["adjustments"][0]["field"] == "units"
            assert detail["adjustments"][0]["reason"] == "fat finger"
    run(go())


def test_changing_payout_details_needs_a_reason_and_keeps_the_old_account():
    async def go():
        async with Harness() as h:
            iid = await h.investor("Ada")
            await h.patch(f"/{iid}", {"payout_account_number": "111"
                                      , "reason": "initial details"})
            refused = await h.patch(f"/{iid}", {"payout_account_number": "999"})
            assert refused.status_code == 400
            assert (await h.get(f"/{iid}")).json()["investor"]["payout_account_number"] == "111"

            ok = await h.patch(f"/{iid}", {"payout_account_number": "999",
                                           "reason": "investor called from registered phone"})
            assert ok.status_code == 200
            adj = (await h.get(f"/{iid}")).json()["adjustments"]
            assert adj[0]["old_value"] == "111" and adj[0]["new_value"] == "999"

            # a name change needs no reason
            assert (await h.patch(f"/{iid}", {"name": "Ada L."})).status_code == 200
            async with h.Session() as s:
                logs = (await s.execute(select(InvestorAuditLog).where(
                    InvestorAuditLog.action == "investor.updated"))).scalars().all()
            # account numbers live in the adjustment trail, not the audit log
            assert all("999" not in str(lg.detail) and "111" not in str(lg.detail) for lg in logs)
    run(go())


# ── disclosure ───────────────────────────────────────────────────────────────

async def _trade(h, status="CLOSED", pnl=42.5):
    async with h.Session() as s:
        t = Trade(user_id="op", strategy_id="SECRET_EDGE_v3", symbol="XAUUSD.m",
                  direction="BUY", status=status, pnl=pnl, balance_before=10000.0,
                  entry_time=datetime(2026, 9, 28, 9), exit_time=datetime(2026, 9, 28, 11))
        s.add(t)
        await s.commit()
        return t.id


def test_disclosure_publishes_without_strategy_identity():
    async def go():
        async with Harness() as h:
            tid = await _trade(h)
            q = (await h.get("/disclosures")).json()
            assert q[0]["trade_id"] == tid and q[0]["raw"]["strategy_id"] == "SECRET_EDGE_v3"
            assert (await h.get("/overview")).json()["queues"]["disclosures"] == 1

            pub = await h.post(f"/disclosures/{tid}/publish", {})
            assert pub.status_code == 200
            assert set(pub.json()) == set(discmod.PUBLIC_FIELDS)
            assert pub.json()["result_amount"] == "42.50"
            preview = (await h.get("/disclosures/preview")).json()
            assert "SECRET" not in str(preview) and "strategy" not in str(preview)
            assert (await h.get("/disclosures")).json() == []
    run(go())


def test_publishing_edited_needs_a_reason_and_is_an_adjustment():
    async def go():
        async with Harness() as h:
            tid = await _trade(h)
            r = await h.post(f"/disclosures/{tid}/publish", {"symbol": "XAUUSD"})
            assert r.status_code == 400
            assert await h.count(Adjustment) == 0
            # a note is commentary, not a figure
            assert (await h.post(f"/disclosures/{tid}/publish",
                                 {"note": "gold, London open"})).status_code == 200
            r = await h.post(f"/disclosures/{tid}/publish",
                             {"symbol": "XAUUSD", "result_amount": "40",
                              "reason": "broker suffix; net of hedge"})
            assert r.status_code == 200 and r.json()["result_amount"] == "40.00"
            adj = (await h.get("/audit/adjustments",
                               params={"entity_type": "trade_disclosure"})).json()
            assert {a["field"] for a in adj} == {"symbol", "result_amount"}
            assert {a["old_value"] for a in adj} == {"XAUUSD.m", "42.50"}
            listed = (await h.get("/disclosures", params={"state": "published"})).json()
            assert listed[0]["edited"] is True and listed[0]["published"]["note"] == "gold, London open"
    run(go())


def test_hiding_a_published_trade_needs_a_reason():
    async def go():
        async with Harness() as h:
            tid = await _trade(h)
            open_tid = await _trade(h, status="OPEN")
            assert (await h.post(f"/disclosures/{open_tid}/publish", {})).status_code == 400
            await h.post(f"/disclosures/{tid}/publish", {})
            assert (await h.post(f"/disclosures/{tid}/hide", {})).status_code == 400
            r = await h.post(f"/disclosures/{tid}/hide", {"reason": "published in error"})
            assert r.json()["state"] == "hidden"
            assert (await h.get("/disclosures/preview")).json() == []
    run(go())


# ── closure ──────────────────────────────────────────────────────────────────

def test_closure_pays_out_zeroes_units_and_erases_the_person_not_the_history():
    async def go():
        async with Harness() as h:
            iid = await h.investor("Ada", deposit=D("1000"))
            other = await h.investor("Bea", deposit=D("1000"))
            await h.patch(f"/{iid}", {"payout_account_number": "12345", "phone": "+234",
                                      "reason": "setup"})
            await h.post("/nav/snapshot", {"pool_equity": "2200"})   # +10%

            assert (await h.post(f"/{iid}/closure/approve",
                                 {"payment_reference": "X"})).status_code == 400  # not requested
            assert (await h.post(f"/{iid}/closure/request", {})).status_code == 200
            assert len((await h.get("/queues/closures")).json()) == 1

            wid = await _withdrawal(h, iid, D("50"), justification="x")
            quote = (await h.get(f"/{iid}/closure")).json()
            # $1,100 less this month's fees: management 1100 x 2% x 1/365 = 0.06,
            # performance 20% x (100.00 - 0.06) = 19.99
            assert quote["fees_owed"] == "20.05" and quote["net_payable"] == "1079.95"
            assert quote["blockers"]
            blocked = await h.post(f"/{iid}/closure/approve", {"payment_reference": "TX9"})
            assert blocked.status_code == 400 and "decide them first" in blocked.json()["detail"]

            await h.post(f"/withdrawals/{wid}/decline", {"reason": "closing instead"})
            r = await h.post(f"/{iid}/closure/approve", {"payment_reference": "TX9"})
            assert r.status_code == 200, r.text
            assert r.json()["amount_paid"] == "1079.95"

            detail = (await h.get(f"/{iid}")).json()
            inv = detail["investor"]
            assert inv["status"] == "closed" and inv["name"] == "Closed investor"
            assert inv["email"].endswith("@anonymised.invalid")
            assert inv["phone"] is None and inv["payout_account_number"] is None
            assert D(detail["statement"]["units"]) == 0
            # history survives: subscribe + redeem still in the ledger
            assert [t["kind"] for t in detail["ledger"]] == ["SUBSCRIBE", "FEE", "FEE", "REDEEM"]
            paid = [w for w in detail["withdrawals"] if w["state"] == "paid"]
            assert paid[0]["payment_reference"] == "TX9"
            assert all(w["destination_account_number"] is None for w in detail["withdrawals"])

            # a closed account accepts nothing further
            assert (await h.post(f"/{iid}/deposits", {"amount": "500"})).status_code == 400
            assert (await h.patch(f"/{iid}", {"name": "Back"})).status_code == 400

            # the remaining investor is untouched and the books still balance
            assert (await h.get(f"/{other}")).json()["statement"]["current_value"] == "1100.00"
            # the broker still holds Ada's $20.05 of fees until the manager takes
            # them out: 2200 - 1079.95 paid = 1120.05, of which 20.05 is owed
            rec = (await h.get("/reconciliation", params={"pool_equity": "1120.05"})).json()
            assert rec["healthy"] is True, rec
    run(go())


def test_reconciliation_uses_the_latest_snapshot_when_no_equity_is_given():
    async def go():
        async with Harness() as h:
            await h.investor("Ada", deposit=D("1000"))
            none = (await h.get("/reconciliation")).json()
            assert none["pool_equity_source"] == "none"
            await h.post("/nav/snapshot", {"pool_equity": "1000"})
            rec = (await h.get("/reconciliation")).json()
            assert rec["pool_equity_source"].startswith("snapshot ")
            assert rec["healthy"] is True
            gap = (await h.get("/reconciliation", params={"pool_equity": "900"})).json()
            assert gap["healthy"] is False
    run(go())


def test_overview_counts_the_queues():
    async def go():
        async with Harness() as h:
            iid = await h.investor("Ada", deposit=D("1000"))
            await _withdrawal(h, iid, D("10"), justification="x")
            await h.post(f"/{iid}/closure/request", {})
            ov = (await h.get("/overview")).json()
            assert ov["queues"]["exceptions"] == 1 and ov["queues"]["closures"] == 1
            assert ov["aum"] == "1000.00" and ov["investors"] == {"closing": 1}
    run(go())


def test_the_cap_explanation_reads_30_percent_not_30_point_0000():
    """The cap percentage comes back from a NUMERIC(9,4) column as 30.0000, and
    the explanation the investor reads used to print it that way."""
    async def go():
        async with Harness() as h:
            iid = await h.investor("Ada", deposit=D("1000"), on=date(2026, 1, 5))
            # the setting must be read back from the database, not the in-memory default
            await h.post("/settings", {"notice_days": 8})
            await h.post("/nav/snapshot", {"pool_equity": "1100"})
            text = (await h.get(f"/{iid}")).json()["statement"]["withdrawable_explanation"]
            assert text.startswith("30% of this month"), text
    run(go())


def test_a_zero_balance_is_0_not_scientific_notation():
    """Decimal('0E-8') — zero quantized to 8dp — prints as "0E-8" through str().
    A fully redeemed investor showed "0E-8" units on screen."""
    async def go():
        async with Harness() as h:
            iid = await h.investor("Ada", deposit=D("1000"))
            pending = await h.investor("Bea")
            await h.post(f"/{iid}/closure/request", {})
            await h.post(f"/{iid}/closure/approve", {"payment_reference": "T"})
            for who in (iid, pending):
                st = (await h.get(f"/{who}")).json()["statement"]
                assert "E" not in st["units"] and D(st["units"]) == 0, st["units"]
            rows = (await h.get("")).json()["investors"]
            assert all("E" not in r["units"] for r in rows)
            rec = (await h.get("/reconciliation", params={"pool_equity": "0"})).json()
            assert "E" not in rec["units_in_issue"], rec["units_in_issue"]
    run(go())


def test_fees_preview_close_once_and_mark_paid():
    async def go():
        async with Harness() as h:
            await h.investor("Ada", deposit=D("1000"), on=date(2026, 5, 31))
            assert (await h.get("/fees/preview", params={"year": 2026, "month": 6})).status_code == 400
            await h.post("/nav/snapshot", {"pool_equity": "1100", "on": "2026-06-30"})
            pv = (await h.get("/fees/preview", params={"year": 2026, "month": 6})).json()
            assert pv["lines"][0]["name"] == "Ada" and D(pv["total"]) > 0 and not pv["already_closed"]
            assert await h.count(Adjustment) == 0
            r = await h.post("/fees/close", {"year": 2026, "month": 6})
            assert r.status_code == 201 and r.json()["charged"] == pv["total"]
            assert (await h.post("/fees/close", {"year": 2026, "month": 6})).status_code == 400
            # a month that has not ended cannot be closed
            assert (await h.post("/fees/close", {"year": 2099, "month": 1})).status_code == 400
            periods = (await h.get("/fees/periods")).json()
            assert periods[0]["charged"] == pv["total"] and periods[0]["paid"] == "0.00"
            assert (await h.post("/fees/paid", {"period_start": "2026-06-01", "period_end": "2026-06-30",
                                                "on": "2026-07-03",
                                                "reference": "MGR"})).status_code == 200
            assert (await h.get("/fees/periods")).json()[0]["paid"] == pv["total"]
    run(go())
