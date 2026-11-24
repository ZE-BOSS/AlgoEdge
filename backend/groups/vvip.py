"""
backend/groups/vvip.py

VVIP (phase 5): clients whose own MT5 accounts the bot trades.

What is here
  * the client's record, with limits clamped so no setting can trade a client
    harder than the plan allows (risk 0.1-2 %, daily loss 1-10 %, drawdown stop
    5-30 %), and the MT5 password stored Fernet-encrypted;
  * investor-portal routes (the investor audience, never the admin one):
    see your account, connect an MT5 account after accepting the terms, change
    risk, pause or resume;
  * the Paystack webhook: a verified payment makes the payer a VVIP member for
    the paid period and sends the VVIP invite. Off until PAYSTACK_SECRET_KEY is
    set - and per the plan, not switched on before the legal check.

What is NOT automatic yet: provisioning the MT5 terminal for a client. An
admin runs deploy/linux/add_instance.sh for the client and sets the account's
instance on the Clients page; that instance trades the client's account with
the same slots as live, filled in by the demo-live signal sync, sized from the
client's own balance.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.data.database import get_db
from backend.groups import settings as S
from backend.groups.models import ClientAccount, GroupMember, Payment, SignalGroup
from backend.utils.logger import get_logger

logger = get_logger(__name__)

LIMITS = {"risk_pct": (0.1, 2.0), "daily_loss_pct": (1.0, 10.0), "max_dd_pct": (5.0, 30.0)}
LEVELS = ("STARTER", "PRO", "ELITE")
TERMS = ("I understand trading carries risk and losses are possible; results are not guaranteed; the money stays "
         "in my own broker account; I can pause or disconnect the bot at any time; my MT5 password is stored "
         "encrypted and deleted when I disconnect.")


def clamp_limits(c: ClientAccount) -> ClientAccount:
    for k, (lo, hi) in LIMITS.items():
        v = getattr(c, k)
        setattr(c, k, min(hi, max(lo, float(v if v is not None else lo))))
    if c.level not in LEVELS:
        c.level = "STARTER"
    return c


def _cipher():
    from backend.services.api_key_store import _get_cipher
    c = _get_cipher()
    if c is None:
        raise HTTPException(503, "account connection is unavailable: the server has no ENCRYPTION_KEY")
    return c


def encrypt(secret: str) -> str:
    return _cipher().encrypt(secret.encode()).decode()


def decrypt(token: str) -> str:
    return _cipher().decrypt(token.encode()).decode()


def can(level: str, feature: str) -> bool:
    order = {lv: i for i, lv in enumerate(LEVELS)}
    need = {"live": "STARTER", "backtest": "PRO", "orderflow": "ELITE"}[feature]
    return order.get((level or "").upper(), -1) >= order[need]


# ── investor-portal routes ────────────────────────────────────────────────────

portal = APIRouter(prefix="/api/investor/vvip", tags=["vvip"])


async def _investor(request: Request, db: AsyncSession = Depends(get_db)):
    from backend.api.routes.investor_portal import current_investor, _bearer
    token = await _bearer(request)
    return await current_investor(token, db)


async def _account(db, inv_id: str) -> ClientAccount | None:
    return (await db.execute(select(ClientAccount).where(ClientAccount.user_id == str(inv_id)))).scalars().first()


async def _member(db, inv_id: str) -> GroupMember | None:
    return (await db.execute(select(GroupMember).where(GroupMember.user_id == str(inv_id)))).scalars().first()


@portal.get("/me")
async def me(db: AsyncSession = Depends(get_db), inv=Depends(_investor)):
    acc = await _account(db, inv.id)
    mem = await _member(db, inv.id)
    cfg = await S.load(db)
    level = (mem.level if mem and mem.status == "ACTIVE" and mem.tier == "VVIP" else "") or ""
    return {"level": level, "expires_at": mem.expires_at.isoformat() if mem and mem.expires_at else None,
            "features": {f: can(level, f) for f in ("live", "backtest", "orderflow")},
            "prices": cfg.get("vvip_levels"), "period_days": cfg.get("vvip_period_days"), "terms": TERMS,
            "account": None if acc is None else {
                "broker": acc.broker, "mt5_login": acc.mt5_login, "mt5_server": acc.mt5_server, "state": acc.state,
                "risk_pct": acc.risk_pct, "daily_loss_pct": acc.daily_loss_pct, "max_dd_pct": acc.max_dd_pct,
                "paused_reason": acc.paused_reason}}


class ConnectIn(BaseModel):
    broker: str
    mt5_login: str
    mt5_server: str
    mt5_password: str
    risk_pct: float = 1.0
    accept_terms: bool = False


@portal.post("/connect")
async def connect(body: ConnectIn, db: AsyncSession = Depends(get_db), inv=Depends(_investor)):
    mem = await _member(db, inv.id)
    if not (mem and mem.tier == "VVIP" and mem.status == "ACTIVE"):
        raise HTTPException(403, "VVIP membership needed to connect an account")
    if not body.accept_terms:
        raise HTTPException(400, "please accept the terms first")
    if not body.mt5_login.strip().isdigit():
        raise HTTPException(400, "the MT5 login is the account number (digits)")
    acc = await _account(db, inv.id) or ClientAccount(user_id=str(inv.id), member_id=mem.id)
    acc.broker, acc.mt5_login, acc.mt5_server = body.broker[:40], body.mt5_login.strip(), body.mt5_server[:100]
    acc.mt5_password_encrypted = encrypt(body.mt5_password)
    acc.risk_pct, acc.level = body.risk_pct, mem.level or "STARTER"
    acc.state = "NEW" if acc.state in (None, "", "STOPPED") else acc.state
    acc.terms_accepted_at = datetime.utcnow()
    clamp_limits(acc)
    db.add(acc)
    await db.commit()
    await _notify(f"🔌 VVIP client connected an MT5 account: login {acc.mt5_login} ({acc.broker}, {acc.mt5_server}), "
                  f"{acc.level}, risk {acc.risk_pct}%. Provision it on the Clients page.")
    return {"ok": True, "state": acc.state}


class ClientSettingsIn(BaseModel):
    risk_pct: float | None = None
    pause: bool | None = None
    disconnect: bool | None = None


@portal.patch("/settings")
async def client_settings(body: ClientSettingsIn, db: AsyncSession = Depends(get_db), inv=Depends(_investor)):
    acc = await _account(db, inv.id)
    if acc is None:
        raise HTTPException(404, "no account connected")
    if body.risk_pct is not None:
        acc.risk_pct = body.risk_pct
    if body.pause is not None:
        acc.state = "PAUSED" if body.pause else ("ACTIVE" if acc.instance else "NEW")
        acc.paused_reason = "paused by the client" if body.pause else ""
    if body.disconnect:
        acc.state, acc.mt5_password_encrypted, acc.paused_reason = "STOPPED", "", "disconnected by the client"
    clamp_limits(acc)
    await db.commit()
    await _notify(f"⚙️ VVIP client {acc.mt5_login}: risk {acc.risk_pct}%, state {acc.state}.")
    return {"ok": True, "state": acc.state, "risk_pct": acc.risk_pct}


async def _notify(text: str) -> None:
    try:
        from backend.groups.bot import group_bot
        await group_bot.notify_admins(text)
    except Exception:  # noqa: BLE001
        pass


# ── payments ──────────────────────────────────────────────────────────────────

payments = APIRouter(prefix="/api/vvip", tags=["vvip"])


def paystack_valid(raw: bytes, signature: str | None, secret: str) -> bool:
    if not secret or not signature:
        return False
    mac = hmac.new(secret.encode(), raw, hashlib.sha512).hexdigest()
    return hmac.compare_digest(mac, signature)


@payments.post("/paystack/webhook")
async def paystack_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    secret = os.getenv("PAYSTACK_SECRET_KEY", "").strip()
    raw = await request.body()
    if not secret:
        raise HTTPException(503, "payments are not switched on")
    if not paystack_valid(raw, request.headers.get("x-paystack-signature"), secret):
        raise HTTPException(401, "bad signature")
    ev = json.loads(raw or b"{}")
    if ev.get("event") != "charge.success":
        return {"ok": True}
    data = ev.get("data") or {}
    ref = str(data.get("reference") or "")
    if not ref:
        return {"ok": True}
    if (await db.execute(select(Payment).where(Payment.reference == ref))).scalars().first():
        return {"ok": True, "duplicate": True}
    meta = data.get("metadata") or {}
    level = str(meta.get("level") or "STARTER").upper()
    cfg = await S.load(db)
    days = int(cfg.get("vvip_period_days") or 30)
    mem = None
    if meta.get("telegram_id"):
        mem = (await db.execute(select(GroupMember).where(GroupMember.telegram_id == int(meta["telegram_id"])))).scalars().first()
    if mem is None and meta.get("investor_id"):
        mem = (await db.execute(select(GroupMember).where(GroupMember.user_id == str(meta["investor_id"])))).scalars().first()
    pay = Payment(member_id=mem.id if mem else None, user_id=str(meta.get("investor_id") or "") or None, provider="paystack",
                  reference=ref, amount=float(data.get("amount") or 0) / 100.0, currency=str(data.get("currency") or ""),
                  level=level if level in LEVELS else "STARTER", period_days=days, status="PAID", raw=data)
    db.add(pay)
    if mem is not None:
        base = mem.expires_at if mem.expires_at and mem.expires_at > datetime.utcnow() else datetime.utcnow()
        mem.tier, mem.level, mem.status, mem.expires_at = "VVIP", pay.level, "ACTIVE", base + timedelta(days=days)
    await db.commit()
    if mem is not None:
        try:
            from backend.groups.bot import group_bot
            groups = (await db.execute(select(SignalGroup).where(SignalGroup.tier == "VVIP"))).scalars().all()
            links = [await group_bot.api.invite_link(g.chat_id, f"VVIP {mem.id}") for g in groups]
            if links:
                await group_bot.api.send(mem.telegram_id, "🎉 Payment received - welcome to VVIP. Your invite (one use, 24 hours):\n" + "\n".join(links))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[VVIP] invite after payment failed: {e}")
    await _notify(f"💳 VVIP payment {ref}: {pay.amount:,.2f} {pay.currency}, {pay.level}"
                  + ("" if mem else " - no member matched; link it on the Members page."))
    return {"ok": True}
