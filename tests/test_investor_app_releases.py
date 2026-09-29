"""
tests/test_investor_app_releases.py

Phase 6 — the Android app's server side.

  * an APK upload must be an APK, with a version code above every earlier one
    (Android refuses a downgrade, so a mistake would strand updated phones)
  * the website and the app's update check read the current release; the
    download is the file, byte for byte, with its SHA-256 published
  * push tokens are registered per investor, move with the phone, and
    notifications mirror the investor emails that matter on a phone
  * a closed account's phones are forgotten
"""

from __future__ import annotations

import asyncio
import hashlib
import json

import httpx
from sqlalchemy import select

from backend.api.routes import public
from backend.investor.models import InvestorDevice
from backend.notify import outbox
from test_investor_portal import INV, App

APK = b"PK\x03\x04" + b"fake-signed-apk-body" * 100
TOKEN = "ExponentPushToken[abc123]"


def run(coro):
    return asyncio.run(coro)


async def upload(a, data=APK, name="app.apk", code=1, version="1.0.0", current=True):
    return await a.http.post(
        "/api/admin/investors/releases",
        headers={"Authorization": f"Bearer {a.admin_token}"},
        files={"file": (name, data, "application/vnd.android.package-archive")},
        data={"version_name": version, "version_code": str(code), "make_current": str(current).lower(),
              "notes": "first release"})


def test_releases_upload_publish_and_download(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_RELEASE_DIR", str(tmp_path))

    async def go():
        async with App() as a:
            a.http._transport.app.include_router(public.router)
            assert (await a.http.get("/api/public/app")).status_code == 404
            assert (await upload(a, name="app.zip")).status_code == 400
            assert (await upload(a, data=b"not an apk at all")).status_code == 400
            assert list(tmp_path.iterdir()) == []                    # the rejected file was removed
            r = await upload(a)
            assert r.status_code == 201 and r.json()["sha256"] == hashlib.sha256(APK).hexdigest()
            assert (await upload(a, code=1, version="1.0.1")).status_code == 400   # not higher

            cur = (await a.http.get("/api/public/app")).json()
            assert cur["version"] == "1.0.0" and cur["version_code"] == 1
            dl = await a.http.get(cur["url"].replace("http://test", ""))
            assert dl.status_code == 200 and dl.content == APK
            assert dl.headers["content-type"] == "application/vnd.android.package-archive"

            second = (await upload(a, code=2, version="1.1.0")).json()
            assert (await a.http.get("/api/public/app")).json()["version"] == "1.1.0"
            listed = (await a.admin("GET", "/releases/list")).json()
            first_id = [x["id"] for x in listed if x["version_code"] == 1][0]
            await a.admin("POST", f"/releases/{first_id}/current")           # roll back
            assert (await a.http.get("/api/public/app")).json()["version"] == "1.0.0"
            assert second["is_current"] is True
    run(go())


def test_push_tokens_move_with_the_phone_and_mirror_the_emails(monkeypatch):
    monkeypatch.setenv("EMAIL_MODE", "log")
    monkeypatch.setenv("PUSH_MODE", "live")
    sent = []

    def handler(request):
        body = json.loads(request.content)
        sent.append(body)
        return httpx.Response(200, json={"data": [{"status": "ok", "id": "t1"} for _ in body]})
    outbox.use_transport(httpx.MockTransport(handler))

    async def go():
        async with App() as a:
            ada_id, ada = await a.onboard("Ada")
            bea_id, bea = await a.onboard("Bea")
            assert (await a.inv("POST", "/devices", ada, {"push_token": "not-a-token"})).status_code == 422
            assert (await a.inv("POST", "/devices", ada, {"push_token": TOKEN})).status_code == 201
            # Bea signs in on the same phone: it is hers now
            await a.inv("POST", "/devices", bea, {"push_token": TOKEN})
            async with a.Session() as s:
                rows = (await s.execute(select(InvestorDevice))).scalars().all()
            assert [(r.investor_id) for r in rows] == [bea_id]

            await a.inv("POST", "/deposits", bea, {"amount": "1000"})
            dep = (await a.admin("GET", "/queues/deposits")).json()[0]["id"]
            await a.admin("POST", f"/deposits/{dep}/confirm", {"amount_confirmed": "1000"})
            await outbox.drain()
            assert len(sent) == 1 and sent[0][0]["to"] == TOKEN
            assert "$1,000.00" in sent[0][0]["title"] and sent[0][0]["data"]["kind"] == "deposit_confirmed"

            await a.inv("DELETE", "/devices", bea, params={"push_token": TOKEN})
            async with a.Session() as s:
                assert (await s.execute(select(InvestorDevice))).scalars().all() == []
    try:
        run(go())
    finally:
        outbox.use_transport(None)


def test_an_uninstalled_app_is_forgotten(monkeypatch):
    monkeypatch.setenv("PUSH_MODE", "live")

    def handler(request):
        return httpx.Response(200, json={"data": [{"status": "error", "details": {"error": "DeviceNotRegistered"}}]})
    outbox.use_transport(httpx.MockTransport(handler))

    async def go():
        async with App() as a:
            iid, sess = await a.onboard()
            await a.inv("POST", "/devices", sess, {"push_token": TOKEN})
            assert await outbox.push(iid, "t", "b") == 0
            async with a.Session() as s:
                assert (await s.execute(select(InvestorDevice))).scalars().all() == []
    try:
        run(go())
    finally:
        outbox.use_transport(None)


def test_closing_an_account_forgets_its_phones(monkeypatch):
    monkeypatch.setenv("EMAIL_MODE", "log")
    monkeypatch.setenv("PUSH_MODE", "log")
    from test_investor_portal import D

    async def go():
        async with App() as a:
            iid, sess = await a.onboard(deposit=D("1000"))
            await a.inv("POST", "/devices", sess, {"push_token": TOKEN})
            await a.admin("POST", f"/{iid}/closure/request", {})
            r = await a.admin("POST", f"/{iid}/closure/approve", {"payment_reference": "T"})
            assert r.status_code == 200, r.text
            async with a.Session() as s:
                assert (await s.execute(select(InvestorDevice))).scalars().all() == []
    run(go())
