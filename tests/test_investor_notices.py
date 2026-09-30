"""
Notifications: the feed (the bell), phone and browser pushes, live trades.

  * every investor event lands in their own feed, pushes follow their settings
  * a published trade says profit or loss; the feed carries their own share
  * a newly opened trade is announced once, to investors with money in, with
    market and side only; an old one is not announced
  * the bell's unread count, marking read, and browser subscriptions
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
from decimal import Decimal

from backend.data.models import Trade
from backend.investor import live as livemod
from backend.notify import outbox
from test_investor_portal import App
from test_investor_split import _publish

D = Decimal


def run(coro):
    return asyncio.run(coro)


def _capture(monkeypatch):
    sent = []

    async def phone(investor_id, title, body, data=None):
        sent.append(("phone", investor_id, title, (data or {}).get("link")))
        return 1

    async def browser(investor_id, title, body, data=None):
        sent.append(("web", investor_id, title, (data or {}).get("link")))
        return 1

    monkeypatch.setattr(outbox, "push", phone)
    monkeypatch.setattr(outbox, "web_push", browser)
    return sent


def test_a_published_trade_reaches_the_feed_phone_and_browser(monkeypatch):
    monkeypatch.setenv("EMAIL_MODE", "log")
    sent = _capture(monkeypatch)

    async def go():
        async with App() as a:
            ada_id, ada = await a.onboard("Ada", deposit=D("1000"), on=date(2026, 9, 1))
            bea_id, bea = await a.onboard("Bea", deposit=D("3000"), on=date(2026, 9, 1))
            await a.inv("PUT", "/preferences", bea, {"push": {"trades": False}})
            sent.clear()
            await _publish(a, 700, -80.0, datetime(2026, 9, 20, 15))
            await outbox.drain()
            assert {(k, i) for k, i, *_ in sent} == {("phone", ada_id), ("web", ada_id)}
            assert all(t.startswith("Loss on XAUUSD buy") and "$" not in t for *_, t, _l in sent)

            for tok, share in ((ada, "-$20.00"), (bea, "-$60.00")):
                feed = (await a.inv("GET", "/notifications", tok)).json()
                top = feed["items"][0]
                assert top["kind"] == "trade_published" and top["link"] == "/trades"
                assert share in top["body"], "their own share, in the feed, behind sign-in"
            assert "-$20.00" not in str((await a.inv("GET", "/notifications", bea)).json())

            # and by email, unless switched off (Bea turns trade emails off)
            from sqlalchemy import select

            from backend.investor.models import EmailLog
            await a.inv("PUT", "/preferences", bea, {"email": {"trades": False}})
            await _publish(a, 701, 30.0, datetime(2026, 9, 21, 15))
            async with a.Session() as s:
                logs = (await s.execute(select(EmailLog.to_address, EmailLog.subject)
                                        .where(EmailLog.kind == "trade_published"))).all()
            to = [x for x, _ in logs]
            assert to.count("ada@x.com") == 2 and to.count("bea@x.com") == 1
            assert any(subj.startswith("Profit on XAUUSD buy") for _, subj in logs)
    run(go())


def test_money_events_and_the_bell(monkeypatch):
    monkeypatch.setenv("EMAIL_MODE", "log")
    sent = _capture(monkeypatch)

    async def go():
        async with App() as a:
            ada_id, ada = await a.onboard("Ada", deposit=D("3000"), on=date(2026, 9, 1))
            r = await a.inv("POST", "/deposits", ada, {"amount": "500"})
            assert r.status_code == 201, r.text
            await outbox.drain()
            feed = (await a.inv("GET", "/notifications", ada)).json()
            kinds = [n["kind"] for n in feed["items"]]
            assert "deposit_claimed" in kinds and "deposit_confirmed" in kinds
            assert feed["unread"] == len(feed["items"])
            assert any(k == "phone" and t.startswith("Watching for your $500.00") for k, _, t, _l in sent)

            one = feed["items"][0]["id"]
            assert (await a.inv("POST", "/notifications/read", ada, {"ids": [one]})).json()["marked"] == 1
            assert (await a.inv("GET", "/notifications", ada)).json()["unread"] == feed["unread"] - 1
            await a.inv("POST", "/notifications/read", ada, {})
            assert (await a.inv("GET", "/notifications", ada)).json()["unread"] == 0

            # another investor's feed is their own
            _, bo = await a.onboard("Bo")
            assert (await a.inv("GET", "/notifications", bo)).json()["items"] == [] or all(
                n["kind"] != "deposit_claimed" for n in (await a.inv("GET", "/notifications", bo)).json()["items"])
    run(go())


def test_browser_subscriptions(monkeypatch):
    monkeypatch.setenv("EMAIL_MODE", "log")

    async def go():
        async with App() as a:
            _, ada = await a.onboard("Ada", deposit=D("1000"))
            sub = {"endpoint": "https://push.example/abc123", "keys": {"p256dh": "k", "auth": "a"}}
            assert (await a.inv("POST", "/webpush", ada, sub)).status_code == 201
            assert (await a.inv("POST", "/webpush", ada, sub)).status_code == 201, "idempotent"
            assert (await a.inv("POST", "/webpush", ada, {"endpoint": sub["endpoint"], "keys": {}})).status_code == 400
            r = await a.inv("DELETE", "/webpush?endpoint=https://push.example/abc123", ada)
            assert r.json() == {"subscribed": False}
            assert "public_key" in (await a.inv("GET", "/webpush/key", ada)).json()
    run(go())


def test_a_live_trade_is_announced_once_to_investors_with_money_in(monkeypatch):
    monkeypatch.setenv("EMAIL_MODE", "log")
    sent = _capture(monkeypatch)

    async def go():
        async with App() as a:
            ada_id, ada = await a.onboard("Ada", deposit=D("1000"), on=date(2026, 9, 1))
            bea_id, bea = await a.onboard("Bea", deposit=D("1000"), on=date(2026, 9, 1))
            await a.inv("PUT", "/preferences", bea, {"push": {"live_trades": False}})
            _, cy = await a.onboard("Cy")                        # no money in
            now = datetime.utcnow()
            async with a.Session() as s:
                s.add(Trade(id=800, user_id="op", strategy_id="SECRET_v9", symbol="EURUSD", direction="SELL",
                            status="OPEN", entry_time=now - timedelta(minutes=5), volume=1.5, entry_price=1.1))
                s.add(Trade(id=801, user_id="op", strategy_id="SECRET_v9", symbol="GBPUSD", direction="BUY",
                            status="OPEN", entry_time=now - timedelta(hours=9)))
                await s.commit()
            sent.clear()
            async with a.Session() as s:
                assert await livemod.announce_new(s) == 1, "only the fresh one"
                await s.commit()
            await outbox.drain()
            async with a.Session() as s:
                assert await livemod.announce_new(s) == 0, "each trade once"
            assert {(k, i) for k, i, *_ in sent} == {("phone", ada_id), ("web", ada_id)}
            assert all(t == "Trade live: EURUSD sell" for *_, t, _l in sent)
            assert (await a.inv("GET", "/notifications", bea)).json()["items"][0]["kind"] == "trade_opened", \
                "in the feed even with the push switched off"
            assert all(n["kind"] != "trade_opened" for n in (await a.inv("GET", "/notifications", cy)).json()["items"])

            live = (await a.inv("GET", "/live", ada)).json()
            assert live["invested"] and {t["symbol"] for t in live["trades"]} == {"EURUSD", "GBPUSD"}
            assert set(live["trades"][0]) == {"id", "symbol", "direction", "side", "opened_at"}
            assert "SECRET" not in str(live)
            assert (await a.inv("GET", "/live", cy)).json() == {"invested": False, "trades": []}

            monkeypatch.setenv("INVESTOR_LIVE_TRADE_ALERTS", "off")
            async with a.Session() as s:
                s.add(Trade(id=802, user_id="op", symbol="XAUUSD", direction="BUY", status="OPEN",
                            entry_time=datetime.utcnow()))
                await s.commit()
                assert await livemod.announce_new(s) == 0
    run(go())
