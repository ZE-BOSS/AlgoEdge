"""
backend/copytrade/telegram_client.py

A Telegram USER login (MTProto, via Telethon) that watches signal channels.

Why a user login and not a bot token: a bot can only read channels whose admin
adds it. Signal channels are someone else's, so the only way to read them is as
a member - the user's own account. The user creates an api_id/api_hash at
my.telegram.org, enters them with their phone number in AlgoEdge, and types the
code Telegram sends them (and their 2FA password, if set). The resulting session
string is stored encrypted (copytrade.crypto) and never returned by the API.

    start_login / complete_login / logout / status
    dialogs()               the channels and groups the account is in (the picker)
    resolve(link)           a @username / t.me link / invite link -> the chat (joins if asked)
    history(chat_id, n)     recent messages, for the parse preview
    start_watching()        handlers for new and edited messages on watched channels
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from sqlalchemy import select

from backend.copytrade import crypto
from backend.copytrade.models import CopyTradeChannel, CopyTradeSettings
from backend.copytrade.rules import merge_rules
from backend.data.database import async_session
from backend.utils.logger import get_logger

logger = get_logger(__name__)

try:
    from telethon import TelegramClient, events, utils
    from telethon.errors import PhoneCodeExpiredError, PhoneCodeInvalidError, SessionPasswordNeededError
    from telethon.sessions import StringSession
    from telethon.tl.functions.channels import JoinChannelRequest
    from telethon.tl.functions.messages import CheckChatInviteRequest, ImportChatInviteRequest
    from telethon.tl.types import ChannelParticipantsAdmins
    HAS_TELETHON = True
except ImportError:  # pragma: no cover
    HAS_TELETHON = False


def _who(me: Any) -> str:
    name = " ".join(x for x in [getattr(me, "first_name", ""), getattr(me, "last_name", "")] if x)
    return f"{name} (@{me.username})" if getattr(me, "username", None) else (name or str(me.id))


class _UserClient:
    def __init__(self, user_id: str):
        self.user_id = user_id
        self.client: Any = None
        self.phone: str | None = None
        self.phone_code_hash: str | None = None
        self.watching = False
        self.channels: dict[int, int] = {}      # telegram chat id -> CopyTradeChannel.id
        self.last_error: str | None = None
        self.last_message_at: float | None = None
        self.admins: dict[int, tuple[float, set[int]]] = {}   # group chat id -> (fetched at, admin user ids)


def sender_allowed(chat_is_broadcast: bool, chat_id: int, sender_id: int | None, sender_username: str | None,
                   admins: set[int] | None, rules: dict) -> bool:
    """Who may move your money in a watched chat. A broadcast channel: only its
    admins can post, so everything. A group: members chat there too ("closed
    all", "move SL to ..."), so only the senders listed in allowed_senders, or
    else the group's admins (and admins posting anonymously as the group)."""
    if chat_is_broadcast:
        return True
    if sender_id is not None and int(sender_id) == int(chat_id):
        return True                                    # an anonymous admin posts as the group itself
    allowed = [str(x).lower().lstrip("@") for x in (rules.get("allowed_senders") or []) if str(x).strip()]
    if allowed:
        return str(sender_id) in allowed or (sender_username or "").lower() in allowed
    if not rules.get("only_admins", True):
        return True
    return sender_id is not None and admins is not None and int(sender_id) in admins


class TelegramManager:
    def __init__(self) -> None:
        self._clients: dict[str, _UserClient] = {}
        self._monitor_task: asyncio.Task | None = None

    def _uc(self, user_id: str) -> _UserClient:
        if user_id not in self._clients:
            self._clients[user_id] = _UserClient(user_id)
        return self._clients[user_id]

    @staticmethod
    def _require() -> None:
        if not HAS_TELETHON:
            raise RuntimeError("telethon is not installed on this server (pip install telethon)")

    # ── login ───────────────────────────────────────────────────────────────
    async def start_login(self, user_id: str, api_id: int, api_hash: str, phone: str) -> dict:
        self._require()
        uc = self._uc(user_id)
        if uc.client is not None:
            await uc.client.disconnect()
        uc.client = TelegramClient(StringSession(), int(api_id), api_hash)
        await uc.client.connect()
        sent = await uc.client.send_code_request(phone)
        uc.phone, uc.phone_code_hash = phone, sent.phone_code_hash
        async with async_session() as s:
            st = await s.get(CopyTradeSettings, user_id) or CopyTradeSettings(user_id=user_id, rules={})
            st.api_id, st.api_hash_enc, st.phone = int(api_id), crypto.encrypt(api_hash), phone
            s.add(st)
            await s.commit()
        return {"sent": True, "via": type(sent.type).__name__.replace("SentCodeType", "")}

    async def complete_login(self, user_id: str, code: str | None = None, password: str | None = None) -> dict:
        self._require()
        uc = self._uc(user_id)
        if uc.client is None or not uc.phone:
            return {"ok": False, "error": "start the login first (send the code)"}
        try:
            if code:
                await uc.client.sign_in(phone=uc.phone, code=str(code).strip(), phone_code_hash=uc.phone_code_hash)
            elif password:
                await uc.client.sign_in(password=password)
            else:
                return {"ok": False, "error": "enter the code"}
        except SessionPasswordNeededError:
            if not password:
                return {"ok": False, "need_password": True}
            await uc.client.sign_in(password=password)
        except (PhoneCodeInvalidError, PhoneCodeExpiredError) as e:
            return {"ok": False, "error": f"code rejected: {type(e).__name__}"}
        me = await uc.client.get_me()
        async with async_session() as s:
            st = await s.get(CopyTradeSettings, user_id)
            st.session_enc = crypto.encrypt(uc.client.session.save())
            st.tg_user = _who(me)
            await s.commit()
        await self.start_watching(user_id)
        return {"ok": True, "user": _who(me)}

    async def logout(self, user_id: str) -> None:
        uc = self._uc(user_id)
        if uc.client is not None:
            try:
                await uc.client.log_out()
            except Exception:  # noqa: BLE001
                pass
            uc.client = None
        uc.watching = False
        async with async_session() as s:
            st = await s.get(CopyTradeSettings, user_id)
            if st:
                st.session_enc, st.tg_user, st.enabled = None, None, False
                await s.commit()

    async def _connect_saved(self, user_id: str) -> bool:
        """Connect with the stored session; False if there is none or it was revoked."""
        self._require()
        uc = self._uc(user_id)
        if uc.client is not None and uc.client.is_connected() and await uc.client.is_user_authorized():
            return True
        async with async_session() as s:
            st = await s.get(CopyTradeSettings, user_id)
        sess = crypto.decrypt(st.session_enc) if st else None
        api_hash = crypto.decrypt(st.api_hash_enc) if st else None
        if not (sess and api_hash and st.api_id):
            return False
        uc.client = TelegramClient(StringSession(sess), int(st.api_id), api_hash)
        await uc.client.connect()
        if not await uc.client.is_user_authorized():
            uc.last_error = "Telegram session was revoked - log in again"
            return False
        return True

    async def status(self, user_id: str) -> dict:
        uc = self._uc(user_id)
        async with async_session() as s:
            st = await s.get(CopyTradeSettings, user_id)
        return {
            "telethon": HAS_TELETHON,
            "logged_in": bool(st and st.session_enc),
            "user": st.tg_user if st else None,
            "api_id": st.api_id if st else None,
            "phone": st.phone if st else None,
            "connected": bool(uc.client is not None and uc.client.is_connected()),
            "watching": uc.watching,
            "watched_chats": len(uc.channels),
            "awaiting_code": bool(uc.phone_code_hash and not (st and st.session_enc)),
            "last_error": uc.last_error,
            "last_message_at": uc.last_message_at,
        }

    # ── chats ───────────────────────────────────────────────────────────────
    async def dialogs(self, user_id: str, limit: int = 300) -> list[dict]:
        if not await self._connect_saved(user_id):
            raise RuntimeError("not logged in to Telegram")
        out = []
        async for d in self._uc(user_id).client.iter_dialogs(limit=limit):
            if d.is_channel or d.is_group:
                joined = getattr(d.entity, "date", None)       # for a channel/supergroup: when we joined
                out.append({"chat_id": d.id, "title": d.name, "username": getattr(d.entity, "username", None),
                            "is_channel": bool(d.is_channel and not d.is_group), "unread": d.unread_count,
                            "joined": joined.isoformat() if joined else None,
                            "last_message": d.date.isoformat() if getattr(d, "date", None) else None})
        return out

    async def resolve(self, user_id: str, link: str, join: bool = True) -> dict:
        """A chat from a @username, t.me/name, t.me/+invite or t.me/joinchat/invite link."""
        if not await self._connect_saved(user_id):
            raise RuntimeError("not logged in to Telegram")
        c = self._uc(user_id).client
        link = link.strip()
        m = re.search(r"(?:t\.me/\+|t\.me/joinchat/|telegram\.me/joinchat/)([\w-]+)", link)
        if m:
            invite = await c(CheckChatInviteRequest(m.group(1)))
            chat = getattr(invite, "chat", None)
            if chat is None:
                if not join:
                    return {"needs_join": True, "title": getattr(invite, "title", "")}
                upd = await c(ImportChatInviteRequest(m.group(1)))
                chat = upd.chats[0]
        else:
            name = re.sub(r"^(https?://)?(t\.me|telegram\.me)/", "", link).lstrip("@").split("/")[0]
            chat = await c.get_entity(name)
            if join and getattr(chat, "left", False):
                await c(JoinChannelRequest(chat))
        return {"chat_id": utils.get_peer_id(chat), "title": getattr(chat, "title", ""),
                "username": getattr(chat, "username", None)}

    async def history(self, user_id: str, chat_id: int, limit: int = 100) -> list[dict]:
        if not await self._connect_saved(user_id):
            raise RuntimeError("not logged in to Telegram")
        c = self._uc(user_id).client
        try:
            entity = await c.get_entity(chat_id)
        except ValueError:
            await c.get_dialogs()          # fills the entity cache
            entity = await c.get_entity(chat_id)
        msgs = await c.get_messages(entity, limit=limit)
        broadcast = bool(getattr(entity, "broadcast", False))
        admins = None if broadcast else await self._admins(self._uc(user_id), int(chat_id), entity)
        out = []
        for m in msgs:
            if not (m.message or "").strip():
                continue
            sid = getattr(m, "sender_id", None)
            out.append({"id": m.id, "date": m.date.isoformat() if m.date else None, "text": m.message or "",
                        "reply_to": getattr(m.reply_to, "reply_to_msg_id", None), "edited": bool(m.edit_date),
                        "sender_id": sid,
                        "from_admin": broadcast or (sid is not None and (int(sid) == int(chat_id) or
                                                                           int(sid) in (admins or set())))})
        return out

    async def _admins(self, uc: _UserClient, chat_id: int, chat: Any) -> set[int] | None:
        """Admin user ids of a group, cached for an hour. None if they can't be listed."""
        now = asyncio.get_running_loop().time()
        hit = uc.admins.get(chat_id)
        if hit and now - hit[0] < 3600:
            return hit[1]
        try:
            ids = {int(u.id) async for u in uc.client.iter_participants(chat, filter=ChannelParticipantsAdmins)}
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[COPYTRADE] can't list the admins of chat {chat_id}: {e} - set allowed_senders for it")
            ids = None
        uc.admins[chat_id] = (now, ids or set())
        return ids

    # ── watching ────────────────────────────────────────────────────────────
    async def reload_channels(self, user_id: str) -> None:
        async with async_session() as s:
            rows = (await s.execute(select(CopyTradeChannel).where(
                CopyTradeChannel.user_id == user_id, CopyTradeChannel.enabled.is_(True)))).scalars().all()
        self._uc(user_id).channels = {int(r.chat_id): int(r.id) for r in rows}

    async def start_watching(self, user_id: str) -> bool:
        uc = self._uc(user_id)
        if not await self._connect_saved(user_id):
            return False
        await self.reload_channels(user_id)
        if uc.watching:
            return True
        await uc.client.get_dialogs()   # entity cache, so updates resolve to known chats

        from backend.copytrade.service import copytrade_service

        async def on_message(event, edited: bool) -> None:
            chat_id = int(event.chat_id or 0)
            ch_id = uc.channels.get(chat_id)
            if ch_id is None:
                return
            async with async_session() as s:
                st = await s.get(CopyTradeSettings, user_id)
                if not st or not st.enabled:
                    return
                ch = await s.get(CopyTradeChannel, ch_id)
                rules = merge_rules(st.rules, ch.rules if ch else None)
            chat = await event.get_chat()
            broadcast = bool(getattr(chat, "broadcast", False))
            if not broadcast:
                sender = await event.get_sender()
                admins = await self._admins(uc, chat_id, chat)
                if not sender_allowed(False, chat_id, event.sender_id, getattr(sender, "username", None), admins, rules):
                    return                      # a member of the group, not the signal provider
            m = event.message
            uc.last_message_at = asyncio.get_running_loop().time()
            await copytrade_service.handle_message(
                user_id, ch_id, int(m.id), m.message or "",
                reply_to=getattr(m.reply_to, "reply_to_msg_id", None), edited=edited)

        async def on_new(event):
            try:
                await on_message(event, False)
            except Exception as e:  # noqa: BLE001
                uc.last_error = f"{type(e).__name__}: {e}"
                logger.exception(f"[COPYTRADE] new-message handler failed: {e}")

        async def on_edit(event):
            try:
                await on_message(event, True)
            except Exception as e:  # noqa: BLE001
                uc.last_error = f"{type(e).__name__}: {e}"
                logger.exception(f"[COPYTRADE] edit handler failed: {e}")

        uc.client.add_event_handler(on_new, events.NewMessage())
        uc.client.add_event_handler(on_edit, events.MessageEdited())
        uc.watching = True
        logger.info(f"[COPYTRADE] watching {len(uc.channels)} Telegram chats for user {user_id}")
        return True

    # ── lifecycle ───────────────────────────────────────────────────────────
    async def start_all(self) -> None:
        """App startup: reconnect every saved login and start the housekeeping loop."""
        if not HAS_TELETHON:
            logger.warning("[COPYTRADE] telethon not installed - Telegram copy trading unavailable")
        else:
            async with async_session() as s:
                rows = (await s.execute(select(CopyTradeSettings).where(
                    CopyTradeSettings.session_enc.is_not(None)))).scalars().all()
            for st in rows:
                try:
                    await self.start_watching(st.user_id)
                except Exception as e:  # noqa: BLE001
                    self._uc(st.user_id).last_error = f"{type(e).__name__}: {e}"
                    logger.warning(f"[COPYTRADE] could not reconnect Telegram for {st.user_id}: {e}")
        if self._monitor_task is None:
            self._monitor_task = asyncio.create_task(self._monitor_loop())

    async def _monitor_loop(self) -> None:
        from backend.copytrade.service import copytrade_service
        while True:
            try:
                await copytrade_service.monitor_once()
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[COPYTRADE] monitor: {e}")
            await asyncio.sleep(10)

    async def stop_all(self) -> None:
        if self._monitor_task:
            self._monitor_task.cancel()
            self._monitor_task = None
        for uc in self._clients.values():
            if uc.client is not None:
                try:
                    await uc.client.disconnect()
                except Exception:  # noqa: BLE001
                    pass


telegram_manager = TelegramManager()
