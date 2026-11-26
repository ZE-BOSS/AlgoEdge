"""
Signal groups: router, outbox threading, scheduler, join flow, moderation,
admin commands, expiry, content alerts, the trading-code feeds, the API and
the VVIP pieces. Telegram is a fake that records every call.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.data.database import Base
import backend.groups.models  # noqa: F401
from backend.groups import settings as S
from backend.groups import templates as T
from backend.groups import writer as W
from backend.groups.botapi import BotAPIError
from backend.groups.bot import GroupBot, claim_code
from backend.groups.models import (ClientAccount, GroupMember, GroupPost, GroupSignal, LLMDraft, Payment,
                                   SignalGroup, SignalRoute)
from backend.groups.router import GroupRouter

TOKEN = "123:TEST"


class FakeAPI:
    def __init__(self):
        self.token = TOKEN
        self.calls: list[tuple] = []
        self.next_id = 100
        self.fail_send: set[int] = set()
        self.members: dict[tuple[int, int], str] = {}

    configured = True

    def _mid(self):
        self.next_id += 1
        return self.next_id

    async def send(self, chat_id, text, reply_to=None, buttons=None, silent=False):
        if chat_id in self.fail_send:
            raise BotAPIError("sendMessage", 403, "Forbidden: bot was blocked by the user")
        mid = self._mid()
        self.calls.append(("send", chat_id, text, reply_to, buttons, mid))
        return {"message_id": mid}

    async def edit(self, chat_id, message_id, text, buttons=None):
        self.calls.append(("edit", chat_id, message_id, text))
        return {}

    async def delete(self, chat_id, message_id):
        self.calls.append(("delete", chat_id, message_id))

    async def pin(self, chat_id, message_id):
        self.calls.append(("pin", chat_id, message_id))

    async def invite_link(self, chat_id, name, hours=24):
        self.calls.append(("invite", chat_id, name))
        return f"https://t.me/+inv{chat_id}"

    async def remove_member(self, chat_id, uid):
        self.calls.append(("remove", chat_id, uid))

    async def answer_callback(self, cid, text=None):
        self.calls.append(("answer", cid, text))

    async def chat_member(self, chat_id, uid):
        return {"status": self.members.get((chat_id, uid), "member")}

    async def restrict(self, chat_id, uid, minutes):
        self.calls.append(("restrict", chat_id, uid, minutes))

    async def close(self):
        pass

    def sent(self, chat_id=None):
        return [c for c in self.calls if c[0] == "send" and (chat_id is None or c[1] == chat_id)]


GEN, VIP, VVIP_CHAT, ADMIN = -1001, -1002, -1003, 777


@pytest.fixture
def env(monkeypatch):
    monkeypatch.delenv("GROUP_ADMIN_IDS", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    sf = async_sessionmaker(eng, expire_on_commit=False)

    async def setup():
        async with eng.begin() as c:
            await c.run_sync(Base.metadata.create_all)
        async with sf() as s:
            for cid, tier in ((GEN, "GENERAL"), (VIP, "VIP"), (VVIP_CHAT, "VVIP")):
                s.add(SignalGroup(chat_id=cid, title=tier.title(), chat_type="supergroup", tier=tier, posting=True,
                                  bot_status="administrator", can_post=True, can_invite=True, can_delete=True))
            await S.save(s, {"admin_ids": [ADMIN]})
            await s.commit()
    run(setup())
    api = FakeAPI()
    router = GroupRouter(sf)
    bot = GroupBot(api, sf)
    import backend.groups.bot as botmod
    monkeypatch.setattr(botmod, "group_router", router)
    bot.username, bot.bot_id = "zemirah_bot", 999
    return {"sf": sf, "api": api, "router": router, "bot": bot}


def run(coro):
    return _loop().run_until_complete(coro)


_LOOP = None


def _loop():
    global _LOOP
    if _LOOP is None or _LOOP.is_closed():
        _LOOP = asyncio.new_event_loop()
    return _LOOP


async def _gid(sf, chat):
    async with sf() as s:
        return (await s.execute(select(SignalGroup.id).where(SignalGroup.chat_id == chat))).scalar_one()


async def _route(sf, chat, source_type="strategy", key="RQ_NYORB", **kw):
    gid = await _gid(sf, chat)
    async with sf() as s:
        r = SignalRoute(group_id=gid, source_type=source_type, source_key=key, **kw)
        s.add(r)
        await s.commit()
        return r.id


async def _all(sf, model):
    async with sf() as s:
        return (await s.execute(select(model).order_by(model.id))).scalars().all()


def _backdate(sf, model, **fields):
    async def go():
        async with sf() as s:
            for o in (await s.execute(select(model))).scalars().all():
                for k, v in fields.items():
                    setattr(o, k, v)
            await s.commit()
    run(go())


# ── templates and writer ──────────────────────────────────────────────────────

SOURCE_NAMES = ("RQ_NYORB", "RQ_SigmaBreakout", "Easy Forex", "XAUUSD PROFIT", "Gold Pips", "strategy", "channel")


def test_templates_never_name_a_source():
    texts = [T.entry("XAUUSDm", "BUY", 2650.5, 2640.0, [2660.0, 2670.0], chase_limit=2651.8),
             T.entry("EURUSD", "SELL", None, 1.09, [1.08], zone=(1.0850, 1.0860)),
             T.headsup("XAUUSD", "BUY", 2655.0, "above", 2645.0), T.tp_hit(1, 2660.0, 1.0, move_be=True),
             T.closed(-1.0, "stop hit"), T.cancelled("XAUUSD", "entry never reached"), T.withdrawn("XAUUSD", "BUY"),
             T.day_summary([{"symbol": "XAUUSD", "side": "BUY", "status": "CLOSED", "result_r": 2.0}], "Mon"),
             T.RISK_PIN]
    for t in texts:
        for n in SOURCE_NAMES:
            assert n.lower() not in t.lower(), (n, t)


def test_entry_shows_chase_limit_and_risk_line():
    t = T.entry("XAUUSDm", "BUY", 2650.0, 2640.0, [2660.0], chase_limit=2651.33)
    assert "GOLD" in t and "2,650.00" in t and "2,640.00" in t and "2,660.00" in t
    assert "2,651.33" in t and "1-2%" in t


def test_writer_rejects_promises_and_source_words():
    assert W.clean("Guaranteed profit today, 100% win rate!") is None
    assert W.clean("This signal comes from our copy trading channel") is None
    assert W.clean("Good morning, traders. Gold is quiet ahead of CPI - patience pays.")


def test_writer_falls_back_without_keys():
    text, why = run(W.write(S.DEFAULTS, "Write a good-morning post", {"date": "Monday"}))
    assert text is None
    assert W.fallback("morning", {"date": "Monday 1 Dec", "signals_today": []})


# ── router ────────────────────────────────────────────────────────────────────

def test_open_posts_to_matching_groups_only(env):
    sf, router = env["sf"], env["router"]
    run(_route(sf, GEN))
    run(_route(sf, VIP))
    run(_route(sf, VIP, key="OTHER"))
    n = run(router.on_open("strategy", "RQ_NYORB", "grp:1", "XAUUSDm", "BUY", 2650.0, 2640.0, [2660.0, 2670.0]))
    assert n == 2
    posts = run(_all(sf, GroupPost))
    assert sorted(p.chat_id for p in posts) == sorted([GEN, VIP])
    assert all("RQ_NYORB" not in p.text for p in posts)


def test_posting_off_or_paused_route_gets_nothing(env):
    sf, router = env["sf"], env["router"]
    run(_route(sf, GEN, paused_reason="5 losing signals in a row"))
    run(_route(sf, VIP))

    async def off():
        async with sf() as s:
            g = (await s.execute(select(SignalGroup).where(SignalGroup.chat_id == VIP))).scalar_one()
            g.posting = False
            await s.commit()
    run(off())
    assert run(router.on_open("strategy", "RQ_NYORB", "grp:1", "XAUUSD", "BUY", 1.0, 0.9, [1.1])) == 0


def test_symbol_filter_and_sampling(env):
    sf, router = env["sf"], env["router"]
    run(_route(sf, GEN, symbol="XAUUSD", sample_every=2))
    results = [run(router.on_open("strategy", "RQ_NYORB", f"grp:{i}", sym, "BUY", 2600.0 + i * 50, 2590.0, [2700.0]))
               for i, sym in enumerate(["XAUUSDm", "EURUSD", "XAUUSDm", "XAUUSDm", "XAUUSDm"])]
    # EURUSD filtered; of 4 gold signals every 2nd -> 1st and 3rd
    assert results == [1, 0, 0, 1, 0]


def test_delay_and_dedup(env):
    sf, router = env["sf"], env["router"]
    run(_route(sf, GEN, delay_s=300))
    run(_route(sf, GEN, source_type="channel", key="55"))
    assert run(router.on_open("strategy", "RQ_NYORB", "grp:1", "XAUUSD", "BUY", 2650.0, 2640.0, [2660.0])) == 1
    # the same trade from a channel a minute later -> not posted twice
    assert run(router.on_open("channel", "55", "ct:9", "XAUUSDm", "BUY", 2651.0, 2641.0, [2661.0])) == 0
    # opposite side is a different signal
    assert run(router.on_open("channel", "55", "ct:10", "XAUUSD", "SELL", 2651.0, 2661.0, [2641.0])) == 1
    p = run(_all(sf, GroupPost))[0]
    assert p.send_at > datetime.utcnow() + timedelta(seconds=250)


def test_lifecycle_events_queue_replies_and_status_edits(env):
    sf, router = env["sf"], env["router"]
    run(_route(sf, VIP))
    run(router.on_open("strategy", "RQ_NYORB", "grp:7", "XAUUSD", "BUY", 2650.0, 2640.0, [2660.0, 2670.0]))
    run(router.on_event("grp:7", {"type": "tp", "n": 1, "price": 2660.0}))
    run(router.on_event("grp:7", {"type": "tp", "n": 1, "price": 2660.0}))   # repeat ignored
    run(router.on_event("grp:7", {"type": "be"}))
    run(router.on_event("grp:7", {"type": "closed", "price": 2670.0, "how": "target hit"}))
    kinds = [p.kind for p in run(_all(sf, GroupPost))]
    assert kinds == ["signal", "tp", "edit", "be", "edit", "closed", "edit"]
    g = run(_all(sf, GroupSignal))[0]
    assert g.status == "CLOSED" and g.result_r == pytest.approx(2.0) and g.tps_hit == [1]
    # nothing more once closed
    run(router.on_event("grp:7", {"type": "tp", "n": 2, "price": 2670.0}))
    assert len(run(_all(sf, GroupPost))) == 7


def test_basic_lifecycle_only_posts_the_result(env):
    sf, router = env["sf"], env["router"]
    run(_route(sf, GEN, lifecycle="basic"))
    run(router.on_open("strategy", "RQ_NYORB", "grp:7", "XAUUSD", "BUY", 2650.0, 2640.0, [2660.0, 2670.0]))
    run(router.on_event("grp:7", {"type": "tp", "n": 1, "price": 2660.0}))
    run(router.on_event("grp:7", {"type": "closed", "r": 1.5}))
    assert [p.kind for p in run(_all(sf, GroupPost))] == ["signal", "closed", "edit"]


def test_headsup_then_signal_threads_and_withdrawn(env):
    sf, router, bot, api = env["sf"], env["router"], env["bot"], env["api"]
    run(_route(sf, VIP))
    assert run(router.on_headsup("strategy", "RQ_NYORB", "hu:slot1", "XAUUSD", "BUY", 2655.0, "above", 2645.0)) == 1
    assert run(router.on_headsup("strategy", "RQ_NYORB", "hu:slot1", "XAUUSD", "BUY", 2655.0, "above", 2645.0)) == 0
    run(bot.flush_outbox())
    hu_mid = api.sent(VIP)[0][5]
    run(router.on_open("strategy", "RQ_NYORB", "grp:3", "XAUUSD", "BUY", 2655.5, 2645.0, [2670.0]))
    run(bot.flush_outbox())
    sig_send = api.sent(VIP)[1]
    assert sig_send[3] == hu_mid and "heads-up above" in sig_send[2]
    # a second heads-up that is withdrawn
    run(router.on_headsup("strategy", "RQ_NYORB", "hu:slot2", "XAUUSD", "SELL", 2600.0, "below", 2610.0))
    run(router.on_withdrawn("hu:slot2"))
    run(bot.flush_outbox())
    withdrawn = api.sent(VIP)[-1]
    assert "withdrawn" in withdrawn[2].lower() or "no trade" in withdrawn[2].lower()
    assert withdrawn[3] == api.sent(VIP)[-2][5]


def test_losing_streak_pauses_route_and_tells_admin(env):
    sf, router = env["sf"], env["router"]
    told = []

    async def note(t):
        told.append(t)
    router.admin_notify = note
    rid = run(_route(sf, GEN, max_losing_streak=3))
    for i in range(3):
        run(router.on_open("strategy", "RQ_NYORB", f"grp:{i}", "XAUUSD", "BUY", 2000.0 + i * 100, 1990.0 + i * 100, [2050.0 + i * 100]))
        run(router.on_event(f"grp:{i}", {"type": "closed", "r": -1.0}))

    async def route():
        async with sf() as s:
            return await s.get(SignalRoute, rid)
    assert "3 losing" in run(route()).paused_reason
    assert told and "paused" in told[0]
    assert run(router.on_open("strategy", "RQ_NYORB", "grp:9", "XAUUSD", "BUY", 3000.0, 2990.0, [3050.0])) == 0


def test_router_never_raises(env):
    class Boom:
        def __call__(self):
            raise RuntimeError("db down")
    r = GroupRouter(Boom())
    assert run(r.on_open("strategy", "x", "k", "XAUUSD", "BUY", 1.0, 0.9, [1.1])) == 0
    run(r.on_event("k", {"type": "tp"}))
    run(r.on_withdrawn("k"))


# ── outbox ────────────────────────────────────────────────────────────────────

def test_outbox_threads_replies_and_edits_status(env):
    sf, router, bot, api = env["sf"], env["router"], env["bot"], env["api"]
    run(_route(sf, VIP))
    run(router.on_open("strategy", "RQ_NYORB", "grp:1", "XAUUSD", "BUY", 2650.0, 2640.0, [2660.0, 2670.0]))
    run(router.on_event("grp:1", {"type": "tp", "n": 1, "price": 2660.0}))
    run(bot.flush_outbox())
    sends = api.sent(VIP)
    root = sends[0][5]
    assert sends[1][3] == root                                 # TP1 is a reply to the signal
    edits = [c for c in api.calls if c[0] == "edit"]
    assert edits and edits[0][2] == root and "TP1 hit" in edits[0][3]
    g = run(_all(sf, GroupSignal))[0]
    assert g.status == "OPEN" and g.message_id == root
    assert all(p.status == "SENT" for p in run(_all(sf, GroupPost)))


def test_delayed_signal_holds_its_replies(env):
    sf, router, bot, api = env["sf"], env["router"], env["bot"], env["api"]
    run(_route(sf, GEN, delay_s=600))
    run(router.on_open("strategy", "RQ_NYORB", "grp:1", "XAUUSD", "BUY", 2650.0, 2640.0, [2660.0, 2670.0]))
    run(router.on_event("grp:1", {"type": "tp", "n": 1, "price": 2660.0}))
    run(bot.flush_outbox())
    assert api.sent(GEN) == []
    _backdate(sf, GroupPost, send_at=datetime.utcnow() - timedelta(seconds=1))
    run(bot.flush_outbox())
    s = api.sent(GEN)
    assert len(s) == 2 and s[1][3] == s[0][5]


def test_failed_send_is_marked_not_retried_forever(env):
    sf, router, bot, api = env["sf"], env["router"], env["bot"], env["api"]
    run(_route(sf, GEN))
    api.fail_send.add(GEN)
    run(router.on_open("strategy", "RQ_NYORB", "grp:1", "XAUUSD", "BUY", 2650.0, 2640.0, [2660.0]))
    run(bot.flush_outbox())
    p = run(_all(sf, GroupPost))[0]
    assert p.status == "FAILED" and "blocked" in p.error
    assert run(bot.flush_outbox()) == 0


# ── updates: groups, admins, join flow ────────────────────────────────────────

def test_added_to_group_is_recorded_and_admin_told(env):
    sf, bot, api = env["sf"], env["bot"], env["api"]
    run(bot.handle_update({"update_id": 1, "my_chat_member": {
        "chat": {"id": -1003622850842, "title": "AlphaEdge Collectives (VIP)", "type": "supergroup"},
        "new_chat_member": {"status": "administrator", "can_invite_users": True, "can_delete_messages": False}}}))
    g = [x for x in run(_all(sf, SignalGroup)) if x.chat_id == -1003622850842][0]
    assert g.can_post and g.can_invite and not g.can_delete and not g.posting and g.tier == ""
    dm = api.sent(ADMIN)[-1][2]
    assert "AlphaEdge Collectives" in dm and "delete messages" in dm and "tier" in dm
    run(bot.handle_update({"update_id": 2, "my_chat_member": {
        "chat": {"id": -1003622850842, "title": "AlphaEdge Collectives (VIP)", "type": "supergroup"},
        "new_chat_member": {"status": "left"}}}))
    assert "removed" in api.sent(ADMIN)[-1][2]


def test_claim_code_makes_an_admin(env):
    sf, bot, api = env["sf"], env["bot"], env["api"]
    run(bot.on_private({"from": {"id": 4242}, "chat": {"type": "private"}, "text": "/claim WRONG"}))
    assert "isn't right" in api.sent(4242)[-1][2]
    run(bot.on_private({"from": {"id": 4242}, "chat": {"type": "private"}, "text": f"/claim {claim_code(TOKEN).lower()}"}))

    async def ids():
        async with sf() as s:
            return (await S.load(s))["admin_ids"]
    assert 4242 in run(ids()) and ADMIN in run(ids())


def test_admin_commands(env):
    sf, bot, api = env["sf"], env["bot"], env["api"]
    run(bot.on_private({"from": {"id": ADMIN}, "chat": {"type": "private"}, "text": "/status"}))
    assert "GENERAL: General - posting on" in api.sent(ADMIN)[-1][2]
    run(bot.on_private({"from": {"id": ADMIN}, "chat": {"type": "private"}, "text": "/pause general"}))
    assert "posting off" in api.sent(ADMIN)[-1][2]
    run(bot.on_private({"from": {"id": ADMIN}, "chat": {"type": "private"}, "text": "/post vip Market is closed <today>"}))
    p = run(_all(sf, GroupPost))[-1]
    assert p.chat_id == VIP and "&lt;today&gt;" in p.text
    run(bot.on_private({"from": {"id": ADMIN}, "chat": {"type": "private"}, "text": "/reply 4242 hello"}))
    assert api.sent(4242)[-1][2].endswith("hello")
    # a non-admin's slash command is treated as the join chat, not a command
    run(bot.on_private({"from": {"id": 5}, "chat": {"type": "private"}, "text": "/pause vip"}))
    assert "/start" in api.sent(5)[-1][2]


def test_vip_join_flow_end_to_end(env):
    sf, bot, api = env["sf"], env["bot"], env["api"]
    frm = {"id": 31337, "first_name": "Ada", "username": "ada"}
    run(bot.on_private({"from": frm, "chat": {"type": "private"}, "text": "/start"}))
    assert api.sent(31337)[-1][4]                                  # menu buttons
    run(bot.on_callback({"id": "c1", "from": frm, "data": "join:vip"}))
    assert "MT5 login" in api.sent(31337)[-1][2]
    run(bot.on_private({"from": frm, "chat": {"type": "private"}, "text": "my login is abc"}))
    assert "doesn't look like" in api.sent(31337)[-1][2]
    run(bot.on_private({"from": frm, "chat": {"type": "private"}, "text": "12345678"}))
    m = run(_all(sf, GroupMember))[0]
    assert m.status == "PENDING" and m.mt5_login == "12345678"
    req = api.sent(ADMIN)[-1]
    assert "12345678" in req[2] and req[4][0][0]["callback_data"] == f"m:ap:{m.id}"
    # a non-admin can't press approve
    run(bot.on_callback({"id": "c2", "from": frm, "data": f"m:ap:{m.id}"}))
    assert ("answer", "c2", "Admins only") in api.calls
    run(bot.on_callback({"id": "c3", "from": {"id": ADMIN}, "data": f"m:ap:{m.id}",
                         "message": {"message_id": req[5], "chat": {"id": ADMIN}, "text": "VIP request"}}))
    m = run(_all(sf, GroupMember))[0]
    assert m.status == "ACTIVE" and m.tier == "VIP" and m.expires_at > datetime.utcnow() + timedelta(days=29)
    assert ("invite", VIP, f"VIP {m.id}") in api.calls
    assert "t.me/+inv" in api.sent(31337)[-1][2]


def test_talk_to_admin_relay(env):
    bot, api = env["bot"], env["api"]
    frm = {"id": 8080, "first_name": "Bo"}
    run(bot.on_callback({"id": "c", "from": frm, "data": "join:admin"}))
    run(bot.on_private({"from": frm, "chat": {"type": "private"}, "text": "How do I renew?"}))
    relay = api.sent(ADMIN)[-1][2]
    assert "How do I renew?" in relay and "/reply 8080" in relay


# ── moderation and FAQ ────────────────────────────────────────────────────────

def _gmsg(uid, text, mid=1, **kw):
    return {"message_id": mid, "chat": {"id": GEN, "type": "supergroup"}, "from": {"id": uid}, "text": text, **kw}


def test_moderation_deletes_links_and_restricts_on_third_strike(env):
    sf, bot, api = env["sf"], env["bot"], env["api"]

    async def on():
        async with sf() as s:
            await S.save(s, {"moderate": True})
            await s.commit()
    run(on())
    for i in range(3):
        run(bot.on_group_message(_gmsg(55, f"join my signals t.me/scam{i}", mid=i + 1)))
    assert [c for c in api.calls if c[0] == "delete"] == [("delete", GEN, 1), ("delete", GEN, 2), ("delete", GEN, 3)]
    assert ("restrict", GEN, 55, 60) in api.calls
    run(bot.on_group_message(_gmsg(56, "call me +234 803 123 4567", mid=10)))
    run(bot.on_group_message(_gmsg(57, "DM me for account management", mid=11)))
    assert ("delete", GEN, 10) in api.calls and ("delete", GEN, 11) in api.calls
    # chat admins and ordinary messages are left alone
    api.members[(GEN, 60)] = "administrator"
    run(bot.on_group_message(_gmsg(60, "new video: https://youtube.com/x", mid=12)))
    run(bot.on_group_message(_gmsg(61, "nice trade everyone", mid=13)))
    assert ("delete", GEN, 12) not in api.calls and ("delete", GEN, 13) not in api.calls


def test_faq_and_money_questions(env):
    sf, bot, api = env["sf"], env["bot"], env["api"]

    async def cfg():
        async with sf() as s:
            await S.save(s, {"answer_faq": True, "admin_contact": "@zemirah",
                             "faq": [{"q": "lot size", "a": "Risk 1-2% per trade; size from your stop."}]})
            await s.commit()
    run(cfg())
    run(bot.on_group_message(_gmsg(70, "what lot size should I use?", mid=20)))
    assert api.sent(GEN)[-1][2].startswith("Risk 1-2%") and api.sent(GEN)[-1][3] == 20
    run(bot.on_group_message(_gmsg(71, "how do I withdraw my money?", mid=21)))
    assert "@zemirah" in api.sent(GEN)[-1][2]
    n = len(api.sent(GEN))
    run(bot.on_group_message(_gmsg(72, "lot size is 0.01 for me", mid=22)))     # not a question
    assert len(api.sent(GEN)) == n


# ── scheduler, drafts, maintenance ────────────────────────────────────────────

LAGOS = ZoneInfo("Africa/Lagos")


def test_scheduler_fires_once_per_day_and_skips_weekends(env):
    bot, api, sf = env["bot"], env["api"], env["sf"]
    tue = datetime(2026, 11, 24, 7, 35, tzinfo=LAGOS)
    assert run(bot.scheduler_tick(tue)) == ["morning"]
    assert run(bot.scheduler_tick(tue + timedelta(minutes=5))) == []
    assert run(bot.scheduler_tick(datetime(2026, 11, 24, 8, 30, tzinfo=LAGOS))) == []   # >20 min late
    sat = datetime(2026, 11, 28, 7, 35, tzinfo=LAGOS)
    assert run(bot.scheduler_tick(sat)) == []
    assert run(bot.scheduler_tick(datetime(2026, 11, 28, 11, 5, tzinfo=LAGOS))) == ["education"]
    # morning is an LLM kind -> a draft for the admin with buttons, nothing in the groups yet
    d = run(_all(sf, LLMDraft))[0]
    assert d.kind == "morning" and d.status == "PENDING"
    assert api.sent(ADMIN)[0][4][0][0]["callback_data"] == f"d:ap:{d.id}"
    assert api.sent(GEN) == []


def test_draft_approve_posts_to_general_and_vip(env):
    bot, api, sf = env["bot"], env["api"], env["sf"]
    run(bot.scheduler_tick(datetime(2026, 11, 24, 7, 31, tzinfo=LAGOS)))
    d = run(_all(sf, LLMDraft))[0]
    assert run(bot.decide_draft(d.id, "ap")) == "Posted to 2 group(s)"
    assert run(bot.decide_draft(d.id, "ap")) == "Already handled"
    run(bot.flush_outbox())
    assert len(api.sent(GEN)) == 1 and len(api.sent(VIP)) == 1 and api.sent(VVIP_CHAT) == []


def test_summary_is_automatic_template(env):
    bot, api, sf, router = env["bot"], env["api"], env["sf"], env["router"]
    run(_route(sf, VIP))
    run(router.on_open("strategy", "RQ_NYORB", "grp:1", "XAUUSD", "BUY", 2650.0, 2640.0, [2670.0]))
    run(router.on_event("grp:1", {"type": "closed", "r": 2.0}))
    now = datetime.now(LAGOS).replace(hour=21, minute=20)
    if now.weekday() >= 5:
        now -= timedelta(days=now.weekday() - 4)
    assert "summary" in run(bot.scheduler_tick(now))
    posts = [p for p in run(_all(sf, GroupPost)) if p.kind == "summary"]
    assert {p.chat_id for p in posts} == {GEN, VIP} and run(_all(sf, LLMDraft)) == []


def test_expiry_warns_then_removes(env):
    bot, api, sf = env["bot"], env["api"], env["sf"]

    async def add():
        async with sf() as s:
            s.add(GroupMember(telegram_id=11, tier="VIP", status="ACTIVE", expires_at=datetime.utcnow() + timedelta(days=3)))
            s.add(GroupMember(telegram_id=12, tier="VIP", status="ACTIVE", expires_at=datetime.utcnow() - timedelta(hours=1)))
            await s.commit()
    run(add())
    run(bot.expire_members())
    st = {m.telegram_id: m.status for m in run(_all(sf, GroupMember))}
    assert st == {11: "WARNED", 12: "REMOVED"}
    assert ("remove", VIP, 12) in api.calls and "ends on" in api.sent(11)[-1][2]
    run(bot.expire_members())
    assert len(api.sent(11)) == 1                     # warned only once


def test_content_alert_after_big_win(env):
    bot, api, sf, router = env["bot"], env["api"], env["sf"], env["router"]
    run(_route(sf, VIP))
    run(router.on_open("strategy", "RQ_NYORB", "grp:1", "XAUUSD", "BUY", 2650.0, 2640.0, [2690.0]))
    run(router.on_event("grp:1", {"type": "closed", "r": 4.0}))
    cfg = run(_load(sf))
    sent = run(bot.content_alerts(cfg))
    assert sent and "win" in sent[0]
    msg = api.sent(ADMIN)[-1][2]
    assert "RQ_NYORB" not in msg and ("video" in msg.lower() or "script" in msg.lower())
    assert run(bot.content_alerts(cfg)) == []         # once per moment


async def _load(sf):
    async with sf() as s:
        return await S.load(s)


# ── feeds from the trading code ───────────────────────────────────────────────

def test_feeds_are_inert_without_token(monkeypatch):
    from backend.groups import feeds
    monkeypatch.delenv("GROUP_BOT_TOKEN", raising=False)
    assert not feeds.active()
    feeds.strategy_opened("S", "g1", object(), [])     # no loop, no token: silently nothing


def test_feeds_route_strategy_and_copier_events(env, monkeypatch):
    from types import SimpleNamespace as NS
    from backend.groups import feeds
    import backend.groups.router as rmod
    sf, router = env["sf"], env["router"]
    monkeypatch.setenv("GROUP_BOT_TOKEN", TOKEN)
    monkeypatch.setattr(rmod, "group_router", router)
    run(_route(sf, VIP))
    run(_route(sf, GEN, source_type="channel", key="77"))

    async def go():
        sig = NS(symbol="XAUUSDm", direction="buy", entry_price=2650.0, stop_loss=2640.0)
        feeds.strategy_opened("RQ_NYORB", "g1", sig, [{"tp_price": 2670.0}, {"tp_price": 2660.0}, {"tp_price": None}])
        await asyncio.sleep(0.05)
        trade = NS(group_id="g1", status="OPEN", risk_reward=None)
        feeds.strategy_leg_closed(trade, NS(tp_level=1), "TP1", 2660.0)
        await asyncio.sleep(0.05)
        feeds.strategy_breakeven(trade)
        await asyncio.sleep(0.05)
        trade.status, trade.risk_reward = "CLOSED", 1.5
        feeds.strategy_leg_closed(trade, NS(tp_level=2), "TP2", 2670.0)
        await asyncio.sleep(0.05)
        # copier
        sig = NS(id=500, kind="OPEN", status="EXECUTED", root_signal_id=None,
                 parsed={"symbol": "GOLD", "side": "SELL", "entry": 2600.0, "sl": 2610.0, "tps": [2590.0, 2580.0]})
        feeds.copier_message(sig, NS(id=77), [NS(symbol="XAUUSDm", entry=2600.0, sl=2610.0)])
        await asyncio.sleep(0.05)
        feeds.copier_message(NS(id=501, kind="BREAKEVEN", status="EXECUTED", root_signal_id=500, parsed={}), NS(id=77), [])
        await asyncio.sleep(0.05)
        root = NS(id=500, status="CLOSED")
        rows = [NS(id=1, status="CLOSED", tp=2590.0, leg=1)]
        feeds.copier_round(root, rows, {1: ("OPEN",)}, {1: {"price": 2590.0}})
        await asyncio.sleep(0.05)
    run(go())
    sigs = run(_all(sf, GroupSignal))
    s1 = [g for g in sigs if g.key == "grp:g1"][0]
    assert s1.side == "BUY" and s1.tps == [2660.0, 2670.0] and s1.status == "CLOSED" and s1.result_r == 1.5
    s2 = [g for g in sigs if g.key == "ct:500"][0]
    assert s2.symbol == "XAUUSDm" and s2.side == "SELL" and s2.status == "CLOSED" and s2.tps_hit == [1]
    assert s2.result_r == pytest.approx(1.0)
    kinds = [p.kind for p in run(_all(sf, GroupPost)) if p.chat_id == GEN]
    assert kinds == ["signal", "be", "edit", "tp", "edit", "closed", "edit"]


def test_watch_service_emits_group_events(monkeypatch):
    from backend.services import watch_service as wmod
    from backend.groups import feeds
    got = []
    monkeypatch.setattr(feeds, "headsup", lambda *a: got.append(("hu",) + a))
    monkeypatch.setattr(feeds, "headsup_withdrawn", lambda *a: got.append(("wd",) + a))
    ws = wmod.WatchService()
    ws.evaluate = lambda **kw: (ws.events.append(("headsup", "RQ_NYORB", "s1", "XAUUSD", {"direction": "BUY", "level": 1.0})), [])[1]
    run(ws.after_bar(slot_id="s1"))
    assert got and got[0][0] == "hu" and got[0][1] == "RQ_NYORB" and ws.events == []


# ── VVIP ──────────────────────────────────────────────────────────────────────

def test_vvip_limits_are_clamped_and_levels_gate_features():
    from backend.groups.vvip import can, clamp_limits
    c = clamp_limits(ClientAccount(risk_pct=7.0, daily_loss_pct=0.1, max_dd_pct=90.0, level="GOD"))
    assert (c.risk_pct, c.daily_loss_pct, c.max_dd_pct, c.level) == (2.0, 1.0, 30.0, "STARTER")
    assert can("STARTER", "live") and not can("STARTER", "backtest")
    assert can("PRO", "backtest") and not can("PRO", "orderflow") and can("ELITE", "orderflow")
    assert not can("", "live")


def test_vvip_password_round_trip(monkeypatch):
    from cryptography.fernet import Fernet
    from fastapi import HTTPException
    from backend.config import settings
    from backend.groups.vvip import decrypt, encrypt
    monkeypatch.setattr(settings.security, "encryption_key", "")
    with pytest.raises(HTTPException):
        encrypt("x")                                   # never stored in plain text
    monkeypatch.setattr(settings.security, "encryption_key", Fernet.generate_key().decode())
    tok = encrypt("hunter2")
    assert tok != "hunter2" and decrypt(tok) == "hunter2"


def test_paystack_signature():
    from backend.groups.vvip import paystack_valid
    raw = b'{"event":"charge.success"}'
    sig = hmac.new(b"sk_test", raw, hashlib.sha512).hexdigest()
    assert paystack_valid(raw, sig, "sk_test")
    assert not paystack_valid(raw, sig, "other") and not paystack_valid(raw, None, "sk_test")
    assert not paystack_valid(raw, sig, "")


# ── HTTP: admin API and the payment webhook ───────────────────────────────────

@pytest.fixture
def client(env, monkeypatch):
    import httpx
    from fastapi import FastAPI
    from backend.api.deps import require_admin
    from backend.api.routes import groups as groutes
    from backend.data.database import get_db
    from backend.groups import vvip
    import backend.groups.bot as botmod
    monkeypatch.setattr(botmod, "group_bot", env["bot"])
    app = FastAPI()
    app.include_router(groutes.router)
    app.include_router(vvip.payments)

    async def db():
        async with env["sf"]() as s:
            yield s
    app.dependency_overrides[get_db] = db
    app.dependency_overrides[require_admin] = lambda: object()
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")
    return c


def test_api_groups_routes_and_posting(env, client):
    sf, api = env["sf"], env["api"]

    async def go():
        r = await client.get("/api/groups/status")
        assert r.status_code == 200 and len(r.json()["groups"]) == 3
        gid = r.json()["groups"][0]["id"]
        # a group with no tier can't be switched on
        async with sf() as s:
            s.add(SignalGroup(chat_id=-5228203057, title="new", tier="", can_post=True))
            await s.commit()
        new = [g for g in (await client.get("/api/groups/status")).json()["groups"] if g["chat_id"] == -5228203057][0]
        assert (await client.patch(f"/api/groups/groups/{new['id']}", json={"posting": True})).status_code == 400
        assert (await client.patch(f"/api/groups/groups/{new['id']}", json={"tier": "BOSS"})).status_code == 400
        r = await client.patch(f"/api/groups/groups/{new['id']}", json={"tier": "general", "posting": True})
        assert r.json()["tier"] == "GENERAL" and r.json()["posting"] is True
        r = await client.post("/api/groups/routes", json={"group_id": int(gid), "source_type": "strategy",
                                                         "source_key": "RQ_NYORB", "symbol": "XAUUSD", "sample_every": 2})
        assert r.status_code == 200
        rid = r.json()["id"]
        assert (await client.post("/api/groups/routes", json={"group_id": int(gid), "source_type": "x",
                                                              "source_key": "y"})).status_code == 400
        r = await client.patch(f"/api/groups/routes/{rid}", json={"delay_s": 120, "resume": True})
        assert r.json()["delay_s"] == 120
        r = await client.post("/api/groups/post", json={"group_id": int(gid), "text": "RISK_PIN", "pin": True})
        assert r.json()["pinned"] is True and ("pin", GEN, r.json()["message_id"]) in api.calls
        assert (await client.delete(f"/api/groups/routes/{rid}")).json()["ok"]
        r = await client.put("/api/groups/settings", json={"patch": {"vip_min_balance": 500}})
        assert r.json()["vip_min_balance"] == 500
        assert "posts" in (await client.get("/api/groups/feed")).json()
    run(go())


def test_api_member_and_draft_decisions(env, client):
    sf, api = env["sf"], env["api"]

    async def go():
        async with sf() as s:
            m = GroupMember(telegram_id=9, tier="VIP", status="PENDING", mt5_login="1234567")
            d = LLMDraft(kind="education", text="Patience is a position.", status="PENDING", facts={})
            s.add_all([m, d])
            await s.commit()
        r = await client.post(f"/api/groups/members/{m.id}/approve")
        assert "invite sent" in r.json()["result"]
        r = await client.patch(f"/api/groups/members/{m.id}", json={"days": 90})
        assert r.json()["expires_at"] > (datetime.utcnow() + timedelta(days=89)).isoformat()
        r = await client.post(f"/api/groups/drafts/{d.id}/approve")
        assert r.json()["result"] == "Posted to 2 group(s)"
        assert (await client.post(f"/api/groups/drafts/{d.id}/nope")).status_code == 400
    run(go())


def test_paystack_webhook(env, client, monkeypatch):
    sf, api = env["sf"], env["api"]

    async def go():
        body = {"event": "charge.success", "data": {"reference": "ref1", "amount": 30000, "currency": "USD",
                                                   "metadata": {"telegram_id": 4040, "level": "pro"}}}
        raw = json.dumps(body).encode()
        monkeypatch.delenv("PAYSTACK_SECRET_KEY", raising=False)
        assert (await client.post("/api/vvip/paystack/webhook", content=raw)).status_code == 503
        monkeypatch.setenv("PAYSTACK_SECRET_KEY", "sk_test_x")
        assert (await client.post("/api/vvip/paystack/webhook", content=raw,
                                  headers={"x-paystack-signature": "bad"})).status_code == 401
        async with sf() as s:
            s.add(GroupMember(telegram_id=4040, status="NEW"))
            await s.commit()
        sig = hmac.new(b"sk_test_x", raw, hashlib.sha512).hexdigest()
        r = await client.post("/api/vvip/paystack/webhook", content=raw, headers={"x-paystack-signature": sig})
        assert r.json() == {"ok": True}
        r = await client.post("/api/vvip/paystack/webhook", content=raw, headers={"x-paystack-signature": sig})
        assert r.json().get("duplicate") is True
        m = (await _all(sf, GroupMember))[0]
        assert (m.tier, m.level, m.status) == ("VVIP", "PRO", "ACTIVE")
        assert m.expires_at > datetime.utcnow() + timedelta(days=29)
        p = (await _all(sf, Payment))[0]
        assert p.amount == 300.0 and p.period_days == 30
        assert ("invite", VVIP_CHAT, f"VVIP {m.id}") in api.calls and "welcome to VVIP" in api.sent(4040)[-1][2]
    run(go())


def test_vvip_portal_connect_pause_disconnect(env, monkeypatch):
    import httpx
    from types import SimpleNamespace as NS
    from cryptography.fernet import Fernet
    from fastapi import FastAPI
    from backend.config import settings
    from backend.data.database import get_db
    from backend.groups import vvip
    import backend.groups.bot as botmod
    sf, api = env["sf"], env["api"]
    monkeypatch.setattr(botmod, "group_bot", env["bot"])
    monkeypatch.setattr(settings.security, "encryption_key", Fernet.generate_key().decode())
    app = FastAPI()
    app.include_router(vvip.portal)

    async def db():
        async with sf() as s:
            yield s
    who = {"inv": NS(id="inv-1")}
    app.dependency_overrides[get_db] = db
    app.dependency_overrides[vvip._investor] = lambda: who["inv"]
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")
    body = {"broker": "Exness", "mt5_login": "87654321", "mt5_server": "Exness-MT5Real8", "mt5_password": "pw!",
            "risk_pct": 5, "accept_terms": True}

    async def go():
        assert (await c.get("/api/investor/vvip/me")).json()["level"] == ""
        assert (await c.post("/api/investor/vvip/connect", json=body)).status_code == 403
        async with sf() as s:
            s.add(GroupMember(telegram_id=1, user_id="inv-1", tier="VVIP", level="PRO", status="ACTIVE",
                              expires_at=datetime.utcnow() + timedelta(days=30)))
            await s.commit()
        assert (await c.post("/api/investor/vvip/connect", json={**body, "accept_terms": False})).status_code == 400
        r = await c.post("/api/investor/vvip/connect", json=body)
        assert r.json() == {"ok": True, "state": "NEW"}
        acc = (await _all(sf, ClientAccount))[0]
        assert acc.risk_pct == 2.0 and acc.mt5_password_encrypted and "pw!" not in acc.mt5_password_encrypted
        assert vvip.decrypt(acc.mt5_password_encrypted) == "pw!" and acc.terms_accepted_at
        assert "87654321" in api.sent(ADMIN)[-1][2]
        me = (await c.get("/api/investor/vvip/me")).json()
        assert me["level"] == "PRO" and me["features"]["backtest"] and not me["features"]["orderflow"]
        assert "mt5_password" not in json.dumps(me)
        r = await c.patch("/api/investor/vvip/settings", json={"pause": True, "risk_pct": 0.5})
        assert r.json()["state"] == "PAUSED" and r.json()["risk_pct"] == 0.5
        r = await c.patch("/api/investor/vvip/settings", json={"disconnect": True})
        acc = (await _all(sf, ClientAccount))[0]
        assert acc.state == "STOPPED" and acc.mt5_password_encrypted == ""
    run(go())


# ── VVIP client guard and live <-> client sync ────────────────────────────────

def test_guard_daily_stop_resets_next_day_and_dd_stop_holds():
    from backend.groups.client_guard import GuardState, evaluate
    st = GuardState()
    kw = dict(wanted="ACTIVE", daily_loss_pct=5, max_dd_pct=20)
    evaluate(st, equity=1000, balance=1000, today="d1", **kw)
    assert st.state == "ACTIVE" and st.peak == 1000 and st.day_start == 1000
    evaluate(st, equity=940, balance=960, today="d1", **kw)
    assert st.state == "DAY_STOP" and "daily loss" in st.reason
    evaluate(st, equity=945, balance=945, today="d2", **kw)
    assert st.state == "ACTIVE"
    evaluate(st, equity=790, balance=800, today="d2", **kw)
    assert st.state == "DD_STOP" and "drawdown" in st.reason
    evaluate(st, equity=900, balance=900, today="d3", **kw)
    assert st.state == "DD_STOP", "only the admin resumes after a drawdown stop"
    evaluate(st, equity=900, balance=900, today="d3", **{**kw, "wanted": "PAUSED"})
    assert st.state == "DD_STOP"


def test_guard_pause_and_resume_by_request():
    from backend.groups.client_guard import GuardState, evaluate
    st = GuardState()
    evaluate(st, equity=1000, balance=1000, today="d1", wanted="PAUSED", daily_loss_pct=5, max_dd_pct=20)
    assert st.state == "PAUSED"
    evaluate(st, equity=1000, balance=1000, today="d1", wanted="ACTIVE", daily_loss_pct=5, max_dd_pct=20)
    assert st.state == "ACTIVE"


def test_set_risk_overrides_every_slot():
    from backend.groups.client_guard import set_risk
    cfg = {"risk": {"risk_per_trade_pct": 0.5, "max_risk_hard_cap_pct": 5}, "instrument_slots": [
        {"symbol": "XAUUSDm", "risk_per_trade_pct": 3.0, "risk": {"risk_per_trade_pct": 3.0, "max_risk_hard_cap_pct": 6}},
        {"symbol": "USTECm"}]}
    set_risk(cfg, 1.5, 4)
    assert cfg["risk"] == {"risk_per_trade_pct": 1.5, "max_risk_hard_cap_pct": 2.0, "max_daily_drawdown_pct": 4.0}
    assert all(sl["risk_per_trade_pct"] == 1.5 for sl in cfg["instrument_slots"])
    assert cfg["instrument_slots"][0]["risk"] == {"risk_per_trade_pct": 1.5}


def test_sync_clients_control_status_and_resume(env, tmp_path, monkeypatch):
    from backend.groups.vvip import sync_clients
    sf = env["sf"]
    monkeypatch.setenv("VVIP_SHARED_DIR", str(tmp_path))
    told = []

    async def note(t):
        told.append(t)

    async def go():
        async with sf() as s:
            a = ClientAccount(mt5_login="555", instance="client1", state="ACTIVE", risk_pct=1.0, daily_loss_pct=5, max_dd_pct=20)
            s.add(a)
            await s.commit()
        await sync_clients(sf, note)
        ctl = json.loads((tmp_path / "client1.control.json").read_text())
        assert ctl == {"state": "ACTIVE", "risk_pct": 1.0, "daily_loss_pct": 5.0, "max_dd_pct": 20.0}
        (tmp_path / "client1.status.json").write_text(json.dumps({"ok": True, "state": "DD_STOP", "balance": 790,
            "equity": 780, "drawdown_pct": 22.0, "reason": "drawdown stop: equity 22% below its peak"}))
        await sync_clients(sf, note)
        a = (await _all(sf, ClientAccount))[0]
        assert a.state == "PAUSED" and a.guard_state == "DD_STOP" and a.live["equity"] == 780
        assert told and "555" in told[0] and "drawdown" in told[0]
        assert json.loads((tmp_path / "client1.control.json").read_text())["state"] == "PAUSED"
        # the admin resumes it
        async with sf() as s:
            a = await s.get(ClientAccount, a.id)
            a.state = "ACTIVE"
            await s.commit()
        await sync_clients(sf, note)
        tok = json.loads((tmp_path / "client1.control.json").read_text()).get("resume_token")
        assert tok
        await sync_clients(sf, note)
        assert json.loads((tmp_path / "client1.control.json").read_text())["resume_token"] == tok, "one resume"
        (tmp_path / "client1.status.json").write_text(json.dumps({"ok": True, "state": "ACTIVE", "equity": 780}))
        await sync_clients(sf, note)
        assert "trading again" in told[-1]
        a = (await _all(sf, ClientAccount))[0]
        assert a.paused_reason == ""
    run(go())


def test_portal_pro_backtests_and_elite_orderflow(env, monkeypatch, tmp_path):
    import httpx
    from types import SimpleNamespace as NS
    from fastapi import FastAPI
    from backend.data.database import get_db
    from backend.data.models import BacktestRun, BacktestTrade, User, UserConfigModel
    from backend.groups import vvip
    import backend.groups.bot as botmod
    sf = env["sf"]
    monkeypatch.setattr(botmod, "group_bot", env["bot"])
    monkeypatch.setenv("ORDERFLOW_DIR", str(tmp_path))
    app = FastAPI()
    app.include_router(vvip.portal)

    async def db():
        async with sf() as s:
            yield s
    app.dependency_overrides[get_db] = db
    app.dependency_overrides[vvip._investor] = lambda: NS(id="inv-9")
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")

    async def go():
        async with sf() as s:
            s.add(User(id="admin", email="a@x", password_hash="x", name="A", is_admin=True))
            s.add(UserConfigModel(user_id="admin", config_json=json.dumps({"instrument_slots": [
                {"strategy_id": "RQ_NYORB", "symbol": "XAUUSDm", "enabled": True},
                {"strategy_id": "SECRET", "symbol": "XAUUSDm", "enabled": False}]})))
            s.add(BacktestRun(id="r1", user_id="admin", strategy_id="RQ_NYORB", symbol="XAUUSDm",
                              start_date=datetime(2025, 1, 1), end_date=datetime(2026, 1, 1), params_snapshot="{}",
                              total_trades=3, win_rate=66.7, profit_factor=2.0, expectancy_r=0.67, max_drawdown_pct=4.0))
            for i, r in enumerate((1.0, -1.0, 2.0)):
                s.add(BacktestTrade(backtest_id="r1", symbol="XAUUSDm", exit_time=datetime(2025, 1 + i, 5), pnl_r=r))
            m = GroupMember(telegram_id=9, user_id="inv-9", tier="VVIP", level="STARTER", status="ACTIVE")
            s.add(m)
            await s.commit()
        assert (await c.get("/api/investor/vvip/backtests")).status_code == 403
        async with sf() as s:
            (await s.get(GroupMember, m.id)).level = "PRO"
            await s.commit()
        r = (await c.get("/api/investor/vvip/backtests")).json()
        sysm = r["systems"]
        assert len(sysm) == 1 and sysm[0]["label"] == "GOLD (XAUUSD) - system A" and sysm[0]["total_r"] == 2.0
        assert sysm[0]["curve"] == [1.0, 0.0, 2.0] and sysm[0]["months"]["2025-03"] == 2.0
        assert "RQ_NYORB" not in json.dumps(r) and "SECRET" not in json.dumps(r)
        assert (await c.post("/api/investor/vvip/backtests/request", json={"market": "Gold", "months": 12})).json()["ok"]
        assert "Gold" in env["api"].sent(ADMIN)[-1][2]
        assert (await c.get("/api/investor/vvip/orderflow")).status_code == 403
        async with sf() as s:
            (await s.get(GroupMember, m.id)).level = "ELITE"
            await s.commit()
        (tmp_path / "levels.json").write_text(json.dumps({"updated": 1, "connected": True, "markets": [
            {"name": "Gold futures", "cfd": "XAUUSDm", "future": "GC.v.0", "cfd_levels": {"poc": 2648.0},
             "futures": {"delta": 120, "delta_30m": -5, "volume": 9000, "session": "2026-11-24"}}]}))
        o = (await c.get("/api/investor/vvip/orderflow")).json()
        assert o["live"] and o["markets"][0]["levels"]["poc"] == 2648.0 and "future" not in o["markets"][0]
    run(go())


def test_group_learned_from_first_message_and_tier_command(env):
    sf, bot, api = env["sf"], env["bot"], env["api"]

    async def cm(chat_id, uid):
        return {"status": "administrator", "can_invite_users": True, "can_delete_messages": True}
    api.chat_member = cm
    chat = {"id": -100777, "title": "AlphaEdge Collectives", "type": "supergroup"}
    run(bot.on_group_message({"message_id": 5, "chat": chat, "from": {"id": 42}, "text": "hello all"}))
    g = [x for x in run(_all(sf, SignalGroup)) if x.chat_id == -100777][0]
    assert g.can_post and g.tier == "" and not g.posting
    run(bot.on_group_message({"message_id": 6, "chat": chat, "from": {"id": ADMIN}, "text": "/tier general"}))
    g = [x for x in run(_all(sf, SignalGroup)) if x.chat_id == -100777][0]
    assert g.tier == "GENERAL" and g.posting
    assert ("delete", -100777, 6) in api.calls and "tier GENERAL, posting on" in api.sent(ADMIN)[-1][2]
    # a non-admin can't set it
    run(bot.on_group_message({"message_id": 7, "chat": chat, "from": {"id": 42}, "text": "/tier vip"}))
    assert [x for x in run(_all(sf, SignalGroup)) if x.chat_id == -100777][0].tier == "GENERAL"
