"""
backend/groups/bot.py

The group bot: one Telegram bot (GROUP_BOT_TOKEN) that runs the General, VIP
and VVIP channels. Runs only on the instance that has the token set.

Loops
  updates      long-polls getUpdates: groups the bot is added to, admin
               commands, the VIP join chat, moderation and FAQ answers in
               groups, approve / skip buttons.
  outbox       sends queued posts in order, threading replies under their
               signal's first post and editing its status line.
  scheduler    morning / midday / summary / weekly / education posts at the
               configured local times; LLM posts become drafts for the admin
               unless approval is "auto" for that kind.
  maintenance  membership expiry and the admin's content alerts (video scripts
               after a big win, a loss streak, the weekly recap).

Admins: Telegram user ids in settings.admin_ids (or GROUP_ADMIN_IDS). An admin
links their Telegram account by sending the bot "/claim <code>", the code
shown on the admin Groups page.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import re
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import desc, select

from backend.groups import settings as S
from backend.groups import templates as T
from backend.groups import writer as W
from backend.groups.botapi import BotAPI, BotAPIError
from backend.groups.models import BotKV, GroupMember, GroupPost, GroupSignal, LLMDraft, SignalGroup
from backend.groups.router import group_router
from backend.utils.logger import get_logger

logger = get_logger(__name__)

LINK_RE = re.compile(r"(https?://|www\.|t\.me/|telegram\.me/|wa\.me/|bit\.ly/)", re.I)
PHONE_RE = re.compile(r"(?<!\d)\+?\d[\d\s-]{9,}\d(?!\d)")
DM_RE = re.compile(r"\b(dm me|inbox me|message me|whatsapp|text me|contact me on)\b", re.I)
MONEY_RE = re.compile(r"\b(pay(ment)?|refund|withdraw\w*|deposit\w*|invest\w*|account manag\w*|my money|subscription|renew\w*)\b", re.I)


def _now() -> datetime:
    return datetime.utcnow()


def claim_code(token: str) -> str:
    """The code an admin sends the bot to link their Telegram account."""
    return hashlib.sha256(("claim:" + (token or "")).encode()).hexdigest()[:8].upper()


class GroupBot:
    def __init__(self, api: BotAPI | None = None, session_factory=None):
        self.api = api or BotAPI()
        self._sf = session_factory
        self.tasks: list[asyncio.Task] = []
        self.running = False
        self.username = ""
        self.bot_id = 0
        self.last_error = ""
        self._admin_cache: dict[tuple[int, int], tuple[bool, float]] = {}
        self._strikes: dict[tuple[int, int], int] = {}

    @property
    def sf(self):
        if self._sf is None:
            from backend.data.database import async_session
            return async_session
        return self._sf

    @property
    def enabled(self) -> bool:
        return self.api.configured and os.getenv("ALGOEDGE_AUDIT") != "1"

    # ── lifecycle ─────────────────────────────────────────────────────────────
    async def start(self) -> None:
        if not self.enabled or self.running:
            return
        self.running = True
        group_router.admin_notify = self.notify_admins
        self.tasks = [asyncio.create_task(self._guard(n, f)) for n, f in (
            ("updates", self._updates_loop), ("outbox", self._outbox_loop),
            ("scheduler", self._scheduler_loop), ("maintenance", self._maintenance_loop))]
        logger.info("[GROUPBOT] started")

    async def stop(self) -> None:
        self.running = False
        for t in self.tasks:
            t.cancel()
        self.tasks = []
        await self.api.close()

    async def _guard(self, name: str, fn) -> None:
        delay = 5
        while self.running:
            try:
                await fn()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                self.last_error = f"{name}: {type(e).__name__}: {e}"
                logger.warning(f"[GROUPBOT] {self.last_error}")
                await asyncio.sleep(delay)
                delay = min(delay * 2, 120)

    async def _kv(self, s, key: str, value: str | None = None) -> str:
        row = await s.get(BotKV, key)
        if value is None:
            return row.value if row else ""
        if row is None:
            s.add(BotKV(key=key, value=value))
        else:
            row.value = value
        return value

    # ── admins ────────────────────────────────────────────────────────────────
    async def notify_admins(self, text: str, buttons: list | None = None) -> list[int]:
        """DM every linked admin; also the existing alerts bot, so nothing is
        missed before an admin has claimed the group bot. Returns message ids."""
        ids = []
        async with self.sf() as s:
            cfg = await S.load(s)
        for uid in cfg.get("admin_ids") or []:
            try:
                m = await self.api.send(uid, text, buttons=buttons)
                ids.append(m.get("message_id"))
            except BotAPIError as e:
                logger.info(f"[GROUPBOT] admin {uid} not reachable: {e.description}")
        if not ids:
            try:
                from backend.services.telegram import telegram_service
                plain = re.sub(r"<[^>]+>", "", text)
                await telegram_service.send_message(telegram_service.escape_markdown(plain))
            except Exception:  # noqa: BLE001
                pass
        return ids

    async def _is_admin_user(self, uid: int) -> bool:
        async with self.sf() as s:
            cfg = await S.load(s)
        return int(uid) in set(cfg.get("admin_ids") or [])

    async def _is_chat_admin(self, chat_id: int, uid: int) -> bool:
        k = (chat_id, uid)
        hit = self._admin_cache.get(k)
        loop_t = asyncio.get_event_loop().time()
        if hit and loop_t - hit[1] < 600:
            return hit[0]
        try:
            m = await self.api.chat_member(chat_id, uid)
            ok = m.get("status") in ("administrator", "creator")
        except BotAPIError:
            ok = False
        ok = ok or await self._is_admin_user(uid)
        self._admin_cache[k] = (ok, loop_t)
        return ok

    # ── updates ───────────────────────────────────────────────────────────────
    async def _updates_loop(self) -> None:
        if not self.bot_id:
            me = await self.api.get_me()
            self.bot_id, self.username = int(me["id"]), me.get("username", "")
            logger.info(f"[GROUPBOT] logged in as @{self.username}")
        async with self.sf() as s:
            off = await self._kv(s, "offset")
        offset = int(off) if off else None
        while self.running:
            ups = await self.api.get_updates(offset, timeout=50)
            for u in ups:
                offset = int(u["update_id"]) + 1
                try:
                    await self.handle_update(u)
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"[GROUPBOT] update {u.get('update_id')} failed: {e}")
            if ups:
                async with self.sf() as s:
                    await self._kv(s, "offset", str(offset))
                    await s.commit()

    async def handle_update(self, u: dict) -> None:
        if "my_chat_member" in u:
            await self.on_my_chat_member(u["my_chat_member"])
        elif "callback_query" in u:
            await self.on_callback(u["callback_query"])
        elif "message" in u:
            m = u["message"]
            if m.get("chat", {}).get("type") == "private":
                await self.on_private(m)
            else:
                await self.on_group_message(m)

    async def on_my_chat_member(self, ev: dict) -> None:
        chat = ev.get("chat") or {}
        new = ev.get("new_chat_member") or {}
        status = new.get("status", "")
        async with self.sf() as s:
            g = (await s.execute(select(SignalGroup).where(SignalGroup.chat_id == int(chat["id"])))).scalars().first()
            if g is None:
                g = SignalGroup(chat_id=int(chat["id"]))
                s.add(g)
            g.title = chat.get("title") or g.title or ""
            g.chat_type = chat.get("type", "")
            g.bot_status = status
            is_admin = status == "administrator"
            g.can_post = is_admin and (new.get("can_post_messages", True) if g.chat_type == "channel" else True)
            g.can_invite = is_admin and bool(new.get("can_invite_users"))
            g.can_delete = is_admin and bool(new.get("can_delete_messages"))
            if status in ("left", "kicked"):
                g.posting = False
            await s.commit()
            gid, title, tier = g.id, g.title, g.tier
        missing = [n for n, ok in (("post", g.can_post), ("invite users", g.can_invite), ("delete messages", g.can_delete)) if not ok]
        if status in ("left", "kicked"):
            await self.notify_admins(f"⚠️ The group bot was removed from <b>{T.e(title)}</b>. Posting there is off.")
        else:
            await self.notify_admins(
                f"🤖 Group bot is now <b>{T.e(status)}</b> in <b>{T.e(title)}</b> (id {chat['id']})."
                + (f"\nMissing rights: {', '.join(missing)}." if missing else "")
                + ("" if tier else "\nSet its tier (General / VIP / VVIP) and switch posting on, on the Groups page."))

    async def on_callback(self, cq: dict) -> None:
        data = cq.get("data") or ""
        uid = int((cq.get("from") or {}).get("id") or 0)
        cid = cq.get("id")
        if data.startswith("join:"):
            await self.api.answer_callback(cid)
            await self.join_step(cq["from"], data.split(":", 1)[1], None)
            return
        if not await self._is_admin_user(uid):
            await self.api.answer_callback(cid, "Admins only")
            return
        kind, act, ident = (data.split(":") + ["", "", ""])[:3]
        if kind == "d":
            msg = await self.decide_draft(int(ident), act)
        elif kind == "m":
            msg = await self.decide_member(int(ident), act)
        else:
            msg = "?"
        await self.api.answer_callback(cid, msg)
        mm = cq.get("message") or {}
        if mm.get("message_id"):
            try:
                await self.api.edit(mm["chat"]["id"], mm["message_id"], (mm.get("text") and T.e(mm["text"]) or "") + f"\n\n<b>{T.e(msg)}</b>", buttons=[])
            except BotAPIError:
                pass

    # ── private chat: admins and the join flow ────────────────────────────────
    async def on_private(self, m: dict) -> None:
        frm = m.get("from") or {}
        uid = int(frm.get("id"))
        text = (m.get("text") or "").strip()
        async with self.sf() as s:
            cfg = await S.load(s)
        if text.startswith("/claim"):
            code = text.split(maxsplit=1)[1].strip().upper() if " " in text else ""
            if code and code == claim_code(self.api.token):
                async with self.sf() as s:
                    ids = sorted(set(cfg.get("admin_ids") or []) | {uid})
                    await S.save(s, {"admin_ids": ids})
                    await s.commit()
                await self.api.send(uid, "✅ You're an admin of the group bot now. Send /help for commands.")
            else:
                await self.api.send(uid, "That code isn't right.")
            return
        if uid in set(cfg.get("admin_ids") or []) and text.startswith("/") and not text.startswith("/start"):
            await self.api.send(uid, await self.admin_command(text))
            return
        if text.startswith("/start"):
            await self.join_step(frm, "menu", None)
            return
        await self.join_step(frm, None, text)

    async def admin_command(self, text: str) -> str:
        parts = text.split(maxsplit=2)
        cmd = parts[0].lower()
        async with self.sf() as s:
            groups = (await s.execute(select(SignalGroup))).scalars().all()
            by_tier = {g.tier.lower(): g for g in groups if g.tier}
            if cmd == "/help":
                return ("/status - groups and queue\n/pause general|vip|vvip\n/resume general|vip|vvip\n"
                        "/post general|vip|vvip text - post as the bot\n/reply telegram_id text - answer a member")
            if cmd == "/status":
                q = (await s.execute(select(GroupPost).where(GroupPost.status == "QUEUED"))).scalars().all()
                lines = [f"{g.tier or '(no tier)'}: {T.e(g.title)} - posting {'on' if g.posting else 'off'}, bot {g.bot_status}"
                         for g in groups] or ["No groups yet - add the bot to a group as admin."]
                return "\n".join(lines + [f"Queued posts: {len(q)}"])
            if cmd in ("/pause", "/resume") and len(parts) > 1:
                g = by_tier.get(parts[1].lower())
                if g is None:
                    return "No group with that tier."
                g.posting = cmd == "/resume"
                await s.commit()
                return f"{g.tier} posting {'on' if g.posting else 'off'}."
            if cmd == "/post" and len(parts) > 2:
                g = by_tier.get(parts[1].lower())
                if g is None:
                    return "No group with that tier."
                s.add(GroupPost(chat_id=g.chat_id, kind="admin", text=T.e(parts[2]), status="QUEUED", send_at=_now()))
                await s.commit()
                return f"Queued for {g.tier}."
        if cmd == "/reply" and len(parts) > 2 and parts[1].lstrip("-").isdigit():
            try:
                await self.api.send(int(parts[1]), "💬 <b>Admin:</b> " + T.e(parts[2]))
                return "Sent."
            except BotAPIError as e:
                return f"Not sent: {e.description}"
        return "Unknown command. /help"

    async def join_step(self, frm: dict, action: str | None, text: str | None) -> None:
        uid = int(frm["id"])
        async with self.sf() as s:
            cfg = await S.load(s)
            mem = (await s.execute(select(GroupMember).where(GroupMember.telegram_id == uid))).scalars().first()
            if mem is None:
                mem = GroupMember(telegram_id=uid, status="NEW")
                s.add(mem)
            mem.username = frm.get("username") or mem.username or ""
            mem.name = " ".join(x for x in (frm.get("first_name"), frm.get("last_name")) if x) or mem.name
            reply, buttons, admin_msg = None, None, None

            if action == "menu":
                if mem.status == "ACTIVE":
                    exp = mem.expires_at.strftime("%d %b %Y") if mem.expires_at else "no end date"
                    reply = f"You're an active <b>{T.e(mem.tier)}</b> member (until {exp})."
                else:
                    reply = (f"Welcome to {T.e(cfg['brand'])}. What would you like to do?")
                    buttons = [[{"text": "Join VIP", "callback_data": "join:vip"}],
                               [{"text": "About VVIP (bot trades your account)", "callback_data": "join:vvip"}],
                               [{"text": "Talk to the admin", "callback_data": "join:admin"}]]
                mem.step = ""
            elif action == "vip":
                links = cfg.get("affiliate_links") or {}
                link_lines = "\n".join(f"• {T.e(b)}: {T.e(u)}" for b, u in links.items()) or \
                    "• The admin will send you the partner link."
                reply = (f"<b>Joining VIP</b>\n1. Open an MT5 account through our partner link:\n{link_lines}\n"
                         f"2. Fund it with at least ${cfg['vip_min_balance']:,.0f}.\n"
                         f"3. Send me your MT5 login number here.")
                mem.step, mem.tier = "await_login", "VIP"
                if mem.status in ("NEW", "DENIED", "REMOVED"):
                    mem.status = "ASKED"
            elif action == "vvip":
                lv = cfg.get("vvip_levels") or {}
                reply = ("<b>VVIP: our bot trades your own MT5 account</b>\n"
                         f"• Starter ${lv.get('STARTER', 300):,.0f}: live trading on your account\n"
                         f"• Pro ${lv.get('PRO', 500):,.0f}: plus backtesting\n"
                         f"• Elite ${lv.get('ELITE', 1000):,.0f}: plus order-flow data\n"
                         f"Price per {cfg['vvip_period_days']} days. Your money stays in your own account; you can pause any time.\n"
                         f"Sign up: {T.e(cfg.get('portal_url', ''))} - or tap Talk to the admin.")
                buttons = [[{"text": "Talk to the admin", "callback_data": "join:admin"}]]
            elif action == "admin":
                mem.step = "to_admin"
                reply = "Type your message and I'll pass it to the admin. They'll answer you here."
            elif text is not None:
                if mem.step == "await_login":
                    login = re.sub(r"\D", "", text)
                    if 5 <= len(login) <= 12:
                        mem.mt5_login, mem.status, mem.step = login, "PENDING", ""
                        reply = "Thanks. The admin will check your account and send your VIP invite here, usually within a few hours."
                        admin_msg = (f"🙋 VIP request: {T.e(mem.name)} (@{T.e(mem.username or '-')}, id {uid})\n"
                                     f"MT5 login: <b>{login}</b>\nCheck it's under our partner account with at least "
                                     f"${cfg['vip_min_balance']:,.0f}.")
                    else:
                        reply = "That doesn't look like an MT5 login - it's the account number (digits only)."
                elif mem.step == "to_admin":
                    mem.step = ""
                    reply = "Sent to the admin. They'll reply here."
                    admin_msg = (f"💬 From {T.e(mem.name)} (@{T.e(mem.username or '-')}):\n{T.e(text[:1500])}\n\n"
                                 f"Answer with: /reply {uid} your message")
                else:
                    reply = "Tap /start to see the options."
            await s.commit()
            mem_id = mem.id
        if reply:
            await self.api.send(uid, reply, buttons=buttons)
        if admin_msg:
            btns = None
            if "VIP request" in admin_msg:
                btns = [[{"text": "Approve VIP", "callback_data": f"m:ap:{mem_id}"},
                         {"text": "Deny", "callback_data": f"m:dn:{mem_id}"}]]
            await self.notify_admins(admin_msg, buttons=btns)

    async def decide_member(self, member_id: int, act: str) -> str:
        async with self.sf() as s:
            cfg = await S.load(s)
            mem = await s.get(GroupMember, member_id)
            if mem is None:
                return "Member not found"
            if act == "dn":
                mem.status = "DENIED"
                await s.commit()
                await self._dm(mem.telegram_id, "Sorry - we couldn't confirm your account for VIP yet. Tap /start and Talk to the admin if you think that's wrong.")
                return "Denied"
            tier = mem.tier or "VIP"
            groups = (await s.execute(select(SignalGroup).where(SignalGroup.tier == tier))).scalars().all()
            mem.status, mem.tier = "ACTIVE", tier
            mem.expires_at = _now() + timedelta(days=30 * int(cfg.get("vip_months") or 1))
            await s.commit()
        links = []
        for g in groups:
            try:
                links.append(await self.api.invite_link(g.chat_id, f"{tier} {member_id}"))
            except BotAPIError as e:
                logger.warning(f"[GROUPBOT] invite link for {g.title} failed: {e}")
        if not links:
            return f"Approved, but no {tier} group with invite rights is set up yet"
        await self._dm(mem.telegram_id, f"🎉 You're in! Your {tier} invite (one use, valid 24 hours):\n" + "\n".join(links))
        return "Approved - invite sent"

    async def _dm(self, uid: int, text: str) -> None:
        try:
            await self.api.send(uid, text)
        except BotAPIError as e:
            logger.info(f"[GROUPBOT] DM to {uid} failed: {e.description}")

    # ── group messages: moderation and FAQ ────────────────────────────────────
    async def on_group_message(self, m: dict) -> None:
        chat_id = int(m["chat"]["id"])
        frm = m.get("from") or {}
        uid = int(frm.get("id") or 0)
        text = m.get("text") or m.get("caption") or ""
        if not uid or frm.get("is_bot") or not text:
            return
        async with self.sf() as s:
            cfg = await S.load(s)
        if text.startswith("/") and await self._is_admin_user(uid):
            out = await self.admin_command(text.replace(f"@{self.username}", ""))
            await self._dm(uid, out)
            return
        if cfg.get("moderate") and (LINK_RE.search(text) or PHONE_RE.search(text) or DM_RE.search(text)):
            if not await self._is_chat_admin(chat_id, uid):
                try:
                    await self.api.delete(chat_id, m["message_id"])
                except BotAPIError:
                    pass
                k = (chat_id, uid)
                self._strikes[k] = self._strikes.get(k, 0) + 1
                if self._strikes[k] >= 3:
                    try:
                        await self.api.restrict(chat_id, uid, 60)
                    except BotAPIError:
                        pass
                    self._strikes[k] = 0
                return
        if not cfg.get("answer_faq"):
            return
        mentioned = self.username and f"@{self.username}".lower() in text.lower()
        replied = ((m.get("reply_to_message") or {}).get("from") or {}).get("id") == self.bot_id
        if not (mentioned or replied or text.rstrip().endswith("?")):
            return
        low = text.lower()
        if MONEY_RE.search(low):
            who = cfg.get("admin_contact") or "the admin"
            await self.api.send(chat_id, f"For anything about payments or your account, please message {T.e(who)} directly.",
                                reply_to=m["message_id"])
            return
        for item in cfg.get("faq") or []:
            if all(w in low for w in str(item.get("q", "")).lower().split()):
                await self.api.send(chat_id, T.e(item.get("a", "")), reply_to=m["message_id"])
                return

    # ── outbox ────────────────────────────────────────────────────────────────
    async def _outbox_loop(self) -> None:
        while self.running:
            n = await self.flush_outbox()
            await asyncio.sleep(0.5 if n else 1.5)

    async def flush_outbox(self, limit: int = 20) -> int:
        """Send due posts. Returns how many were handled."""
        done = 0
        async with self.sf() as s:
            due = (await s.execute(select(GroupPost).where(
                GroupPost.status == "QUEUED", GroupPost.send_at <= _now()).order_by(
                GroupPost.created_at, GroupPost.id).limit(limit))).scalars().all()
            edits_done: set[int] = set()
            for p in due:
                sig = await s.get(GroupSignal, p.signal_id) if p.signal_id else None
                try:
                    if p.kind == "edit":
                        if sig is None or not sig.message_id:
                            continue                         # wait for the signal's own post
                        if sig.id not in edits_done:
                            await self.api.edit(p.chat_id, sig.message_id, sig.text + "\n\n" + T.status_line(p.text))
                            edits_done.add(sig.id)
                        p.status, p.sent_at = "SENT", _now()
                    else:
                        root_kind = p.kind in ("signal", "headsup")
                        reply_to = p.reply_to
                        if sig is not None and not root_kind:
                            if not sig.message_id:
                                continue
                            reply_to = sig.message_id
                        msg = await self.api.send(p.chat_id, p.text, reply_to=reply_to)
                        p.message_id = msg.get("message_id")
                        p.status, p.sent_at = "SENT", _now()
                        if sig is not None and root_kind and not sig.message_id:
                            sig.message_id = p.message_id
                            if sig.status == "QUEUED":
                                sig.status = "OPEN"
                except BotAPIError as e:
                    p.status, p.error = "FAILED", (e.description or str(e))[:300]
                    logger.warning(f"[GROUPBOT] post {p.id} to {p.chat_id} failed: {p.error}")
                done += 1
            await s.commit()
        return done

    # ── scheduler ─────────────────────────────────────────────────────────────
    async def _scheduler_loop(self) -> None:
        while self.running:
            await self.scheduler_tick()
            await asyncio.sleep(30)

    async def scheduler_tick(self, now: datetime | None = None) -> list[str]:
        async with self.sf() as s:
            cfg = await S.load(s)
        tz = ZoneInfo(cfg.get("timezone") or "Africa/Lagos")
        local = (now or datetime.now(tz)).astimezone(tz)
        fired = []
        for kind, when in (cfg.get("schedule") or {}).items():
            day, hhmm = (when.split() if " " in when else (None, when))
            if day and local.strftime("%a") != day[:3].title():
                continue
            if not day and local.weekday() >= 5 and kind != "education":
                continue
            h, mi = (int(x) for x in hhmm.split(":"))
            if (local.hour, local.minute) < (h, mi) or (local.hour * 60 + local.minute) - (h * 60 + mi) > 20:
                continue
            stamp = local.strftime("%Y-%m-%d")
            async with self.sf() as s:
                if await self._kv(s, f"sched:{kind}") == stamp:
                    continue
                await self._kv(s, f"sched:{kind}", stamp)
                await s.commit()
            await self.scheduled_post(kind, cfg, local)
            fired.append(kind)
        return fired

    async def _groups(self, s, tiers=("GENERAL", "VIP")) -> list[SignalGroup]:
        return (await s.execute(select(SignalGroup).where(SignalGroup.posting.is_(True),
                                                          SignalGroup.tier.in_(tiers)))).scalars().all()

    async def facts(self, local: datetime) -> dict[str, Any]:
        start = (local.replace(hour=0, minute=0, second=0, microsecond=0)).astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
        async with self.sf() as s:
            rows = (await s.execute(select(GroupSignal).where(GroupSignal.kind == "SIGNAL",
                                                              GroupSignal.created_at >= start))).scalars().all()
        seen, today = set(), []
        for g in rows:
            k = (g.key, g.symbol, g.side)
            if k in seen:
                continue
            seen.add(k)
            today.append({"symbol": g.symbol, "side": g.side, "status": g.status, "result_r": g.result_r})
        return {"date": local.strftime("%A %d %B"), "signals_today": [
            {"market": T.name(r["symbol"]), "side": r["side"], "status": r["status"].lower(),
             "result": T.fmt_r(r["result_r"]) or None} for r in today],
            "open_signals": sum(1 for r in today if r["status"] == "OPEN"), "events": [], "_rows": today}

    async def scheduled_post(self, kind: str, cfg: dict, local: datetime) -> None:
        f = await self.facts(local)
        rows = f.pop("_rows")
        if kind in ("summary", "weekly"):
            if kind == "summary":
                text = T.day_summary(rows, local.strftime("%a %d %b"))
            else:
                async with self.sf() as s:
                    wk = (await s.execute(select(GroupSignal).where(
                        GroupSignal.kind == "SIGNAL", GroupSignal.created_at >= _now() - timedelta(days=7)))).scalars().all()
                text = T.week_summary([{"symbol": g.symbol, "side": g.side, "result_r": g.result_r} for g in wk],
                                      local.strftime("week of %d %b"))
            await self._queue_for_groups(kind, text)
            return
        tasks = {"morning": "Write a good-morning post for today. Mention the calendar events if any.",
                 "midday": "Write a one-line midday check on the open signals.",
                 "education": "Write a short educational post on one basic trading habit (risk per trade, stop placement, limit orders, patience). Pick one."}
        text, why = await W.write(cfg, tasks.get(kind, kind), f)
        text = text or W.fallback(kind, f)
        if not text:
            return
        if cfg.get("approval") == "auto" and kind in (cfg.get("auto_kinds") or []):
            await self._queue_for_groups(kind, text)
            return
        async with self.sf() as s:
            d = LLMDraft(kind=kind, text=text, status="PENDING", facts=f)
            s.add(d)
            await s.commit()
            did = d.id
        ids = await self.notify_admins(f"📝 Draft {kind} post:\n\n{text}",
                                       buttons=[[{"text": "Post it", "callback_data": f"d:ap:{did}"},
                                                 {"text": "Skip", "callback_data": f"d:sk:{did}"}]])
        if ids:
            async with self.sf() as s:
                d = await s.get(LLMDraft, did)
                d.admin_message_id = ids[0]
                await s.commit()

    async def _queue_for_groups(self, kind: str, text: str, tiers=("GENERAL", "VIP")) -> int:
        async with self.sf() as s:
            groups = await self._groups(s, tiers)
            for g in groups:
                s.add(GroupPost(chat_id=g.chat_id, kind=kind, text=text, status="QUEUED", send_at=_now()))
            await s.commit()
        return len(groups)

    async def decide_draft(self, draft_id: int, act: str) -> str:
        async with self.sf() as s:
            d = await s.get(LLMDraft, draft_id)
            if d is None or d.status != "PENDING":
                return "Already handled"
            d.status = "POSTED" if act == "ap" else "SKIPPED"
            await s.commit()
            kind, text = d.kind, d.text
        if act == "ap":
            n = await self._queue_for_groups(kind, text)
            return f"Posted to {n} group(s)"
        return "Skipped"

    # ── maintenance: expiry and content alerts ────────────────────────────────
    async def _maintenance_loop(self) -> None:
        while self.running:
            await self.maintenance_tick()
            await asyncio.sleep(900)

    async def maintenance_tick(self) -> None:
        await self.expire_members()
        async with self.sf() as s:
            cfg = await S.load(s)
        if cfg.get("content_alerts"):
            await self.content_alerts(cfg)

    async def expire_members(self) -> None:
        now = _now()
        async with self.sf() as s:
            mems = (await s.execute(select(GroupMember).where(GroupMember.status.in_(("ACTIVE", "WARNED")),
                                                              GroupMember.expires_at.isnot(None)))).scalars().all()
            for m in mems:
                if m.expires_at <= now:
                    groups = (await s.execute(select(SignalGroup).where(SignalGroup.tier == m.tier))).scalars().all()
                    for g in groups:
                        try:
                            await self.api.remove_member(g.chat_id, m.telegram_id)
                        except BotAPIError as e:
                            logger.info(f"[GROUPBOT] remove {m.telegram_id} from {g.title}: {e.description}")
                    m.status = "REMOVED"
                    await self._dm(m.telegram_id, f"Your {m.tier} access has ended. Tap /start to renew.")
                elif m.status == "ACTIVE" and m.expires_at - now <= timedelta(days=7):
                    m.status = "WARNED"
                    await self._dm(m.telegram_id, f"Heads-up: your {m.tier} access ends on {m.expires_at:%d %b}. "
                                                  f"Tap /start and Talk to the admin to renew.")
            await s.commit()

    async def content_alerts(self, cfg: dict) -> list[str]:
        sent = []
        async with self.sf() as s:
            done = set((await self._kv(s, "content_done")).split(",")) - {""}
            closed = (await s.execute(select(GroupSignal).where(
                GroupSignal.kind == "SIGNAL", GroupSignal.status == "CLOSED",
                GroupSignal.closed_at >= _now() - timedelta(hours=24)).order_by(desc(GroupSignal.closed_at)))).scalars().all()
            last3 = (await s.execute(select(GroupSignal).where(GroupSignal.kind == "SIGNAL", GroupSignal.status == "CLOSED")
                                     .order_by(desc(GroupSignal.closed_at)).limit(3))).scalars().all()
        moments = []
        for g in closed:
            if (g.result_r or 0) >= 3 and f"win:{g.key}" not in done:
                moments.append((f"win:{g.key}", "A big win to show",
                                {"market": T.name(g.symbol), "side": g.side, "entry": g.entry, "stop": g.sl,
                                 "targets": g.tps, "targets_hit": g.tps_hit, "result": T.fmt_r(g.result_r)}))
        if len(last3) == 3 and all((g.result_r or 0) < -0.05 for g in last3):
            key = "streak:" + ",".join(str(g.id) for g in last3)
            if key not in done:
                moments.append((key, "Explaining a losing streak (trust is won on losses)",
                                {"last_three": [{"market": T.name(g.symbol), "side": g.side, "result": T.fmt_r(g.result_r)} for g in last3]}))
        for key, moment, facts in moments:
            text, _ = await W.write(cfg, f"Write a 60-second video script for the admin: {moment}. Hook line, 3-5 talking "
                                         f"points with the numbers, and a closing line inviting viewers to the free group.", facts)
            text = text or W.script_fallback(moment, facts)
            async with self.sf() as s:
                s.add(LLMDraft(kind="script", text=text, status="ADMIN", facts=facts))
                done.add(key)
                await self._kv(s, "content_done", ",".join(sorted(done)[-200:]))
                await s.commit()
            await self.notify_admins(f"🎬 <b>Worth a video now:</b> {T.e(moment)}\n\n{text}")
            sent.append(key)
        return sent

    # ── status for the admin page ─────────────────────────────────────────────
    def status(self) -> dict:
        return {"configured": self.api.configured, "running": self.running, "username": self.username,
                "last_error": self.last_error, "claim_code": claim_code(self.api.token) if self.api.configured else ""}


group_bot = GroupBot()
