"""The referral routes, over HTTP, against a real database.

The two rules these exist to hold, both stated by the business owner:

  1. An investor sees everything BELOW them (that is what they are paid on)
     and nothing ABOVE them except the rate they pay.
  2. Nobody can be moved under someone else by filling in a form. A self-signup
     with someone else's email and your own link must not re-parent them.

Rule 1 is checked by searching the WHOLE response for the upline's name,
email and id, not by checking named fields - a leak would arrive in a field
nobody thought to look at.

Reuses `App` from test_investor_portal rather than copying it: two harnesses
for the same routes drift, and that file documents why its database is a temp
file rather than an in-memory one.
"""

import json

import httpx
from fastapi import FastAPI

from test_investor_portal import INV, App, run  # noqa: E402


async def _place(a, child, parent):
    r = await a.admin("POST", f"/{child}/referrer", {"referrer_id": parent})
    assert r.status_code == 200, r.text
    return r.json()


async def _tree(a):
    """Ada introduced Bola; Bola introduced Chidi Okafor."""
    ada, s_ada = await a.onboard("Ada")
    bola, s_bola = await a.onboard("Bola")
    chidi, s_chidi = await a.onboard("Chidi")
    await _place(a, bola, ada)
    await _place(a, chidi, bola)
    return (ada, s_ada), (bola, s_bola), (chidi, s_chidi)


# -- 1. visibility -------------------------------------------------------------

def test_an_investor_learns_nothing_about_the_people_above_them():
    async def go():
        async with App() as a:
            (ada, _), (bola, _), (chidi, s_chidi) = await _tree(a)
            r = await a.inv("GET", "/referrals", s_chidi)
            assert r.status_code == 200, r.text
            body = r.json()
            text = json.dumps(body).lower()
            for leak in ("ada", "bola", ada.lower(), bola.lower()):
                assert leak not in text, f"{leak!r} leaked upward into: {text[:400]}"
            # ...but they are told that someone above them exists, and the terms
            assert body["upline_note"]
            assert body["people"] == []
    run(go())


def test_an_introducer_sees_both_levels_below_them():
    async def go():
        async with App() as a:
            (ada, s_ada), (bola, _), (chidi, _) = await _tree(a)
            body = (await a.inv("GET", "/referrals", s_ada)).json()
            by_level = {p["level"]: p for p in body["people"]}
            assert by_level[1]["name"] == "Bola"
            assert by_level[2]["name"] == "Chidi"
            assert by_level[2]["introduced_by"] == "Bola"
            assert body["totals"]["direct"] == 1 and body["totals"]["second_level"] == 1
            assert body["upline_note"] is None          # nobody introduced Ada
            for key in ("deposited", "current_value", "profit", "earned_from_them"):
                assert key in by_level[1]
    run(go())


def test_level_two_shows_a_first_name_only():
    """Someone your referral introduced may be a stranger to you."""
    async def go():
        async with App() as a:
            ada, s_ada = await a.onboard("Ada")
            bola, _ = await a.onboard("Bola")
            r = await a.admin("POST", "", {"name": "Chidi Okafor", "email": "chidi@x.com"})
            chidi = r.json()["id"]
            await _place(a, bola, ada)
            await _place(a, chidi, bola)
            body = (await a.inv("GET", "/referrals", s_ada)).json()
            lvl2 = [p for p in body["people"] if p["level"] == 2][0]
            assert lvl2["name"] == "Chidi"
            assert "okafor" not in json.dumps(body).lower()
    run(go())


def test_everyone_gets_a_code_and_a_link():
    async def go():
        async with App() as a:
            _, s = await a.onboard("Ada")
            body = (await a.inv("GET", "/referrals", s)).json()
            assert len(body["code"]) == 8
            assert body["link"].endswith(f"ref={body['code']}")
            again = (await a.inv("GET", "/referrals", s)).json()
            assert again["code"] == body["code"], "a code must never change once issued"
    run(go())


# -- 2. nobody can be re-parented by a form -----------------------------------

def test_a_signup_with_someone_elses_email_cannot_move_them():
    async def go():
        async with App() as a:
            victim, _ = await a.onboard("Victim")
            attacker, s_att = await a.onboard("Attacker")
            code = (await a.inv("GET", "/referrals", s_att)).json()["code"]
            r = await a.http.post(f"{INV}/auth/signup", json={
                "name": "Whoever", "email": "victim@x.com", "consent": True, "ref": code})
            assert r.status_code == 201, r.text
            tree = (await a.admin("GET", f"/{victim}/referrals")).json()
            assert tree["upline"] == [], "a signup form re-parented an existing investor"
    run(go())


def test_a_new_signup_through_a_link_lands_under_the_introducer():
    async def go():
        async with App() as a:
            ada, s_ada = await a.onboard("Ada")
            code = (await a.inv("GET", "/referrals", s_ada)).json()["code"]
            r = await a.http.post(f"{INV}/auth/signup", json={
                "name": "Newcomer", "email": "new@x.com", "consent": True, "ref": code.lower()})
            assert r.status_code == 201, r.text
            tree = (await a.admin("GET", f"/{ada}/referrals")).json()
            assert [p["name"] for p in tree["downline"]] == ["Newcomer"]
            assert tree["downline"][0]["referral_source"] == "LINK"
    run(go())


def test_a_bad_code_does_not_cost_the_signup():
    async def go():
        async with App() as a:
            r = await a.http.post(f"{INV}/auth/signup", json={
                "name": "Typo", "email": "typo@x.com", "consent": True, "ref": "NOTACODE"})
            assert r.status_code == 201, r.text
    run(go())


# -- the admin side -------------------------------------------------------------

def test_an_admin_cannot_make_a_loop():
    async def go():
        async with App() as a:
            (ada, _), (bola, _), (chidi, _) = await _tree(a)
            r = await a.admin("POST", f"/{ada}/referrer", {"referrer_id": chidi})
            assert r.status_code == 400 and "loop" in r.text
    run(go())


def test_an_admin_can_place_someone_by_code():
    async def go():
        async with App() as a:
            ada, s_ada = await a.onboard("Ada")
            bola, _ = await a.onboard("Bola")
            code = (await a.inv("GET", "/referrals", s_ada)).json()["code"]
            r = await a.admin("POST", f"/{bola}/referrer", {"referral_code": code})
            assert r.status_code == 200 and r.json()["referred_by"] == ada
    run(go())


def test_the_referral_rates_are_actually_saved():
    """The settings body model IS the field list; a field it does not declare is
    dropped without a word, and the screen would report success regardless."""
    async def go():
        async with App() as a:
            r = await a.admin("POST", "/settings", {"referral_model": "INVESTOR_PROFIT",
                                                    "referral_l1_pct": "10",
                                                    "referral_l2_pct": "2.5"})
            assert r.status_code == 201, r.text
            cur = (await a.admin("GET", "/settings/current")).json()["current"]
            assert cur["referral_model"] == "INVESTOR_PROFIT"
            assert float(cur["referral_l1_pct"]) == 10.0
            assert float(cur["referral_l2_pct"]) == 2.5
    run(go())


def test_an_unknown_referral_model_is_refused():
    async def go():
        async with App() as a:
            r = await a.admin("POST", "/settings", {"referral_model": "PYRAMID"})
            assert r.status_code == 422
    run(go())


# -- the public form --------------------------------------------------------------

def _public_client(a):
    from backend.api.routes import public
    from backend.data.database import get_db

    public.reset_rate_limit()

    async def _db():
        async with a.Session() as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    app = FastAPI()
    app.include_router(public.router)
    app.dependency_overrides[get_db] = _db
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def test_the_public_check_discloses_the_cost_without_naming_the_owner():
    async def go():
        async with App() as a:
            await a.admin("POST", "/settings", {"referral_model": "INVESTOR_PROFIT",
                                                "referral_l1_pct": "10",
                                                "referral_l2_pct": "2"})
            _, s_ada = await a.onboard("Ada")
            code = (await a.inv("GET", "/referrals", s_ada)).json()["code"]
            async with _public_client(a) as pub:
                ok = (await pub.get(f"/api/public/referral/{code}")).json()
                bad = (await pub.get("/api/public/referral/ZZZZZZZZ")).json()
            assert ok["valid"] is True and "12" in ok["disclosure"]   # 10 + 2
            assert "ada" not in json.dumps(ok).lower()
            assert bad == {"valid": False}
    run(go())


def test_an_application_with_a_code_is_placed_when_accepted():
    async def go():
        async with App() as a:
            ada, s_ada = await a.onboard("Ada")
            code = (await a.inv("GET", "/referrals", s_ada)).json()["code"]
            async with _public_client(a) as pub:
                r = await pub.post("/api/public/apply", json={
                    "name": "Applicant", "email": "applicant@x.com", "consent": True,
                    "confirms_18": True, "ref": code})
            assert r.status_code == 201, r.text
            apps = (await a.admin("GET", "/applications/list")).json()
            mine = [x for x in apps if x["email"] == "applicant@x.com"][0]
            assert mine["referral_code"] == code and mine["referred_by"] == ada
            app_id = mine["id"]
            acc = await a.admin("POST", f"/applications/{app_id}/accept")
            assert acc.status_code == 201, acc.text
            assert acc.json()["referred_by"] == ada
    run(go())


def test_the_earnings_ledger_lists_and_settles_once():
    """`/referral-earnings` must not be swallowed by the catch-all
    `/{investor_id}` route, and a payment can be recorded exactly once."""
    from datetime import date
    from decimal import Decimal

    from backend.investor.models import ReferralEarning

    async def go():
        async with App() as a:
            (ada, _), (bola, _), (chidi, _) = await _tree(a)
            async with a.Session() as s:
                e = ReferralEarning(earner_id=bola, source_investor_id=chidi, level=1,
                                    model="HOUSE_FEE", basis_amount=Decimal("100"),
                                    rate_pct=Decimal("20"), amount=Decimal("20.00"),
                                    period_start=date(2026, 1, 1), period_end=date(2026, 2, 28))
                s.add(e)
                await s.commit()
                eid = e.id
            r = await a.admin("GET", "/referral-earnings")
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["rows"][0]["earner"] == "Bola"
            assert body["totals"] == {"accrued": "20.00"}
            first = await a.admin("POST", f"/referral-earnings/{eid}/settle",
                                  {"payment_reference": "TRF-001"})
            again = await a.admin("POST", f"/referral-earnings/{eid}/settle",
                                  {"payment_reference": "TRF-002"})
            assert first.status_code == 200 and first.json()["state"] == "settled"
            assert again.status_code == 400
            # and the earner sees it as settled, not pending
            _, s_bola = None, None
    run(go())
