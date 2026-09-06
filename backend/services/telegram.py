"""
backend/services/telegram.py

Telegram notification transport.

WHY THE TLS SETUP LOOKS LIKE THIS
---------------------------------
This file has flip-flopped between two broken states:

  d15f59a  added a custom SSL context with check_hostname=False and
           verify_mode=CERT_NONE. That made delivery work again on a machine
           whose certificate store could not verify api.telegram.org, at the
           cost of accepting *any* certificate — anyone able to intercept the
           connection could read or forge the traffic, bot token included.

  1b135ee  removed it, restoring aiohttp's verified default. That closed the
           security hole and re-broke delivery on that same machine, silently:
           every failure is caught per-message and written to the log, so from
           the frontend it just looks like Telegram "stopped working".

Both are avoidable. The actual problem is the *trust store*, not verification:
Python on Windows does not read the OS certificate store by default, so a
freshly built venv can have no usable CA bundle at all. `certifi` ships one.
We verify certificates and hostnames properly, against certifi's bundle, with
`truststore` (which does read the OS store) preferred when available.

Verification is never disabled. If TLS fails, the error is recorded in
`last_error` and surfaced by GET /api/telegram/status and POST /api/telegram/test
so it is visible instead of silent.
"""

import asyncio
import ssl
from datetime import datetime, timezone

import aiohttp

from backend.utils.logger import get_logger

logger = get_logger(__name__)

_API = "https://api.telegram.org"


def _build_ssl_context() -> ssl.SSLContext:
    """A verifying TLS context with a CA bundle that actually exists.

    Order of preference:
      1. truststore  — uses the operating system's certificate store, so a
         corporate/VPS root that the OS trusts is honoured.
      2. certifi     — Mozilla's bundle, shipped with the package.
      3. Python's default context.

    Hostname checking and certificate verification stay ON in every branch.
    """
    try:
        import truststore
        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except Exception:
        pass
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception as e:
        logger.warning(f"[Telegram] certifi unavailable ({e}); using default TLS context")
        return ssl.create_default_context()


class TelegramService:
    def __init__(self):
        self.bot_token = ""
        self.chat_id = ""
        # Diagnostics, read by /api/telegram/status. Without these the only
        # evidence of a delivery failure was a log line on the VPS.
        self.last_error: str | None = None
        self.last_error_at: str | None = None
        self.last_sent_at: str | None = None
        self.sent_count: int = 0
        self.failed_count: int = 0
        self._ssl_context: ssl.SSLContext | None = None

    # ── configuration ────────────────────────────────────────────────────────

    @property
    def configured(self) -> bool:
        return bool(self.bot_token and self.chat_id)

    @property
    def chat_ids(self) -> list[str]:
        return [c.strip() for c in (self.chat_id or "").split(",") if c.strip()]

    def update_config(self, token: str, chat_id: str) -> bool:
        """Load credentials. Returns True if the service is now usable.

        Called from three places now, not one: application startup, every
        config save, and each bot scan cycle. It used to be called ONLY from
        inside the bot's scan loop, so the credentials the user had saved in
        the frontend did not exist in this process until the bot was running —
        and any alert raised before that point (or while the bot was stopped)
        was dropped by the `configured` guard below without a word.
        """
        token = (token or "").strip()
        chat_id = (chat_id or "").strip()
        changed = (token != self.bot_token) or (chat_id != self.chat_id)
        self.bot_token = token
        self.chat_id = chat_id
        if changed:
            if self.configured:
                logger.info(f"[Telegram] Credentials loaded — {len(self.chat_ids)} chat id(s).")
                self.last_error = None
            else:
                missing = "bot token missing" if not token else "chat id missing"
                logger.warning(f"[Telegram] Notifications are DISABLED: {missing}.")
        return self.configured

    # ── formatting ───────────────────────────────────────────────────────────

    def escape_markdown(self, text: str) -> str:
        """Escape special characters for Telegram Markdown V1."""
        if not text:
            return ""
        text = str(text)
        text = text.replace("_", "\\_")
        text = text.replace("*", "\\*")
        text = text.replace("`", "\\`")
        text = text.replace("[", "\\[")
        return text

    # ── delivery ─────────────────────────────────────────────────────────────

    async def send_message(self, message: str, parse_mode: str = "Markdown") -> dict:
        """Send `message` to every configured chat id.

        Returns a result dict rather than None, so callers (and the test
        endpoint) can tell "sent" from "silently dropped". Never raises.
        """
        if not self.configured:
            reason = "no bot token configured" if not self.bot_token else "no chat id configured"
            self.last_error = f"Not sent — {reason}"
            self.last_error_at = datetime.now(timezone.utc).isoformat()
            logger.warning(f"[Telegram] Message dropped: {reason}.")
            return {"ok": False, "reason": reason, "results": []}

        if self._ssl_context is None:
            self._ssl_context = _build_ssl_context()

        url = f"{_API}/bot{self.bot_token}/sendMessage"
        results: list[dict] = []

        async def _send(session, cid):
            payload = {
                "chat_id": cid,
                "text": message,
                "parse_mode": parse_mode,
                "disable_web_page_preview": True,
            }
            try:
                async with session.post(url, json=payload) as resp:
                    body = await resp.text()
                    if resp.status != 200:
                        # Telegram answers with a JSON description that names the
                        # real problem (bad token, chat not found, bot not started
                        # by the user, group migrated). Keep it: it is the single
                        # most useful thing for diagnosing "not receiving alerts".
                        logger.warning(
                            f"[Telegram] API error for chat {cid}: HTTP {resp.status} {body}"
                        )
                        self.failed_count += 1
                        return {"chat_id": cid, "ok": False, "status": resp.status,
                                "detail": body[:500]}
                    self.sent_count += 1
                    return {"chat_id": cid, "ok": True, "status": 200}
            except Exception as e:
                logger.error(f"[Telegram] Failed to send to {cid}: {type(e).__name__}: {e}")
                self.failed_count += 1
                return {"chat_id": cid, "ok": False, "status": None,
                        "detail": f"{type(e).__name__}: {e}"}

        try:
            connector = aiohttp.TCPConnector(ssl=self._ssl_context)
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
                results = list(await asyncio.gather(*[_send(session, c) for c in self.chat_ids]))
        except Exception as e:
            detail = f"{type(e).__name__}: {e}"
            logger.error(f"[Telegram] Session error: {detail}")
            self.last_error = detail
            self.last_error_at = datetime.now(timezone.utc).isoformat()
            return {"ok": False, "reason": detail, "results": results}

        ok = bool(results) and all(r["ok"] for r in results)
        if ok:
            self.last_sent_at = datetime.now(timezone.utc).isoformat()
            self.last_error = None
        else:
            failed = [r for r in results if not r["ok"]]
            self.last_error = "; ".join(
                f"{r['chat_id']}: {r.get('detail') or r.get('status')}" for r in failed
            ) or "unknown failure"
            self.last_error_at = datetime.now(timezone.utc).isoformat()
        return {"ok": ok, "results": results}

    def status(self) -> dict:
        """Everything the frontend needs to explain why alerts are/aren't arriving."""
        return {
            "configured": self.configured,
            "has_token": bool(self.bot_token),
            "chat_id_count": len(self.chat_ids),
            "chat_ids_masked": [
                (c[:3] + "..." + c[-3:]) if len(c) > 7 else c for c in self.chat_ids
            ],
            "sent_count": self.sent_count,
            "failed_count": self.failed_count,
            "last_sent_at": self.last_sent_at,
            "last_error": self.last_error,
            "last_error_at": self.last_error_at,
        }


telegram_service = TelegramService()


async def load_telegram_config_for_user(user_id: str) -> bool:
    """Pull the saved bot token / chat id out of the DB into the service.

    Exists so credentials are live in this process without the bot running.
    """
    import json

    from sqlalchemy import select

    from backend.data.database import async_session
    from backend.data.models import UserConfigModel

    try:
        async with async_session() as session:
            row = (
                await session.execute(
                    select(UserConfigModel).where(UserConfigModel.user_id == user_id)
                )
            ).scalar_one_or_none()
            if not row or not row.config_json:
                return False
            cfg = json.loads(row.config_json)
            return telegram_service.update_config(
                cfg.get("telegram_bot_token", ""), cfg.get("telegram_chat_id", "")
            )
    except Exception as e:
        logger.warning(f"[Telegram] Could not load config for user {user_id}: {e}")
        return False


async def load_telegram_config_any_user() -> bool:
    """Startup path: load the first saved Telegram config found.

    This is a single-operator deployment (one MT5 login, one bot). Loading at
    startup is what makes alerts work before/without the bot running.
    """
    from sqlalchemy import select

    from backend.data.database import async_session
    from backend.data.models import UserConfigModel

    try:
        async with async_session() as session:
            rows = (await session.execute(select(UserConfigModel))).scalars().all()
        for row in rows:
            if not row.config_json:
                continue
            import json
            cfg = json.loads(row.config_json)
            if cfg.get("telegram_bot_token") and cfg.get("telegram_chat_id"):
                return telegram_service.update_config(
                    cfg["telegram_bot_token"], cfg["telegram_chat_id"]
                )
    except Exception as e:
        logger.warning(f"[Telegram] Startup config load failed: {e}")
    return False
