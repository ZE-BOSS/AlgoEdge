"""
backend/notify/outbox.py

The after-commit outbox and the Resend transport.

WHY NOT FASTAPI BACKGROUNDTASKS: in this FastAPI version a background task runs
before a `yield` dependency's exit code — i.e. before get_db commits. An email
sent from one could confirm a deposit that the commit then failed to save.
Measured, not assumed; see PHASE-4 doc.

MODES (EMAIL_MODE): `live` sends through Resend; `log` writes the email_log row
and sends nothing. With no RESEND_API_KEY, live degrades to log and says so once.
"""

from __future__ import annotations

import asyncio
import base64
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx
from sqlalchemy import event
from sqlalchemy.orm import Session

from backend.utils.logger import get_logger

logger = get_logger(__name__)

RESEND_URL = "https://api.resend.com/emails"
EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"

# Investor messages that also go to the notification feed (the bell, web and
# app) and, if their settings allow, to their phone and browser. The value is
# the screen a tap opens. A push shows on a lock screen, so it carries the
# subject line and a short line only; the detail is in the app.
NOTICE_LINK = {
    "deposit_claimed": "/money", "deposit_confirmed": "/", "deposit_rejected": "/money",
    "withdrawal_received": "/activity", "withdrawal_approved": "/activity",
    "withdrawal_paid": "/activity", "withdrawal_declined": "/activity",
    "statement": "/activity", "fee_charged": "/activity",
    "trade_opened": "/trades", "trade_published": "/trades",
    "password_changed": "/account", "payout_changed": "/account", "closure_requested": "/account",
}
PUSH_KINDS = set(NOTICE_LINK)
_pending: set[asyncio.Task] = set()
_warned = False


@dataclass
class Message:
    kind: str
    to: str
    subject: str
    html: str
    text: str
    investor_id: str | None = None
    attachments: list[tuple[str, bytes]] = field(default_factory=list)   # (filename, bytes)
    reply_to: str | None = None
    # a phone notification with no email (e.g. "a trade closed"); `to` may be empty
    push_only: bool = False
    push_body: str | None = None
    # the investor's saved settings JSON, captured when the message is made (the
    # investor row is already loaded then); None = defaults
    preferences: str | None = None
    # the fuller line in the in-app feed (behind their sign-in); default push_body
    feed_body: str | None = None


def queue(session, message: Message | None) -> None:
    """Send `message` if, and only if, this session's transaction commits."""
    if message is None or (not message.to and not message.push_only):
        return
    if message.investor_id and message.kind in NOTICE_LINK:
        # the feed entry is part of the same transaction as the event itself
        from backend.investor.models import InvestorNotice
        session.add(InvestorNotice(
            investor_id=message.investor_id, kind=message.kind, title=message.subject[:255],
            body=message.feed_body or message.push_body, link=NOTICE_LINK[message.kind]))
    sync = getattr(session, "sync_session", session)
    # Open the transaction now if it is not already. With no open transaction,
    # a rollback fires no event at all while the next commit does — so the
    # message would survive the rollback and go out on that commit.
    if not sync.in_transaction():
        sync.begin()
    sync.info.setdefault("outbox", []).append(message)


@event.listens_for(Session, "after_commit")
def _after_commit(sync_session):
    messages = sync_session.info.pop("outbox", None)
    if not messages:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.error(f"[EMAIL] {len(messages)} message(s) committed with no event loop — not sent")
        return
    task = loop.create_task(_deliver_all(messages))
    _pending.add(task)
    task.add_done_callback(_pending.discard)


# after_SOFT_rollback, not after_rollback: the latter fires only when a
# database transaction was actually open, so a rollback of a session that had
# queued a message but not yet touched the database would keep the message,
# and the next commit would send it. Caught by a test.
@event.listens_for(Session, "after_soft_rollback")
def _after_rollback(sync_session, previous_transaction):
    dropped = sync_session.info.pop("outbox", None)
    if dropped:
        logger.info(f"[EMAIL] rollback — {len(dropped)} message(s) discarded, not sent")


async def drain() -> None:
    """Wait for everything in flight. Tests call this; so does shutdown."""
    while _pending:
        await asyncio.gather(*list(_pending), return_exceptions=True)


def mode() -> str:
    global _warned
    wanted = os.getenv("EMAIL_MODE", "live").strip().lower()
    if wanted == "live" and not os.getenv("RESEND_API_KEY"):
        if not _warned:
            logger.warning("[EMAIL] EMAIL_MODE=live but RESEND_API_KEY is empty — logging only")
            _warned = True
        return "log"
    return "live" if wanted == "live" else "log"


# The transport is swappable so tests can stand in for Resend without a network,
# and the session factory so the log rows land in the database under test.
_transport: httpx.AsyncBaseTransport | None = None
_session_factory = None


def use_session_factory(factory) -> None:
    global _session_factory
    _session_factory = factory


def use_transport(transport: httpx.AsyncBaseTransport | None) -> None:
    global _transport
    _transport = transport


async def _deliver_all(messages: list[Message]) -> None:
    from backend.investor import prefs as prefsmod
    for m in messages:
        raw = m.preferences
        if not m.push_only:
            if prefsmod.wants_email(raw, m.kind):
                try:
                    await deliver(m)
                except Exception as exc:          # one bad message must not stop the rest
                    logger.error(f"[EMAIL] {m.kind} to {m.to} crashed: {exc}")
            else:
                logger.info(f"[EMAIL] {m.kind} to {m.to} skipped: switched off in their settings")
        if m.kind in NOTICE_LINK and m.investor_id:
            await notify(m.investor_id, m.kind, m.subject, m.push_body or "Open the app for details.",
                         feed_body=m.feed_body, preferences=raw)


async def notify(investor_id: str, kind: str, title: str, body: str, *,
                 feed_body: str | None = None, preferences: str | None = None) -> None:
    """Phone and browser pushes, if their settings allow. (The feed entry was
    written with the event, in queue().)"""
    from backend.investor import prefs as prefsmod
    link = NOTICE_LINK.get(kind, "/")
    if not prefsmod.wants_push(preferences, kind):
        logger.info(f"[PUSH] {kind} to {investor_id} skipped: switched off in their settings")
        return
    data = {"kind": kind, "link": link}
    for send in (push, web_push):
        try:
            await send(investor_id, title, body, data)
        except Exception as exc:
            logger.error(f"[PUSH] {kind} to {investor_id} via {send.__name__} crashed: {exc}")


def _factory():
    if _session_factory is not None:
        return _session_factory
    from backend.data.database import async_session
    return async_session


async def web_push(investor_id: str, title: str, body: str, data: dict | None = None) -> int:
    """Send to every browser this investor turned notifications on in.

    Uses the same VAPID keys as the operator console. Subscriptions the push
    service says are gone (404/410) are deleted. Returns how many were sent.
    """
    import json

    from sqlalchemy import delete, select

    from backend.config import settings
    from backend.investor.models import InvestorWebPush
    live = push_mode() != "log"
    if live and not settings.vapid.private_key:
        return 0                     # browser notifications are not set up on this server
    if live:
        try:
            from pywebpush import WebPushException, webpush
        except ImportError:
            logger.warning("[WEBPUSH] pywebpush is not installed")
            return 0
    async with _factory()() as s:
        subs = (await s.execute(select(InvestorWebPush)
                                .where(InvestorWebPush.investor_id == investor_id))).scalars().all()
        if not subs:
            return 0
        if not live:
            logger.info(f"[WEBPUSH] (log mode) {len(subs)} browser(s) of {investor_id}: {title}")
            return len(subs)
        payload = json.dumps({"title": title, "body": body, "data": data or {}})
        claims = {"sub": settings.vapid.claims_email or "mailto:no-reply@alphavantiqcapital.com"}
        gone, sent = [], 0
        for sub in subs:
            info = {"endpoint": sub.endpoint, "keys": {"p256dh": sub.p256dh, "auth": sub.auth}}
            try:
                await asyncio.to_thread(webpush, subscription_info=info, data=payload,
                                        vapid_private_key=settings.vapid.private_key,
                                        vapid_claims=dict(claims), ttl=86400)
                sent += 1
            except WebPushException as exc:
                code = getattr(getattr(exc, "response", None), "status_code", None)
                if code in (404, 410):
                    gone.append(sub.id)
                else:
                    logger.warning(f"[WEBPUSH] {investor_id}: {exc}")
        if gone:
            await s.execute(delete(InvestorWebPush).where(InvestorWebPush.id.in_(gone)))
            await s.commit()
        return sent


def push_mode() -> str:
    return "log" if os.getenv("PUSH_MODE", "live").strip().lower() == "log" else "live"


async def push(investor_id: str, title: str, body: str, data: dict | None = None) -> int:
    """Send a notification to every phone this investor has the app on.

    Tokens Expo reports as DeviceNotRegistered (app uninstalled) are deleted, so
    a dead phone is not retried forever. Returns how many were sent.
    """
    from sqlalchemy import delete, select

    from backend.investor.models import InvestorDevice
    factory = _session_factory
    if factory is None:
        from backend.data.database import async_session as factory
    async with factory() as s:
        tokens = (await s.execute(select(InvestorDevice.push_token)
                                  .where(InvestorDevice.investor_id == investor_id))).scalars().all()
        if not tokens:
            return 0
        if push_mode() == "log":
            logger.info(f"[PUSH] (log mode) {len(tokens)} device(s) of {investor_id}: {title}")
            return len(tokens)
        payload = [{"to": t, "title": title, "body": body, "data": data or {}, "sound": "default",
                    "channelId": "account"} for t in tokens]
        async with httpx.AsyncClient(timeout=15, transport=_transport) as client:
            r = await client.post(EXPO_PUSH_URL, json=payload, headers={"Accept": "application/json"})
        if r.status_code >= 300:
            raise RuntimeError(f"Expo push {r.status_code}: {r.text[:300]}")
        dead = [t for t, ticket in zip(tokens, r.json().get("data", []))
                if ticket.get("status") == "error"
                and (ticket.get("details") or {}).get("error") == "DeviceNotRegistered"]
        if dead:
            await s.execute(delete(InvestorDevice).where(InvestorDevice.push_token.in_(dead)))
            await s.commit()
            logger.info(f"[PUSH] removed {len(dead)} uninstalled device(s)")
        return len(tokens) - len(dead)


async def deliver(m: Message) -> str:
    """Record the message, then send it. Returns the final state."""
    from backend.investor.models import EmailLog
    if _session_factory is None:
        from backend.data.database import async_session as factory
    else:
        factory = _session_factory

    async with factory() as s:
        row = EmailLog(kind=m.kind, to_address=m.to, subject=m.subject[:255],
                       investor_id=m.investor_id, state="queued",
                       attachments=[name for name, _ in m.attachments] or None)
        s.add(row)
        await s.commit()

        if mode() == "log":
            row.state = "logged"
            logger.info(f"[EMAIL] (log mode) {m.kind} to {m.to}: {m.subject}")
        else:
            try:
                row.provider_id = await _send_resend(m, idempotency_key=f"email-{row.id}")
                row.state = "sent"
                row.sent_at = datetime.now(timezone.utc)
                logger.info(f"[EMAIL] sent {m.kind} to {m.to} ({row.provider_id})")
            except Exception as exc:
                row.state = "failed"
                row.error = str(exc)[:2000]
                logger.error(f"[EMAIL] {m.kind} to {m.to} failed: {exc}")
        await s.commit()
        return row.state


async def _send_resend(m: Message, *, idempotency_key: str) -> str:
    payload = {
        "from": os.getenv("EMAIL_FROM", "Alphavantiq Capital <no-reply@alphavantiqcapital.com>"),
        "to": [m.to],
        "subject": m.subject,
        "html": m.html,
        "text": m.text,
    }
    reply_to = m.reply_to or os.getenv("EMAIL_REPLY_TO")
    if reply_to:
        payload["reply_to"] = reply_to
    if m.attachments:
        payload["attachments"] = [{"filename": name, "content": base64.b64encode(data).decode()}
                                  for name, data in m.attachments]
    async with httpx.AsyncClient(timeout=20, transport=_transport) as client:
        r = await client.post(RESEND_URL, json=payload, headers={
            "Authorization": f"Bearer {os.getenv('RESEND_API_KEY', '')}",
            # Resend drops a repeat with the same key, so a retried delivery
            # cannot send the same statement twice
            "Idempotency-Key": idempotency_key,
        })
    if r.status_code >= 300:
        raise RuntimeError(f"Resend {r.status_code}: {r.text[:500]}")
    return r.json().get("id", "")
