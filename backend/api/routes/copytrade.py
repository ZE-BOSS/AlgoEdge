"""
backend/api/routes/copytrade.py

Telegram copy trading.

  GET    /api/copytrade/status              login + watcher state, settings, rule defaults
  PUT    /api/copytrade/settings            enabled / dry_run / rules
  POST   /api/copytrade/login/start         api_id, api_hash, phone -> Telegram sends a code
  POST   /api/copytrade/login/code          code (+ 2FA password) -> logged in, watching
  POST   /api/copytrade/logout
  GET    /api/copytrade/dialogs             channels/groups the account is in
  GET    /api/copytrade/dialogs/{chat_id}/history  read-only history of one of them
  GET    /api/copytrade/channels            watched channels
  POST   /api/copytrade/channels            add by chat_id (from dialogs) or link
  PATCH  /api/copytrade/channels/{id}       enabled / rules overrides
  DELETE /api/copytrade/channels/{id}
  GET    /api/copytrade/channels/{id}/history  recent messages, each with how it parses
  POST   /api/copytrade/parse               text -> parsed signal + the plan it would place now
  GET    /api/copytrade/signals             the log: every message acted on or skipped
  GET    /api/copytrade/signals/{id}/orders the orders a signal opened
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import get_current_user
from backend.copytrade.models import CopyTradeChannel, CopyTradeOrder, CopyTradeSettings, CopyTradeSignal
from backend.copytrade.parser import parse_message
from backend.copytrade.rules import DEFAULT_RULES, merge_rules
from backend.copytrade.service import copytrade_service
from backend.copytrade.telegram_client import telegram_manager
from backend.data.database import get_db
from backend.data.models import User

router = APIRouter(prefix="/api/copytrade", tags=["copytrade"])


def _ch(c: CopyTradeChannel) -> dict:
    return {"id": str(c.id), "chat_id": c.chat_id, "title": c.title, "username": c.username,
            "enabled": c.enabled, "rules": c.rules or {}, "created_at": c.created_at.isoformat() if c.created_at else None}


def _sig(x: CopyTradeSignal) -> dict:
    return {"id": str(x.id), "channel_id": str(x.channel_id), "msg_id": x.msg_id, "reply_to": x.reply_to,
            "edited": x.edited, "text": x.text, "kind": x.kind, "parsed": x.parsed, "symbol": x.symbol,
            "root_signal_id": str(x.root_signal_id) if x.root_signal_id else None, "status": x.status,
            "detail": x.detail, "actions": x.actions or [],
            "created_at": x.created_at.isoformat() if x.created_at else None}


@router.get("/status")
async def status(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    st = await copytrade_service.get_settings(db, user.id)
    await db.commit()
    return {"telegram": await telegram_manager.status(user.id), "enabled": st.enabled, "dry_run": st.dry_run,
            "rules": merge_rules(st.rules), "defaults": DEFAULT_RULES}


class SettingsIn(BaseModel):
    enabled: bool | None = None
    dry_run: bool | None = None
    rules: dict | None = None


@router.put("/settings")
async def put_settings(body: SettingsIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    st = await copytrade_service.get_settings(db, user.id)
    if body.enabled is not None:
        st.enabled = body.enabled
    if body.dry_run is not None:
        st.dry_run = body.dry_run
    if body.rules is not None:
        st.rules = {k: v for k, v in body.rules.items() if k in DEFAULT_RULES}
    await db.commit()
    if st.enabled and st.session_enc:
        await telegram_manager.start_watching(user.id)
    return {"enabled": st.enabled, "dry_run": st.dry_run, "rules": merge_rules(st.rules)}


class LoginStart(BaseModel):
    api_id: int
    api_hash: str
    phone: str


class LoginCode(BaseModel):
    code: str | None = None
    password: str | None = None


@router.post("/login/start")
async def login_start(body: LoginStart, user: User = Depends(get_current_user)):
    try:
        return await telegram_manager.start_login(user.id, body.api_id, body.api_hash.strip(), body.phone.strip())
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"{type(e).__name__}: {e}") from e


@router.post("/login/code")
async def login_code(body: LoginCode, user: User = Depends(get_current_user)):
    try:
        return await telegram_manager.complete_login(user.id, body.code, body.password)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"{type(e).__name__}: {e}") from e


@router.post("/logout")
async def logout(user: User = Depends(get_current_user)):
    await telegram_manager.logout(user.id)
    return {"ok": True}


@router.get("/dialogs")
async def dialogs(user: User = Depends(get_current_user)):
    try:
        return await telegram_manager.dialogs(user.id)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, str(e)) from e


@router.get("/dialogs/{chat_id}/history")
async def dialog_history(chat_id: int, limit: int = 100, user: User = Depends(get_current_user)):
    """Read-only history of any chat the account is in, without adding it as a
    copied channel (adding one turns copying on) - for evaluating a new group."""
    try:
        msgs = await telegram_manager.history(user.id, chat_id, min(max(limit, 1), 5000))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, str(e)) from e
    for m in msgs:
        m["parsed"] = parse_message(m["text"]).to_dict()
    return msgs


@router.get("/channels")
async def channels(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(CopyTradeChannel).where(CopyTradeChannel.user_id == user.id)
                             .order_by(CopyTradeChannel.created_at))).scalars().all()
    return [_ch(r) for r in rows]


class ChannelIn(BaseModel):
    chat_id: int | None = None
    title: str | None = None
    username: str | None = None
    link: str | None = None
    join: bool = True


@router.post("/channels")
async def add_channel(body: ChannelIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    info = {"chat_id": body.chat_id, "title": body.title or "", "username": body.username}
    if body.link:
        try:
            info = await telegram_manager.resolve(user.id, body.link, join=body.join)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(400, f"could not open {body.link}: {e}") from e
        if info.get("needs_join"):
            raise HTTPException(400, "private channel - allow joining to add it")
    if not info.get("chat_id"):
        raise HTTPException(400, "chat_id or link required")
    existing = (await db.execute(select(CopyTradeChannel).where(
        CopyTradeChannel.user_id == user.id, CopyTradeChannel.chat_id == int(info["chat_id"])))).scalars().first()
    if existing:
        existing.enabled = True
        row = existing
    else:
        row = CopyTradeChannel(user_id=user.id, chat_id=int(info["chat_id"]), title=info.get("title") or "",
                               username=info.get("username"), enabled=True, rules={})
        db.add(row)
    await db.commit()
    await telegram_manager.reload_channels(user.id)
    return _ch(row)


class ChannelPatch(BaseModel):
    enabled: bool | None = None
    rules: dict | None = None
    title: str | None = None


@router.patch("/channels/{cid}")
async def patch_channel(cid: int, body: ChannelPatch, user: User = Depends(get_current_user),
                        db: AsyncSession = Depends(get_db)):
    row = await db.get(CopyTradeChannel, cid)
    if row is None or row.user_id != user.id:
        raise HTTPException(404, "channel not found")
    if body.enabled is not None:
        row.enabled = body.enabled
    if body.title is not None:
        row.title = body.title
    if body.rules is not None:
        row.rules = {k: v for k, v in body.rules.items() if k in DEFAULT_RULES}
    await db.commit()
    await telegram_manager.reload_channels(user.id)
    return _ch(row)


@router.delete("/channels/{cid}")
async def delete_channel(cid: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    row = await db.get(CopyTradeChannel, cid)
    if row is None or row.user_id != user.id:
        raise HTTPException(404, "channel not found")
    await db.delete(row)
    await db.commit()
    await telegram_manager.reload_channels(user.id)
    return {"ok": True}


@router.get("/channels/{cid}/history")
async def channel_history(cid: int, limit: int = 100, user: User = Depends(get_current_user),
                          db: AsyncSession = Depends(get_db)):
    row = await db.get(CopyTradeChannel, cid)
    if row is None or row.user_id != user.id:
        raise HTTPException(404, "channel not found")
    try:
        msgs = await telegram_manager.history(user.id, row.chat_id, min(max(limit, 1), 5000))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, str(e)) from e
    for m in msgs:
        m["parsed"] = parse_message(m["text"]).to_dict()
    return msgs


class ParseIn(BaseModel):
    text: str
    channel_id: int | None = None


@router.post("/parse")
async def parse(body: ParseIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    p = parse_message(body.text)
    out: dict = {"parsed": p.to_dict(), "complete": p.complete}
    if p.kind == "OPEN":
        st = await copytrade_service.get_settings(db, user.id)
        ch = await db.get(CopyTradeChannel, body.channel_id) if body.channel_id else None
        rules = merge_rules(st.rules, ch.rules if ch else None)
        try:
            plan, why = await copytrade_service.plan_for(p.to_dict(), rules)
            out["plan"] = plan.to_dict() if plan is not None else None
            out["plan_reason"] = why
        except Exception as e:  # noqa: BLE001
            out["plan"], out["plan_reason"] = None, f"{type(e).__name__}: {e}"
    return out


@router.get("/signals")
async def signals(limit: int = 200, channel_id: int | None = None, user: User = Depends(get_current_user),
                  db: AsyncSession = Depends(get_db)):
    q = select(CopyTradeSignal).where(CopyTradeSignal.user_id == user.id)
    if channel_id:
        q = q.where(CopyTradeSignal.channel_id == channel_id)
    rows = (await db.execute(q.order_by(desc(CopyTradeSignal.created_at)).limit(min(limit, 1000)))).scalars().all()
    return [_sig(r) for r in rows]


@router.get("/signals/{sid}/orders")
async def signal_orders(sid: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(CopyTradeOrder).where(
        CopyTradeOrder.user_id == user.id, CopyTradeOrder.signal_id == sid).order_by(CopyTradeOrder.leg))).scalars().all()
    return [{"id": str(r.id), "leg": r.leg, "ticket": r.ticket, "kind": r.kind, "symbol": r.symbol, "side": r.side,
             "volume": r.volume, "entry": r.entry, "sl": r.sl, "tp": r.tp, "status": r.status, "detail": r.detail}
            for r in rows]
