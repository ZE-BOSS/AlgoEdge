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

# Investor emails that are also worth a phone notification. The push carries the
# subject line only — never an amount's detail beyond it, since a notification
# shows on a lock screen.
PUSH_KINDS = {"deposit_confirmed", "deposit_rejected", "withdrawal_approved",
              "withdrawal_paid", "withdrawal_declined", "statement", "closure_approved",
              "trade_published"}
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


def queue(session, message: Message | None) -> None:
    """Send `message` if, and only if, this session's transaction commits."""
    if message is None or (not message.to and not message.push_only):
        return
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
        if (m.kind in PUSH_KINDS and m.investor_id and m.kind != "closure_approved"
                and prefsmod.wants_push(raw, m.kind)):
            try:
                await push(m.investor_id, m.subject, m.push_body or "Open the app for details.",
                           {"kind": m.kind})
            except Exception as exc:
                logger.error(f"[PUSH] {m.kind} to {m.investor_id} crashed: {exc}")


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
