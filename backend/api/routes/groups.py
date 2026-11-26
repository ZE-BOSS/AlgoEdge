"""
backend/api/routes/groups.py

Admin API for the signal groups: the bot's status and claim code, groups and
their tiers, routes (which source feeds which group - admin only), the post
feed, LLM drafts, members, VVIP client accounts and payments.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import require_admin
from backend.data.database import get_db
from backend.data.models import User
from backend.groups import settings as S
from backend.groups import templates as T
from backend.groups.models import (
    BacktestRequest, ClientAccount, GroupMember, GroupPost, GroupSignal, LLMDraft, Payment, SignalGroup, SignalRoute,
)

router = APIRouter(prefix="/api/groups", tags=["groups"])


def _row(o: Any, *skip: str) -> dict:
    out = {}
    for c in o.__table__.columns:
        if c.name in skip:
            continue
        v = getattr(o, c.name)
        out[c.name] = v.isoformat() if isinstance(v, datetime) else (str(v) if c.name in ("id", "group_id", "route_id", "member_id", "signal_id") and v is not None else v)
    return out


@router.get("/status")
async def status(db: AsyncSession = Depends(get_db), _: User = Depends(require_admin)):
    from backend.groups.bot import group_bot
    groups = (await db.execute(select(SignalGroup).order_by(SignalGroup.created_at))).scalars().all()
    routes = (await db.execute(select(SignalRoute).order_by(SignalRoute.created_at))).scalars().all()
    cfg = await S.load(db)
    queued = len((await db.execute(select(GroupPost.id).where(GroupPost.status == "QUEUED"))).all())
    failed = len((await db.execute(select(GroupPost.id).where(GroupPost.status == "FAILED",
                                                               GroupPost.created_at >= datetime.utcnow() - timedelta(days=1)))).all())
    return {"bot": group_bot.status(), "groups": [_row(g) for g in groups], "routes": [_row(r) for r in routes],
            "settings": cfg, "queued": queued, "failed_24h": failed}


class CreateGroupIn(BaseModel):
    title: str
    tier: str
    about: str = ""
    broadcast: bool = False


@router.post("/create")
async def create_group(body: CreateGroupIn, db: AsyncSession = Depends(get_db), user: User = Depends(require_admin)):
    """Create a Telegram group with the account logged in on the Copy Trading page, add the
    bot as admin and record it with its tier (posting stays off until you switch it on)."""
    tier = body.tier.upper()
    if tier not in ("GENERAL", "VIP", "VVIP"):
        raise HTTPException(400, "tier must be GENERAL, VIP or VVIP")
    if not body.title.strip():
        raise HTTPException(400, "give the group a name")
    from backend.groups.bot import group_bot
    if not group_bot.username:
        raise HTTPException(409, "the group bot is not running on this server - use the Live server")
    from backend.copytrade.telegram_client import telegram_manager
    try:
        r = await telegram_manager.create_group(str(user.id), body.title.strip(), body.about, group_bot.username,
                                                body.broadcast)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"Telegram refused: {e}") from e
    g = (await db.execute(select(SignalGroup).where(SignalGroup.chat_id == int(r["chat_id"])))).scalars().first()
    if g is None:
        g = SignalGroup(chat_id=int(r["chat_id"]))
        db.add(g)
    g.title, g.tier = r["title"], tier
    g.chat_type = "channel" if body.broadcast else "supergroup"
    if r.get("bot_added"):
        g.bot_status, g.can_post, g.can_invite, g.can_delete = "administrator", True, True, True
    await db.commit()
    await db.refresh(g)
    return {**_row(g), "invite_link": r.get("invite_link")}


class SettingsIn(BaseModel):
    patch: dict


@router.put("/settings")
async def put_settings(body: SettingsIn, db: AsyncSession = Depends(get_db), _: User = Depends(require_admin)):
    out = await S.save(db, body.patch)
    await db.commit()
    return out


class GroupIn(BaseModel):
    tier: str | None = None
    posting: bool | None = None


@router.patch("/groups/{gid}")
async def patch_group(gid: int, body: GroupIn, db: AsyncSession = Depends(get_db), _: User = Depends(require_admin)):
    g = await db.get(SignalGroup, gid)
    if g is None:
        raise HTTPException(404, "group not found")
    if body.tier is not None:
        t = body.tier.upper()
        if t not in ("", "GENERAL", "VIP", "VVIP"):
            raise HTTPException(400, "tier must be GENERAL, VIP or VVIP")
        g.tier = t
    if body.posting is not None:
        if body.posting and not g.tier:
            raise HTTPException(400, "set the group's tier first")
        if body.posting and not g.can_post:
            raise HTTPException(400, "the bot can't post there yet - make it an admin with posting rights")
        g.posting = body.posting
    await db.commit()
    await db.refresh(g)
    return _row(g)


class RouteIn(BaseModel):
    group_id: int
    source_type: str
    source_key: str
    symbol: str | None = None
    sample_every: int = 1
    delay_s: int = 0
    lifecycle: str = "full"
    headsups: bool = True
    max_losing_streak: int = 5
    enabled: bool = True


@router.post("/routes")
async def add_route(body: RouteIn, db: AsyncSession = Depends(get_db), _: User = Depends(require_admin)):
    if body.source_type not in ("strategy", "channel"):
        raise HTTPException(400, "source_type must be strategy or channel")
    if await db.get(SignalGroup, body.group_id) is None:
        raise HTTPException(404, "group not found")
    r = SignalRoute(**body.model_dump())
    r.symbol = body.symbol or None
    db.add(r)
    await db.commit()
    await db.refresh(r)
    return _row(r)


class RoutePatch(BaseModel):
    symbol: str | None = None
    sample_every: int | None = None
    delay_s: int | None = None
    lifecycle: str | None = None
    headsups: bool | None = None
    max_losing_streak: int | None = None
    enabled: bool | None = None
    resume: bool | None = None


@router.patch("/routes/{rid}")
async def patch_route(rid: int, body: RoutePatch, db: AsyncSession = Depends(get_db), _: User = Depends(require_admin)):
    r = await db.get(SignalRoute, rid)
    if r is None:
        raise HTTPException(404, "route not found")
    for k, v in body.model_dump(exclude_none=True).items():
        if k == "resume":
            if v:
                r.paused_reason = ""
        else:
            setattr(r, k, v or None if k == "symbol" else v)
    await db.commit()
    await db.refresh(r)
    return _row(r)


@router.delete("/routes/{rid}")
async def delete_route(rid: int, db: AsyncSession = Depends(get_db), _: User = Depends(require_admin)):
    r = await db.get(SignalRoute, rid)
    if r is not None:
        await db.delete(r)
        await db.commit()
    return {"ok": True}


@router.get("/sources")
async def sources(db: AsyncSession = Depends(get_db), user: User = Depends(require_admin)):
    """What a route can point at: the strategies this instance runs and its copy-trading channels."""
    out: dict[str, list] = {"strategies": [], "channels": []}
    try:
        from backend.services.bot_service import bot_service
        diag = bot_service.slot_diagnostics() or []
        seen = set()
        for d in diag:
            k = (d.get("strategy_id"), d.get("symbol"))
            if all(k) and k not in seen:
                seen.add(k)
                out["strategies"].append({"strategy_id": k[0], "symbol": k[1]})
    except Exception:  # noqa: BLE001
        pass
    try:
        from backend.copytrade.models import CopyTradeChannel
        chans = (await db.execute(select(CopyTradeChannel).where(CopyTradeChannel.user_id == user.id))).scalars().all()
        out["channels"] = [{"id": str(c.id), "title": c.title, "copying": bool(c.enabled)} for c in chans]
    except Exception:  # noqa: BLE001
        pass
    return out


@router.get("/feed")
async def feed(limit: int = 100, db: AsyncSession = Depends(get_db), _: User = Depends(require_admin)):
    posts = (await db.execute(select(GroupPost).order_by(desc(GroupPost.created_at)).limit(min(limit, 500)))).scalars().all()
    sigs = (await db.execute(select(GroupSignal).order_by(desc(GroupSignal.created_at)).limit(100))).scalars().all()
    return {"posts": [_row(p) for p in posts], "signals": [_row(g) for g in sigs]}


@router.get("/drafts")
async def drafts(db: AsyncSession = Depends(get_db), _: User = Depends(require_admin)):
    rows = (await db.execute(select(LLMDraft).order_by(desc(LLMDraft.created_at)).limit(100))).scalars().all()
    return [_row(d) for d in rows]


@router.post("/drafts/{did}/{act}")
async def decide_draft(did: int, act: str, _: User = Depends(require_admin)):
    from backend.groups.bot import group_bot
    if act not in ("approve", "skip"):
        raise HTTPException(400, "approve or skip")
    return {"result": await group_bot.decide_draft(did, "ap" if act == "approve" else "sk")}


class PostIn(BaseModel):
    group_id: int
    text: str
    pin: bool = False


@router.post("/post")
async def post(body: PostIn, db: AsyncSession = Depends(get_db), _: User = Depends(require_admin)):
    """Post as the bot (plain text, escaped), or the risk notice with pin=true and text='RISK_PIN'."""
    g = await db.get(SignalGroup, body.group_id)
    if g is None:
        raise HTTPException(404, "group not found")
    from backend.groups.bot import group_bot
    text = T.RISK_PIN if body.text == "RISK_PIN" else T.e(body.text)
    m = await group_bot.api.send(g.chat_id, text)
    if body.pin:
        try:
            await group_bot.api.pin(g.chat_id, m["message_id"])
        except Exception as e:  # noqa: BLE001
            return {"message_id": m.get("message_id"), "pinned": False, "error": str(e)}
    db.add(GroupPost(chat_id=g.chat_id, kind="admin", text=text, status="SENT", message_id=m.get("message_id"),
                     sent_at=datetime.utcnow(), send_at=datetime.utcnow()))
    await db.commit()
    return {"message_id": m.get("message_id"), "pinned": body.pin}


@router.get("/members")
async def members(db: AsyncSession = Depends(get_db), _: User = Depends(require_admin)):
    rows = (await db.execute(select(GroupMember).order_by(desc(GroupMember.updated_at)).limit(500))).scalars().all()
    return [_row(m) for m in rows]


class MemberPatch(BaseModel):
    status: str | None = None
    tier: str | None = None
    level: str | None = None
    days: int | None = None          # extend from now
    note: str | None = None


@router.patch("/members/{mid}")
async def patch_member(mid: int, body: MemberPatch, db: AsyncSession = Depends(get_db), _: User = Depends(require_admin)):
    m = await db.get(GroupMember, mid)
    if m is None:
        raise HTTPException(404, "member not found")
    if body.tier is not None:
        m.tier = body.tier.upper()
    if body.level is not None:
        m.level = body.level.upper()
    if body.status is not None:
        m.status = body.status.upper()
    if body.days is not None:
        m.expires_at = datetime.utcnow() + timedelta(days=int(body.days))
    if body.note is not None:
        m.note = body.note[:300]
    await db.commit()
    await db.refresh(m)
    return _row(m)


@router.post("/members/{mid}/{act}")
async def member_decision(mid: int, act: str, _: User = Depends(require_admin)):
    from backend.groups.bot import group_bot
    if act not in ("approve", "deny"):
        raise HTTPException(400, "approve or deny")
    return {"result": await group_bot.decide_member(mid, "ap" if act == "approve" else "dn")}


# ── VVIP (phase 5) ────────────────────────────────────────────────────────────

@router.get("/clients")
async def clients(db: AsyncSession = Depends(get_db), _: User = Depends(require_admin)):
    rows = (await db.execute(select(ClientAccount).order_by(desc(ClientAccount.created_at)))).scalars().all()
    pays = (await db.execute(select(Payment).order_by(desc(Payment.created_at)).limit(200))).scalars().all()
    reqs = (await db.execute(select(BacktestRequest).order_by(desc(BacktestRequest.created_at)).limit(100))).scalars().all()
    return {"clients": [_row(c, "mt5_password_encrypted") for c in rows], "payments": [_row(p, "raw") for p in pays],
            "requests": [_row(r) for r in reqs]}


@router.post("/backtest-requests/{rid}/{status}")
async def close_backtest_request(rid: int, status: str, db: AsyncSession = Depends(get_db), _: User = Depends(require_admin)):
    r = await db.get(BacktestRequest, rid)
    if r is None or status.upper() not in ("DONE", "DECLINED", "OPEN"):
        raise HTTPException(404, "not found")
    r.status = status.upper()
    await db.commit()
    return {"ok": True}


class ClientPatch(BaseModel):
    state: str | None = None
    level: str | None = None
    risk_pct: float | None = None
    daily_loss_pct: float | None = None
    max_dd_pct: float | None = None
    instance: str | None = None
    paused_reason: str | None = None


@router.patch("/clients/{cid}")
async def patch_client(cid: int, body: ClientPatch, db: AsyncSession = Depends(get_db), _: User = Depends(require_admin)):
    c = await db.get(ClientAccount, cid)
    if c is None:
        raise HTTPException(404, "client not found")
    from backend.groups.vvip import clamp_limits
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(c, k, v.upper() if k in ("state", "level") else v)
    clamp_limits(c)
    await db.commit()
    await db.refresh(c)
    return _row(c, "mt5_password_encrypted")
