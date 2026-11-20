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

from datetime import datetime

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
from backend.utils.logger import get_logger

logger = get_logger(__name__)

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


@router.post("/daily-report")
async def daily_report_now(send: bool = True, user: User = Depends(get_current_user)):
    """Today's end-of-day report so far (Lagos day), sent to Telegram unless send=false."""
    from datetime import datetime, time as dtime, timedelta, timezone
    from zoneinfo import ZoneInfo

    from backend.copytrade import notify
    tz = ZoneInfo(notify.REPORT_TZ)
    today = datetime.now(timezone.utc).astimezone(tz).date()
    start = datetime.combine(today, dtime(0, 0), tz)
    text = await notify.collect_and_send_daily_report(start, start + timedelta(days=1),
                                                      today.strftime("%a %d %b %Y") + " (so far)", send=send)
    return {"sent": send, "text": text}


# ── backtest ────────────────────────────────────────────────────────────────
class BacktestIn(BaseModel):
    channel_ids: list[int]
    days: int = 60
    balance: float = 10000.0
    risk_pct: float = 1.0
    compound: bool = True
    max_open_total: int = 0
    strategy_backtest_ids: list[str] = []
    refresh: bool = False
    # None = each channel's own copy rules
    sl_fraction: float | None = None
    scheme: str | list[float] | None = None
    be_after_tp1: bool | None = None
    max_open: int | None = None
    chase_rule: str | None = None          # late signals: market / limit / skip
    tol_pct: float | None = None
    skip_if_tp1_passed: bool | None = None
    compare_chase: bool = False            # also replay every late-signal setting side by side
    optimise_exits: bool = False           # also tune stop fraction / split / breakeven (70% train, 30% test)


_bt_jobs: dict[str, dict] = {}
_bt_tasks: set = set()          # strong refs, so a running job is not garbage-collected


@router.get("/backtest/range")
async def backtest_range(channel_ids: str, user: User = Depends(get_current_user)):
    """How far back each channel's messages and the prices for its symbols go."""
    from backend.copytrade import backtest as ctbt
    ids = [int(x) for x in channel_ids.split(",") if x.strip().isdigit()]
    try:
        return await ctbt.data_range(user.id, ids)
    except RuntimeError as e:
        raise HTTPException(400, str(e))


@router.post("/backtest")
async def backtest_start(body: BacktestIn, user: User = Depends(get_current_user)):
    """Start a copy-trading backtest; poll GET /backtest/{job_id}."""
    import asyncio as _asyncio
    import uuid

    from backend.copytrade import backtest as ctbt
    if not body.channel_ids and not body.strategy_backtest_ids:
        raise HTTPException(400, "pick at least one channel or strategy backtest")
    if any(j["user_id"] == user.id and j["status"] == "running" for j in _bt_jobs.values()):
        raise HTTPException(409, "a copy-trading backtest is already running")
    jid = uuid.uuid4().hex[:12]
    job = {"user_id": user.id, "status": "running", "started": datetime.utcnow().isoformat(), "result": None, "error": None}
    _bt_jobs[jid] = job
    override = {"sl_fraction": body.sl_fraction, "scheme": body.scheme, "be_after_tp1": body.be_after_tp1,
                "max_open": body.max_open, "chase_rule": body.chase_rule, "tol_pct": body.tol_pct,
                "skip_if_tp1_passed": body.skip_if_tp1_passed}

    async def _go():
        try:
            job["result"] = await ctbt.run(user.id, body.channel_ids, body.days, body.balance, body.risk_pct,
                                           body.compound, override, body.strategy_backtest_ids,
                                           body.max_open_total, body.refresh, body.compare_chase,
                                           body.optimise_exits)
            job["status"] = "done"
        except Exception as e:  # noqa: BLE001
            logger.exception(f"[CT-BT] failed: {e}")
            job["status"], job["error"] = "error", f"{type(e).__name__}: {e}"

    task = _asyncio.create_task(_go())
    _bt_tasks.add(task)
    task.add_done_callback(_bt_tasks.discard)
    for k in [k for k, j in _bt_jobs.items() if j["status"] != "running" and k != jid][:-5]:
        _bt_jobs.pop(k, None)          # keep the last few results
    return {"job_id": jid}


@router.get("/backtest/{job_id}")
async def backtest_poll(job_id: str, user: User = Depends(get_current_user)):
    job = _bt_jobs.get(job_id)
    if not job or job["user_id"] != user.id:
        raise HTTPException(404, "no such backtest")
    return {k: v for k, v in job.items() if k != "user_id"}
