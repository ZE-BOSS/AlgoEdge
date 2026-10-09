"""
tests/test_age_gate.py

Nobody under 18 gets an application recorded, and the server is what decides.

WHY
---
`terms.html` S2 has always said "You must be at least 18", and until 2026-10-08
the application form never asked. The promise and the code disagreed, which is
the worse of the two failure modes: a minor who signs up has been told they are
allowed to.

The gate is enforced in `public.apply`, not in the browser. A checkbox is a
suggestion — a direct POST skips it — and "we had a checkbox" is not a record of
anything. What is kept is `date_of_birth` and `age_confirmed_at`, because the
only useful artefact afterwards is evidence of what was asked, what was answered
and when.
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.api.routes import public
from backend.data.database import get_db
from backend.data.models import Base
from backend.investor.models import Application
from backend.notify import outbox

TODAY = datetime.now(timezone.utc).date()
ADULT = (TODAY - timedelta(days=365 * 30)).isoformat()
EXACTLY_18 = date(TODAY.year - 18, TODAY.month, TODAY.day).isoformat()
ONE_DAY_SHORT = (date(TODAY.year - 18, TODAY.month, TODAY.day) + timedelta(days=1)).isoformat()
CHILD = (TODAY - timedelta(days=365 * 12)).isoformat()

FORM = {"name": "Chidi Obi", "email": "chidi@x.com", "country": "Nigeria",
        "amount_band": "$3,000 to $5,000", "consent": True}


class Site:
    """The public router alone, on its own file-backed database.

    A temp file rather than `:memory:` with a StaticPool: the outbox delivers
    after the request commits, and on a single shared connection that commit
    lands mid-statement and rolls the request back. See test_investor_public.py.
    """

    async def __aenter__(self):
        public.reset_rate_limit()
        self._tmp = tempfile.mkdtemp(prefix="algoedge-age-")
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
        app.dependency_overrides[get_db] = _db
        self.http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                      base_url="http://t")
        return self

    async def __aexit__(self, *exc):
        await outbox.drain()
        outbox.use_session_factory(None)
        await self.http.aclose()
        await self.engine.dispose()
        shutil.rmtree(self._tmp, ignore_errors=True)

    async def apply(self, **over):
        return await self.http.post("/api/public/apply", json={**FORM, **over})

    async def applications(self):
        async with self.Session() as s:
            return (await s.execute(select(Application))).scalars().all()


def run(coro):
    return asyncio.run(coro)


# ── the arithmetic, which is the part that is easy to get wrong ─────────────

@pytest.mark.parametrize("dob,today,expected", [
    (date(2008, 10, 8), date(2026, 10, 8), 18),   # the birthday itself counts
    (date(2008, 10, 9), date(2026, 10, 8), 17),   # one day short
    (date(2008, 2, 29), date(2026, 2, 28), 17),   # leap-day birthday, before
    (date(2008, 2, 29), date(2026, 3, 1), 18),    # leap-day birthday, after
    (date(1990, 1, 1), date(2026, 12, 31), 36),
])
def test_the_age_arithmetic(dob, today, expected):
    assert public._age_on(dob, today) == expected


# ── the gate ────────────────────────────────────────────────────────────────

def test_an_adult_gets_through_and_the_attestation_is_kept():
    async def go():
        async with Site() as site:
            r = await site.apply(date_of_birth=ADULT)
            assert r.status_code == 201, r.text
            rows = await site.applications()
            assert len(rows) == 1
            assert rows[0].date_of_birth == date.fromisoformat(ADULT)
            assert rows[0].age_confirmed_at is not None, (
                "the server must record that IT accepted the age, not that a "
                "browser rendered a field"
            )
    run(go())


def test_exactly_eighteen_today_is_allowed():
    async def go():
        async with Site() as site:
            assert (await site.apply(date_of_birth=EXACTLY_18)).status_code == 201
    run(go())


def test_one_day_short_of_eighteen_is_refused_and_nothing_is_kept():
    async def go():
        async with Site() as site:
            r = await site.apply(date_of_birth=ONE_DAY_SHORT)
            assert r.status_code == 400
            assert "18" in r.json()["detail"]
            assert await site.applications() == [], (
                "a refused application must leave no row — storing a minor's "
                "details is the exposure, not just accepting them"
            )
    run(go())


def test_a_child_is_refused():
    async def go():
        async with Site() as site:
            assert (await site.apply(date_of_birth=CHILD)).status_code == 400
            assert await site.applications() == []
    run(go())


def test_saying_nothing_about_age_is_refused():
    """The gate is not optional, which is what makes it a gate.

    A direct POST that simply omits the field is the case a browser checkbox
    never covers.
    """
    async def go():
        async with Site() as site:
            r = await site.apply()
            assert r.status_code == 400
            assert "18" in r.json()["detail"]
            assert await site.applications() == []
    run(go())


def test_the_tickbox_is_accepted_where_a_date_is_not_given():
    async def go():
        async with Site() as site:
            r = await site.apply(confirms_18=True)
            assert r.status_code == 201
            rows = await site.applications()
            assert rows[0].date_of_birth is None
            assert rows[0].age_confirmed_at is not None
    run(go())


def test_declining_the_tickbox_is_refused():
    async def go():
        async with Site() as site:
            assert (await site.apply(confirms_18=False)).status_code == 400
    run(go())


def test_a_date_of_birth_beats_a_tickbox_that_contradicts_it():
    """Someone could tick "I am 18" and give a date that says otherwise.

    The verifiable answer wins, and the verifiable answer is the date.
    """
    async def go():
        async with Site() as site:
            r = await site.apply(date_of_birth=CHILD, confirms_18=True)
            assert r.status_code == 400, "the tickbox must not override the date"
            assert await site.applications() == []
    run(go())


@pytest.mark.parametrize("dob", ["2099-01-01", "1850-01-01"])
def test_an_impossible_date_is_refused(dob):
    async def go():
        async with Site() as site:
            assert (await site.apply(date_of_birth=dob)).status_code == 400
    run(go())


def test_the_honeypot_still_wins_over_everything():
    """A bot filling the hidden field is thanked and nothing is kept — and that
    must not be reachable by failing the age gate instead."""
    async def go():
        async with Site() as site:
            r = await site.apply(date_of_birth=ADULT, website="x")
            assert r.status_code == 201
            assert await site.applications() == []
    run(go())


# ── the promise and the form must agree ────────────────────────────────────

def test_the_public_form_asks_for_the_date_of_birth():
    html = Path("landing/index.html").read_text(encoding="utf-8")
    assert 'name="date_of_birth"' in html, "the form must ask what the server requires"
    assert 'id="dob"' in html


def test_the_forms_age_limit_is_computed_not_written_down():
    """A hard-coded `max` is right for a year and then lets a 17-year-old past
    the browser check."""
    js = Path("landing/site.js").read_text(encoding="utf-8")
    assert "setFullYear(d.getFullYear() - 18)" in js
    html = Path("landing/index.html").read_text(encoding="utf-8")
    assert 'max="20' not in html, "the date limit must not be a literal in the markup"


def test_the_terms_still_state_the_age_the_code_enforces():
    terms = Path("landing/terms.html").read_text(encoding="utf-8")
    assert "at least 18" in terms
    assert public.MINIMUM_AGE == 18, (
        "if the code's minimum changes, terms.html must change in the same commit"
    )


def test_a_bot_learns_nothing_from_the_age_gate():
    """The honeypot is checked FIRST, so a bot gets the same bland thanks
    whatever else it did or did not send.

    If the age gate ran first, a bot that omitted the date would get a 400
    naming the field it needs to fake next time.
    """
    async def go():
        async with Site() as site:
            r = await site.apply(website="x")          # no date of birth at all
            assert r.status_code == 201, (
                "a bot must be thanked, not told which field failed"
            )
            assert "Thank you" in r.json()["message"]
            assert await site.applications() == []
    run(go())
