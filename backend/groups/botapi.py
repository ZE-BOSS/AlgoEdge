"""
backend/groups/botapi.py

A small Telegram Bot API client (HTTPS, httpx) for the group bot.

Telegram allows a bot about 20 messages a minute in one group and ~30 a second
overall. Every send goes through `call()`, which keeps at least PER_CHAT_GAP_S
between two messages to the same chat and honours `retry_after` on a 429, so a
burst (a signal, two TP hits and a close in one minute) is spaced instead of
refused.
"""
from __future__ import annotations

import asyncio
import os
import time
from typing import Any

import httpx

from backend.utils.logger import get_logger

logger = get_logger(__name__)

PER_CHAT_GAP_S = 3.1          # ~19 a minute per chat
GLOBAL_GAP_S = 0.05


class BotAPIError(RuntimeError):
    def __init__(self, method: str, code: int | None, description: str):
        super().__init__(f"{method}: {code} {description}")
        self.method, self.code, self.description = method, code, description


class BotAPI:
    def __init__(self, token: str | None = None, base: str = "https://api.telegram.org"):
        self.token = token if token is not None else os.getenv("GROUP_BOT_TOKEN", "").strip()
        self.base = base
        self._client: httpx.AsyncClient | None = None
        self._last_chat: dict[int, float] = {}
        self._last_any = 0.0
        self._lock = asyncio.Lock()
        self.me: dict | None = None

    @property
    def configured(self) -> bool:
        return bool(self.token)

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(70.0, connect=15.0))
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _pace(self, chat_id: int | None) -> None:
        async with self._lock:
            now = time.monotonic()
            wait = self._last_any + GLOBAL_GAP_S - now
            if chat_id is not None:
                wait = max(wait, self._last_chat.get(chat_id, 0.0) + PER_CHAT_GAP_S - now)
            if wait > 0:
                await asyncio.sleep(wait)
            t = time.monotonic()
            self._last_any = t
            if chat_id is not None:
                self._last_chat[chat_id] = t

    async def call(self, method: str, paced_chat: int | None = None, retries: int = 2, **params) -> Any:
        if not self.token:
            raise BotAPIError(method, None, "GROUP_BOT_TOKEN is not set")
        params = {k: v for k, v in params.items() if v is not None}
        for attempt in range(retries + 1):
            if paced_chat is not None:
                await self._pace(paced_chat)
            try:
                r = await (await self._http()).post(f"{self.base}/bot{self.token}/{method}", json=params)
                data = r.json()
            except (httpx.HTTPError, ValueError) as e:
                if attempt < retries:
                    await asyncio.sleep(2 * (attempt + 1))
                    continue
                raise BotAPIError(method, None, f"{type(e).__name__}: {e}") from e
            if data.get("ok"):
                return data.get("result")
            code = data.get("error_code")
            retry_after = (data.get("parameters") or {}).get("retry_after")
            if code == 429 and retry_after and attempt < retries:
                logger.info(f"[GROUPBOT] {method}: rate limited, waiting {retry_after}s")
                await asyncio.sleep(float(retry_after) + 0.5)
                continue
            raise BotAPIError(method, code, data.get("description", ""))
        raise BotAPIError(method, None, "gave up")

    # -- the calls the group bot uses -------------------------------------------
    async def get_me(self) -> dict:
        self.me = await self.call("getMe")
        return self.me

    async def get_updates(self, offset: int | None, timeout: int = 50) -> list[dict]:
        return await self.call("getUpdates", offset=offset, timeout=timeout, retries=0,
                               allowed_updates=["message", "edited_message", "channel_post", "my_chat_member",
                                                "chat_member", "callback_query"]) or []

    async def send(self, chat_id: int, text: str, reply_to: int | None = None, buttons: list | None = None,
                   silent: bool = False) -> dict:
        markup = {"inline_keyboard": buttons} if buttons else None
        reply = {"message_id": int(reply_to), "allow_sending_without_reply": True} if reply_to else None
        return await self.call("sendMessage", paced_chat=int(chat_id), chat_id=int(chat_id), text=text,
                               parse_mode="HTML", link_preview_options={"is_disabled": True},
                               reply_parameters=reply, reply_markup=markup, disable_notification=silent or None)

    async def edit(self, chat_id: int, message_id: int, text: str, buttons: list | None = None) -> Any:
        markup = {"inline_keyboard": buttons} if buttons is not None else None
        try:
            return await self.call("editMessageText", paced_chat=int(chat_id), chat_id=int(chat_id),
                                   message_id=int(message_id), text=text, parse_mode="HTML",
                                   link_preview_options={"is_disabled": True}, reply_markup=markup)
        except BotAPIError as e:
            if "message is not modified" in (e.description or ""):
                return None
            raise

    async def delete(self, chat_id: int, message_id: int) -> Any:
        return await self.call("deleteMessage", chat_id=int(chat_id), message_id=int(message_id))

    async def pin(self, chat_id: int, message_id: int) -> Any:
        return await self.call("pinChatMessage", chat_id=int(chat_id), message_id=int(message_id),
                               disable_notification=True)

    async def invite_link(self, chat_id: int, name: str, hours: int = 24) -> str:
        r = await self.call("createChatInviteLink", chat_id=int(chat_id), name=name[:32], member_limit=1,
                            expire_date=int(time.time()) + hours * 3600)
        return r["invite_link"]

    async def remove_member(self, chat_id: int, user_id: int) -> None:
        """Kick without a permanent ban (ban, then unban)."""
        await self.call("banChatMember", chat_id=int(chat_id), user_id=int(user_id))
        await self.call("unbanChatMember", chat_id=int(chat_id), user_id=int(user_id), only_if_banned=True)

    async def answer_callback(self, callback_id: str, text: str = "") -> Any:
        return await self.call("answerCallbackQuery", callback_query_id=callback_id, text=text[:190] or None)

    async def chat_member(self, chat_id: int, user_id: int) -> dict:
        return await self.call("getChatMember", chat_id=int(chat_id), user_id=int(user_id))

    async def get_chat(self, chat_id: int) -> dict:
        return await self.call("getChat", chat_id=int(chat_id))

    async def restrict(self, chat_id: int, user_id: int, minutes: int) -> Any:
        return await self.call("restrictChatMember", chat_id=int(chat_id), user_id=int(user_id),
                               permissions={"can_send_messages": False},
                               until_date=int(time.time()) + minutes * 60)
