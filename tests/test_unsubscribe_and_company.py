"""
tests/test_unsubscribe_and_company.py

One click unsubscribes, and the legal identity is a versioned row an admin owns.

WHY THE UNSUBSCRIBE LINK IS NOT AUTHENTICATED
---------------------------------------------
RFC 8058 one-click means the reader's MAIL PROVIDER issues the request — Gmail
and Yahoo both do — with no cookies, no session and no page. So the link must
act on a bare POST. That forces two properties, both tested here:

  * the token can do exactly one thing (turn one email category off for one
    investor) and cannot be exchanged for a session, so a link sitting in a
    mail archive authenticates nobody;
  * it never expires, because the right to unsubscribe does not.

WHY THE PROFILE IS VERSIONED
----------------------------
A privacy policy is a statement made to a person on a date. "What were we
telling investors in October" has to be answerable from rows, and editing in
place makes it unanswerable. Same reasoning as FundSettings.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.api.deps import get_current_user
from backend.api.routes import admin_investors, public
from backend.data.database import get_db
from backend.data.models import Base
from backend.investor import prefs
from backend.investor import unsubscribe as unsubmod
from backend.investor.models import CompanyProfile, Investor, InvestorAuditLog
from backend.notify import company as companymod
from backend.notify import outbox

ADMIN = SimpleNamespace(id="admin-1", is_admin=True, is_active=True)


def run(coro):
    return asyncio.run(coro)


class Site:
    """The public and admin routers on a file-backed database.

    A temp file, not ":memory:" on a StaticPool — see the note on `App` in
    tests/test_investor_portal.py for the data loss that causes.
    """

    async def __aenter__(self):
        public.reset_rate_limit()
        companymod.set_snapshot(companymod.Snapshot.empty())
        self._tmp = tempfile.mkdtemp(prefix="algoedge-unsub-")
        self.engine = create_async_engine(
            f"sqlite+aiosqlite:///{Path(self._tmp).as_posix()}/site.db")
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
        app.include_router(admin_investors.router)
        app.dependency_overrides[get_db] = _db
        app.dependency_overrides[get_current_user] = lambda: ADMIN
        self.http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                      base_url="http://t")
        return self

    async def __aexit__(self, *exc):
        await outbox.drain()
        outbox.use_session_factory(None)
        companymod.set_snapshot(None)
        await self.http.aclose()
        await self.engine.dispose()
        shutil.rmtree(self._tmp, ignore_errors=True)

    async def investor(self, iid="inv-1"):
        async with self.Session() as s:
            s.add(Investor(id=iid, email=f"{iid}@x.com", name="Ada Obi"))
            await s.commit()
        return iid

    async def prefs_of(self, iid="inv-1") -> dict:
        async with self.Session() as s:
            row = await s.get(Investor, iid)
            return prefs.load(row.preferences)

    async def audit_actions(self) -> list[str]:
        async with self.Session() as s:
            return [r.action for r in
                    (await s.execute(select(InvestorAuditLog))).scalars().all()]


# ── the token ───────────────────────────────────────────────────────────────

def test_the_token_round_trips():
    t = unsubmod.make_token("inv-1", "statements")
    assert unsubmod.read_token(t) == ("inv-1", "statements")


@pytest.mark.parametrize("bad", ["", "nodot", "aaa.bbb", "a.b.c"])
def test_a_forged_or_malformed_token_is_refused(bad):
    with pytest.raises(unsubmod.BadToken):
        unsubmod.read_token(bad)


def test_flipping_one_character_of_the_signature_is_refused():
    t = unsubmod.make_token("inv-1")
    flipped = t[:-1] + ("A" if t[-1] != "A" else "B")
    with pytest.raises(unsubmod.BadToken):
        unsubmod.read_token(flipped)


def test_a_token_cannot_name_a_setting_that_is_not_unsubscribable():
    """Transactional mail is the record of what happened to someone's money.

    `outbox` never attaches an unsubscribe link to it, and this is the second
    lock: asking for one is refused rather than honoured.
    """
    with pytest.raises(ValueError):
        unsubmod.make_token("inv-1", "deposit_confirmed")
    assert "money" not in unsubmod.SETTINGS


def test_marketing_switches_off_every_optional_email():
    """Someone clicking unsubscribe on a promotion means all of it, not only
    the one they happened to open."""
    out = json.loads(unsubmod.apply_to(None, "marketing"))
    assert set(out["email"].values()) == {False}
    # and the phone pushes are a separate choice they did not make here
    assert all(out["push"].values())


# ── the endpoint ────────────────────────────────────────────────────────────

def test_one_click_post_works_with_no_session_at_all():
    async def go():
        async with Site() as site:
            iid = await site.investor()
            token = unsubmod.make_token(iid, "marketing")
            r = await site.http.post(f"/api/public/unsubscribe?t={token}")
            assert r.status_code == 200, r.text
            assert set((await site.prefs_of(iid))["email"].values()) == {False}
            assert "email.unsubscribed.marketing" in await site.audit_actions()
    run(go())


def test_a_person_clicking_the_link_gets_an_answer_in_words():
    async def go():
        async with Site() as site:
            iid = await site.investor()
            r = await site.http.get(
                f"/api/public/unsubscribe?t={unsubmod.make_token(iid, 'statements')}")
            assert r.status_code == 200
            assert r.json()["message"], "a blank page is indistinguishable from a failure"
            p = await site.prefs_of(iid)
            assert p["email"]["statements"] is False
            assert p["email"]["trades"] is True, "one setting means one setting"
    run(go())


def test_unsubscribing_twice_is_not_an_error():
    """A provider may retry, and a person may click twice."""
    async def go():
        async with Site() as site:
            iid = await site.investor()
            t = unsubmod.make_token(iid)
            assert (await site.http.post(f"/api/public/unsubscribe?t={t}")).status_code == 200
            assert (await site.http.post(f"/api/public/unsubscribe?t={t}")).status_code == 200
    run(go())


def test_an_unknown_investor_gets_the_same_answer():
    """The endpoint is public; "that is not a real investor" is something a
    prober would like to learn."""
    async def go():
        async with Site() as site:
            r = await site.http.post(
                f"/api/public/unsubscribe?t={unsubmod.make_token('nobody')}")
            assert r.status_code == 200
    run(go())


def test_a_bad_token_is_refused():
    async def go():
        async with Site() as site:
            assert (await site.http.post("/api/public/unsubscribe?t=rubbish")).status_code == 400
    run(go())


def test_the_endpoint_is_braked():
    async def go():
        async with Site() as site:
            await site.investor()
            # the brake is keyed on the token prefix, so one token repeatedly
            codes = []
            for _ in range(8):
                r = await site.http.post(
                    f"/api/public/unsubscribe?t={unsubmod.make_token('inv-1')}")
                codes.append(r.status_code)
            assert 429 in codes, "a public endpoint must not be probeable at speed"
    run(go())


# ── the company profile ─────────────────────────────────────────────────────

def test_an_absent_profile_reports_what_is_missing_rather_than_erroring():
    async def go():
        async with Site() as site:
            r = await site.http.get("/api/admin/investors/company")
            assert r.status_code == 200
            body = r.json()
            assert body["current"]["version"] == 0
            assert body["blocks_commercial_email"] is True
            assert "legal_entity_name" in body["missing"]
            assert "address_line1" in body["missing"]
    run(go())


def test_saving_the_profile_writes_a_new_version_and_keeps_the_old_one():
    async def go():
        async with Site() as site:
            r1 = await site.http.post("/api/admin/investors/company",
                                      json={"legal_entity_name": "Alphavantiq Capital Ltd"})
            assert r1.status_code == 201
            assert r1.json()["version"] == 1

            r2 = await site.http.post("/api/admin/investors/company",
                                      json={"city": "Lagos", "country": "Nigeria",
                                            "address_line1": "1 Example Road"})
            assert r2.json()["version"] == 2
            # the name set in version 1 is carried forward, not lost
            assert r2.json()["legal_entity_name"] == "Alphavantiq Capital Ltd"
            assert r2.json()["postal_address"] == "1 Example Road, Lagos, Nigeria"

            history = (await site.http.get("/api/admin/investors/company")).json()["history"]
            assert [h["version"] for h in history] == [2, 1], (
                "the old version must survive — a policy is a statement made on a date"
            )
    run(go())


def test_filling_the_profile_unblocks_commercial_email():
    async def go():
        async with Site() as site:
            assert not companymod.get().can_send_commercial_email
            await site.http.post("/api/admin/investors/company",
                                 json={"legal_entity_name": "Alphavantiq Capital Ltd",
                                       "address_line1": "1 Example Road",
                                       "city": "Lagos", "country": "Nigeria"})
            # the cache the email templates read must be refreshed by the save,
            # or the next email still carries no address
            assert companymod.get().can_send_commercial_email, (
                "saving must refresh the process cache the templates read"
            )
            assert companymod.get().postal_address == "1 Example Road, Lagos, Nigeria"
    run(go())


def test_an_empty_save_is_refused():
    async def go():
        async with Site() as site:
            assert (await site.http.post("/api/admin/investors/company", json={})).status_code == 400
    run(go())


def test_the_save_is_audited():
    async def go():
        async with Site() as site:
            await site.http.post("/api/admin/investors/company",
                                 json={"jurisdiction": "Nigeria"})
            assert "company.profile_changed" in await site.audit_actions()
    run(go())


def test_the_profile_is_never_created_implicitly():
    """A blank legal identity in the database looks like a decision somebody
    made. That decision has not been made yet."""
    async def go():
        async with Site() as site:
            await site.http.get("/api/admin/investors/company")
            async with site.Session() as s:
                rows = (await s.execute(select(CompanyProfile))).scalars().all()
            assert rows == [], "reading must not insert"
    run(go())


# ── the published policies ──────────────────────────────────────────────────

def test_the_legal_endpoint_answers_before_a_profile_exists():
    """privacy.html calls this on every load. It must never 404 or 500."""
    async def go():
        async with Site() as site:
            r = await site.http.get("/api/public/legal")
            assert r.status_code == 200
            assert r.json()["has_profile"] is False
            assert r.json()["legal_entity_name"] == ""
    run(go())


def test_the_legal_endpoint_publishes_the_saved_identity():
    async def go():
        async with Site() as site:
            await site.http.post("/api/admin/investors/company", json={
                "legal_entity_name": "Alphavantiq Capital Ltd",
                "address_line1": "1 Example Road", "city": "Lagos", "country": "Nigeria",
                "jurisdiction": "Nigeria", "support_email": "invest@example.com",
                "policy_version": "2026-10"})
            d = (await site.http.get("/api/public/legal")).json()
            assert d["has_profile"] is True
            assert d["postal_address"] == "1 Example Road, Lagos, Nigeria"
            assert d["jurisdiction"] == "Nigeria"
            assert d["policy_version"] == "2026-10"
            # privacy_email falls back to support_email rather than coming back
            # blank, because a policy with no contact address is not a policy
            assert d["privacy_email"] == "invest@example.com"
    run(go())


def test_the_legal_endpoint_needs_no_authentication():
    """It is called by a static page with no session, and everything it
    returns is already printed on the policies themselves."""
    src = Path("backend/api/routes/public.py").read_text(encoding="utf-8")
    block = src.split('@router.get("/legal")', 1)[1].split("@router.", 1)[0]
    assert "Depends(get_current_user)" not in block
    assert "require_admin" not in block


@pytest.mark.parametrize("page", ["landing/privacy.html", "landing/terms.html"])
def test_the_policies_fill_themselves_in(page):
    html = Path(page).read_text(encoding="utf-8")
    assert 'data-legal="legal_entity_name"' in html
    assert 'data-legal="postal_address"' in html
    assert "/legal.js" in html and "/config.js" in html


@pytest.mark.parametrize("page", ["landing/privacy.html", "landing/terms.html"])
def test_a_policy_degrades_to_real_wording_rather_than_a_blank(page):
    """With the API unreachable the page shows what is written in the HTML.

    A legal document that renders "registered at ." when a request fails is
    worse than one that stays general, so every placeholder either has fallback
    text of its own or is removed entirely.
    """
    import re

    html = Path(page).read_text(encoding="utf-8")
    for m in re.finditer(r'<span data-legal="([a-z_]+)"([^>]*)>(.*?)</span>', html):
        key, attrs, inner = m.group(1), m.group(2), m.group(3)
        has_wrap = "data-legal-wrap" in attrs
        assert inner.strip() or has_wrap, (
            f"{key} in {page} is empty and has no wrap — it would render as a "
            f"blank if the API were unreachable"
        )
    js = Path("landing/legal.js").read_text(encoding="utf-8")
    assert "el.remove()" in js, "an optional clause with no value must be removed"
    assert ".catch(" in js, "an unreachable API must leave the page as written"
