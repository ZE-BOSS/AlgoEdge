"""
tests/test_investor_email.py

Phase 4 — email, statements, and the jobs that send them.

  * a message is sent only if the transaction that produced it COMMITS
  * log mode sends nothing but records everything; live mode talks to Resend
    with an idempotency key and records the provider id, or the failure
  * forgot-password answers identically for unknown addresses and is braked
  * a payout-account change emails the investor and sends withdrawals to
    review for 48 hours; entering details the first time does neither
  * closure emails the final statement to the address the person HAD
  * statements: a PDF per month, sent once, and only after that month's fees
  * the admin is told about a sign-in from a new browser
  * the nightly ledger check runs once a day and alerts on drift
"""

from __future__ import annotations

import asyncio
import base64
import json
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import httpx
from sqlalchemy import select

from backend.api.routes import auth as authroutes
from backend.investor import jobs as jobsmod
from backend.investor import nav as navmod
from backend.investor.models import EmailLog, InvestorUnits, JobRun
from backend.notify import outbox
from backend.notify import templates as mail
from test_investor_portal import INV, PASSWORD, App, _funded_long_ago_with_profit

D = Decimal


def run(coro):
    return asyncio.run(coro)


async def emails(a, kind=None):
    await outbox.drain()
    async with a.Session() as s:
        q = select(EmailLog).order_by(EmailLog.id)
        if kind:
            q = q.where(EmailLog.kind == kind)
        return (await s.execute(q)).scalars().all()


# ── only after commit ────────────────────────────────────────────────────────

def test_a_confirmation_is_emailed_and_a_refused_action_emails_nothing(monkeypatch):
    monkeypatch.setenv("EMAIL_MODE", "log")

    async def go():
        async with App() as a:
            iid, sess = await a.onboard()
            await a.inv("POST", "/deposits", sess, {"amount": "1000"})
            dep_id = (await a.admin("GET", "/queues/deposits")).json()[0]["id"]
            ok = await a.admin("POST", f"/deposits/{dep_id}/confirm", {"amount_confirmed": "950"})
            assert ok.status_code == 200
            sent = await emails(a, "deposit_confirmed")
            assert len(sent) == 1 and sent[0].to_address == "ada@x.com" and sent[0].state == "logged"
            assert "$950.00" in sent[0].subject

            # confirming again is refused -> rolled back -> nothing queued survives
            again = await a.admin("POST", f"/deposits/{dep_id}/confirm", {"amount_confirmed": "950"})
            assert again.status_code == 400
            assert len(await emails(a, "deposit_confirmed")) == 1
    run(go())


def test_live_mode_calls_resend_with_an_idempotency_key(monkeypatch):
    monkeypatch.setenv("EMAIL_MODE", "live")
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    seen = []

    def handler(request: httpx.Request):
        seen.append(request)
        body = json.loads(request.content)
        if body["to"] == ["fail@x.com"]:
            return httpx.Response(422, json={"message": "invalid to"})
        return httpx.Response(200, json={"id": "resend-123"})

    outbox.use_transport(httpx.MockTransport(handler))

    async def go():
        async with App() as a:
            m = mail.statement(type("I", (), {"email": "ada@x.com", "name": "Ada", "id": "i1"})(),
                               "June 2026", b"%PDF-1.4 test", D("100"))
            assert await outbox.deliver(m) == "sent"
            m.to = "fail@x.com"
            assert await outbox.deliver(m) == "failed"
            rows = await emails(a)
            assert rows[0].provider_id == "resend-123" and rows[0].attachments == [m.attachments[0][0]]
            assert "422" in rows[1].error
    try:
        run(go())
    finally:
        outbox.use_transport(None)
    req = seen[0]
    assert req.headers["Authorization"] == "Bearer re_test"
    assert req.headers["Idempotency-Key"].startswith("email-")
    body = json.loads(req.content)
    assert base64.b64decode(body["attachments"][0]["content"]) == b"%PDF-1.4 test"
    assert body["text"] and body["html"].startswith("<!doctype html>")


def test_no_key_means_log_mode_not_a_crash(monkeypatch):
    monkeypatch.setenv("EMAIL_MODE", "live")
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    assert outbox.mode() == "log"


def test_names_are_escaped_in_html():
    m = mail.password_changed(type("I", (), {"email": "x@x.com", "name": "<script>x</script>", "id": "i"})())
    assert "<script>" not in m.html and "&lt;script&gt;" in m.html


# ── forgot password ──────────────────────────────────────────────────────────

def test_forgot_password_does_not_reveal_who_is_a_client_and_is_braked(monkeypatch):
    monkeypatch.setenv("EMAIL_MODE", "log")

    async def go():
        async with App() as a:
            await a.onboard()
            unknown = await a.http.post(f"{INV}/auth/forgot", json={"email": "nobody@x.com"})
            known = await a.http.post(f"{INV}/auth/forgot", json={"email": "ada@x.com"})
            assert unknown.json() == known.json() and unknown.status_code == known.status_code == 200
            resets = await emails(a, "reset")
            assert len(resets) == 1 and resets[0].to_address == "ada@x.com"
            for _ in range(3):
                await a.http.post(f"{INV}/auth/forgot", json={"email": "ada@x.com"})
            assert len(await emails(a, "reset")) == 3          # braked at three an hour
    run(go())


def test_the_emailed_reset_link_works(monkeypatch):
    monkeypatch.setenv("EMAIL_MODE", "log")
    captured = []
    real = mail.reset

    def spy(inv, url):
        captured.append(url)
        return real(inv, url)
    monkeypatch.setattr(mail, "reset", spy)

    async def go():
        async with App() as a:
            await a.onboard()
            await a.http.post(f"{INV}/auth/forgot", json={"email": "ada@x.com"})
            token = captured[0].split("token=")[1]
            r = await a.http.post(f"{INV}/auth/accept", json={"token": token, "password": "brand new password"})
            assert r.status_code == 200
            assert (await a.http.post(f"{INV}/auth/login", json={"email": "ada@x.com",
                                                                 "password": PASSWORD})).status_code == 401
    run(go())


# ── payout account changes ───────────────────────────────────────────────────

def test_a_payout_change_emails_the_investor_and_sends_withdrawals_to_review(monkeypatch):
    monkeypatch.setenv("EMAIL_MODE", "log")

    async def go():
        async with App() as a:
            iid, sess = await _funded_long_ago_with_profit(a)       # details entered at onboarding
            assert await emails(a, "payout_changed") == []
            q = (await a.inv("GET", "/withdrawals/quote", sess)).json()
            assert q["cooling_off"] is False

            await a.admin("PATCH", f"/{iid}", {"payout_account_number": "5555555555",
                                               "reason": "investor request, verified by phone"})
            changed = await emails(a, "payout_changed")
            assert len(changed) == 1 and changed[0].to_address == "ada@x.com"
            r = await a.inv("POST", "/withdrawals", sess, {"amount": "10"})      # well inside the cap
            assert r.status_code == 201 and r.json()["state"] == "exception_pending"
            w = (await a.admin("GET", "/queues/withdrawals")).json()[0]
            assert "automatic review" in w["justification"]
    run(go())


# ── closure, statements ──────────────────────────────────────────────────────

def test_closure_emails_the_final_statement_to_the_address_they_had(monkeypatch):
    monkeypatch.setenv("EMAIL_MODE", "log")
    sent = []
    real = mail.closure_approved

    def spy(**kw):
        m = real(**kw)
        sent.append(m)
        return m
    monkeypatch.setattr(mail, "closure_approved", spy)

    async def go():
        async with App() as a:
            iid, sess = await _funded_long_ago_with_profit(a)
            last = navmod.accounting_date().replace(day=1) - timedelta(days=1)
            await a.admin("POST", "/fees/close", {"year": last.year, "month": last.month})
            await a.admin("POST", f"/{iid}/closure/request", {})
            r = await a.admin("POST", f"/{iid}/closure/approve", {"payment_reference": "T9"})
            assert r.status_code == 200, r.text
            rows = await emails(a, "closure_approved")
            assert rows[0].to_address == "ada@x.com"                    # not the anonymised one
            assert sent[0].attachments[0][1].startswith(b"%PDF")
            assert (await a.admin("GET", f"/{iid}")).json()["investor"]["email"].endswith(".invalid")
    run(go())


def test_statements_download_and_send_once_after_fees(monkeypatch):
    monkeypatch.setenv("EMAIL_MODE", "log")

    async def go():
        async with App() as a:
            iid, sess = await _funded_long_ago_with_profit(a)
            last = navmod.accounting_date().replace(day=1) - timedelta(days=1)
            listed = (await a.inv("GET", "/statements", sess)).json()
            assert listed[0]["label"] == f"{last.year}-{last.month:02d}"
            pdf = await a.inv("GET", f"/statements/{last.year}-{last.month}.pdf", sess)
            assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
            today = navmod.accounting_date()
            assert (await a.inv("GET", f"/statements/{today.year}-{today.month}.pdf", sess)).status_code == 400

            body = {"year": last.year, "month": last.month}
            refused = await a.admin("POST", "/statements/send", body)
            assert refused.status_code == 400 and "fees" in refused.json()["detail"]
            await a.admin("POST", "/fees/close", body)
            ok = await a.admin("POST", "/statements/send", body)
            assert ok.status_code == 202 and ok.json()["queued"] == 1
            assert (await a.admin("POST", "/statements/send", body)).status_code == 409
            rows = await emails(a, "statement")
            assert len(rows) == 1 and rows[0].attachments[0].endswith(".pdf")
    run(go())


# ── admin sign-in from a new browser ─────────────────────────────────────────

def test_the_admin_hears_about_a_new_browser_but_not_the_first_or_a_known_one(monkeypatch):
    monkeypatch.setenv("EMAIL_MODE", "log")

    async def go():
        async with App() as a:
            a.http._transport.app.include_router(authroutes.router)
            async with a.Session() as s:
                admin = User_(email="boss@x.com", password_hash=authroutes.pwd_context.hash("admin password"))
                s.add(admin)
                await s.commit()

            async def login(ua):
                r = await a.http.post("/api/auth/login", json={"email": "boss@x.com", "password": "admin password"},
                                      headers={"User-Agent": ua})
                assert r.status_code == 200 and r.json()["user"]["is_admin"] is True
            await login("Chrome/1 laptop")
            await login("Chrome/1 laptop")
            assert await emails(a, "admin_new_device") == []
            await login("Safari/2 unknown phone")
            rows = await emails(a, "admin_new_device")
            assert len(rows) == 1 and rows[0].to_address == "boss@x.com"
    run(go())


def User_(**kw):
    from backend.data.models import User
    return User(id=str(uuid.uuid4()), name="Boss", is_admin=True, is_active=True, **kw)


# ── nightly jobs ─────────────────────────────────────────────────────────────

def test_the_ledger_check_runs_once_a_day_and_alerts_on_drift(monkeypatch):
    monkeypatch.setenv("EMAIL_MODE", "log")
    monkeypatch.setenv("ADMIN_ALERT_EMAIL", "ops@x.com")

    async def go():
        async with App() as a:
            iid, _ = await a.onboard(deposit=D("1000"))
            day = navmod.accounting_date()
            async with a.Session() as s:
                assert (await jobsmod.ledger_check(s, day))["drift"] == []
            async with a.Session() as s:
                assert await jobsmod.ledger_check(s, day) is None        # already ran today
            async with a.Session() as s:
                row = await s.get(InvestorUnits, iid)
                row.units = D("999")                                     # corrupt the cache
                await s.commit()
            async with a.Session() as s:
                result = await jobsmod.ledger_check(s, day + timedelta(days=1))
            assert result["drift"][0][0] == iid
            alerts = await emails(a, "admin_ledger_drift")
            assert len(alerts) == 1 and alerts[0].to_address == "ops@x.com"
            async with a.Session() as s:
                assert len((await s.execute(select(JobRun))).scalars().all()) == 2

            async with a.Session() as s:
                stats = await jobsmod.daily_digest(s, day)
            assert stats["Deposits waiting to confirm"] == 0
            assert len(await emails(a, "admin_digest")) == 1
    run(go())


def test_a_rolled_back_transaction_sends_nothing_and_a_committed_one_does(monkeypatch):
    """The refusal test above never queues; this one queues and THEN rolls back."""
    monkeypatch.setenv("EMAIL_MODE", "log")
    who = type("I", (), {"email": "x@x.com", "name": "X", "id": None})()

    async def go():
        async with App() as a:
            async with a.Session() as s:
                outbox.queue(s, mail.password_changed(who))
                await s.rollback()
                await s.commit()            # a later commit must not resurrect it
            assert await emails(a) == []
            async with a.Session() as s:
                outbox.queue(s, mail.password_changed(who))
                await s.commit()
            assert len(await emails(a)) == 1
    run(go())


# ── operator sign-up closes once someone exists ─────────────────────────────

def test_registration_is_open_for_the_first_account_only(monkeypatch):
    monkeypatch.delenv("ALLOW_REGISTRATION", raising=False)

    async def go():
        from sqlalchemy import delete
        from backend.data.models import User
        async with App() as a:
            a.http._transport.app.include_router(authroutes.router)
            async with a.Session() as s:
                await s.execute(delete(User))
                await s.commit()

            def body(n):
                return {"email": f"op{n}@x.com", "password": "long enough", "name": f"Op {n}"}
            assert (await a.http.post("/api/auth/register", json=body(1))).status_code == 201
            r = await a.http.post("/api/auth/register", json=body(2))
            assert r.status_code == 403 and "closed" in r.json()["detail"]

            monkeypatch.setenv("ALLOW_REGISTRATION", "1")
            assert (await a.http.post("/api/auth/register", json=body(3))).status_code == 201
    run(go())
