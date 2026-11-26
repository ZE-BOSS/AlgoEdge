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
from backend.groups.models import BacktestRequest, ClientAccount, GroupMember, Invoice, Payment, SignalGroup
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
            # orderflow = the account trades the order-flow-filtered strategies; the levels themselves are
            # never shown to clients (Databento live data is licensed for personal use)
            "prices": cfg.get("vvip_levels"), "period_days": cfg.get("vvip_period_days"), "terms": TERMS,
            "account": None if acc is None else {
                "broker": acc.broker, "mt5_login": acc.mt5_login, "mt5_server": acc.mt5_server, "state": acc.state,
                "live": acc.live or {}, "guard_state": acc.guard_state or "",
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


async def leader_pairs(db) -> list[tuple[str, str]]:
    """(strategy, symbol) of every enabled slot in this (the leader's) admin config: what clients trade."""
    from backend.data.models import User, UserConfigModel
    rows = (await db.execute(select(UserConfigModel.config_json).join(User, User.id == UserConfigModel.user_id)
                             .where(User.is_admin.is_(True)))).scalars().all()
    out: list[tuple[str, str]] = []
    for raw in rows:
        try:
            for sl in json.loads(raw or "{}").get("instrument_slots") or []:
                k = (str(sl.get("strategy_id") or ""), str(sl.get("symbol") or ""))
                if sl.get("enabled", True) and all(k) and k not in out:
                    out.append(k)
        except ValueError:
            continue
    return out


def _curve(rs: list[float], points: int = 60) -> list[float]:
    acc, out = 0.0, []
    for r in rs:
        acc += r
        out.append(round(acc, 2))
    if len(out) <= points:
        return out
    step = len(out) / points
    return [out[min(len(out) - 1, int(i * step))] for i in range(points)] + [out[-1]]


async def backtest_summaries(db, pairs: list[tuple[str, str]]) -> list[dict]:
    """The newest saved backtest for each pair, as client-safe numbers: no strategy names, no parameters."""
    from backend.data.models import BacktestRun, BacktestTrade
    from backend.groups import templates as T
    out = []
    per_market: dict[str, int] = {}
    for strat, sym in sorted(pairs):
        run = (await db.execute(select(BacktestRun).where(BacktestRun.strategy_id == strat, BacktestRun.symbol == sym)
                                .order_by(BacktestRun.created_at.desc()).limit(1))).scalars().first()
        if run is None:
            continue
        trades = (await db.execute(select(BacktestTrade.exit_time, BacktestTrade.pnl_r).where(
            BacktestTrade.backtest_id == run.id).order_by(BacktestTrade.exit_time))).all()
        rs = [float(r or 0) for _, r in trades]
        months: dict[str, float] = {}
        for t, r in trades:
            if t is not None:
                k = t.strftime("%Y-%m")
                months[k] = round(months.get(k, 0.0) + float(r or 0), 2)
        market = T.name(sym)
        per_market[market] = per_market.get(market, 0) + 1
        out.append({
            "label": f"{market} - system {chr(64 + per_market[market])}", "market": market,
            "from": run.start_date.date().isoformat() if run.start_date else None,
            "to": run.end_date.date().isoformat() if run.end_date else None,
            "trades": run.total_trades or len(rs), "win_rate": run.win_rate, "profit_factor": run.profit_factor,
            "expectancy_r": run.expectancy_r, "max_drawdown_pct": run.max_drawdown_pct,
            "total_r": round(sum(rs), 2), "curve": _curve(rs), "months": months,
            "tested_on": run.created_at.date().isoformat() if run.created_at else None})
    return out


@portal.get("/backtests")
async def my_backtests(db: AsyncSession = Depends(get_db), inv=Depends(_investor)):
    mem = await _member(db, inv.id)
    level = mem.level if mem and mem.status == "ACTIVE" and mem.tier == "VVIP" else ""
    if not can(level, "backtest"):
        raise HTTPException(403, "backtests come with the Pro and Elite plans")
    reqs = (await db.execute(select(BacktestRequest).where(BacktestRequest.user_id == str(inv.id))
                             .order_by(BacktestRequest.created_at.desc()).limit(20))).scalars().all()
    return {"systems": await backtest_summaries(db, await leader_pairs(db)),
            "requests": [{"market": r.market, "months": r.months, "note": r.note, "status": r.status,
                          "created_at": r.created_at.isoformat() if r.created_at else None} for r in reqs]}


def link_token(investor_id: str) -> str:
    """Telegram /start parameter tying a Telegram user to a portal account (<= 64 chars, [A-Za-z0-9_])."""
    from backend.api.deps import JWT_SECRET_KEY
    raw = str(investor_id).replace("-", "")
    mac = hmac.new(JWT_SECRET_KEY.encode(), ("tg:" + raw).encode(), hashlib.sha256).hexdigest()[:12]
    return f"L{raw}_{mac}"


def investor_from_token(tok: str) -> str | None:
    if not tok.startswith("L") or "_" not in tok:
        return None
    raw, mac = tok[1:].split("_", 1)
    if len(raw) != 32 or not hmac.compare_digest(link_token(raw)[-12:], mac):
        return None
    return f"{raw[:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:]}"


@portal.get("/telegram")
async def telegram_link(inv=Depends(_investor)):
    """Pay and get alerts through the Telegram bot: this link opens it with the account tied to this portal login."""
    from backend.groups.bot import group_bot
    if not group_bot.username:
        raise HTTPException(503, "the bot is not running right now - try again in a minute")
    return {"url": f"https://t.me/{group_bot.username}?start={link_token(str(inv.id))}"}


@portal.get("/invoice")
async def last_invoice(db: AsyncSession = Depends(get_db), inv=Depends(_investor)):
    x = (await db.execute(select(Invoice).where(Invoice.user_id == str(inv.id)).order_by(Invoice.created_at.desc())
                          .limit(1))).scalars().first()
    if x is None:
        return None
    return {"amount": f"{x.amount:.4f}", "address": x.address, "level": x.level, "status": x.status,
            "expires_at": x.expires_at.isoformat() if x.expires_at else None, "txid": x.txid}


class BacktestAsk(BaseModel):
    market: str
    months: int = 6
    note: str = ""


@portal.post("/backtests/request")
async def request_backtest(body: BacktestAsk, db: AsyncSession = Depends(get_db), inv=Depends(_investor)):
    mem = await _member(db, inv.id)
    level = mem.level if mem and mem.status == "ACTIVE" and mem.tier == "VVIP" else ""
    if not can(level, "backtest"):
        raise HTTPException(403, "backtests come with the Pro and Elite plans")
    open_n = len((await db.execute(select(BacktestRequest.id).where(
        BacktestRequest.user_id == str(inv.id), BacktestRequest.status == "OPEN"))).all())
    if open_n >= 3:
        raise HTTPException(429, "you already have 3 requests waiting")
    r = BacktestRequest(user_id=str(inv.id), market=body.market[:40], months=max(1, min(36, int(body.months))),
                        note=body.note[:300])
    db.add(r)
    await db.commit()
    await _notify(f"📊 VVIP backtest request ({level}) from {mem.name or mem.username or inv.id}: "
                  f"{r.market}, last {r.months} months. {r.note}".strip())
    return {"ok": True}


async def _notify(text: str) -> None:
    try:
        from backend.groups.bot import group_bot
        await group_bot.notify_admins(text)
    except Exception:  # noqa: BLE001
        pass


# ── live <-> client instances (client_guard.py runs in each) ─────────────────

def _shared():
    from pathlib import Path
    return Path(os.getenv("VVIP_SHARED_DIR", "/opt/algoedge/shared/vvip"))


def trade_alerts(prev: list[dict], now: list[dict], currency: str = "") -> list[str]:
    """Telegram texts for the client: trades that opened and trades that closed since the last status."""
    from backend.groups import templates as T
    old = {int(p["ticket"]): p for p in prev or []}
    new = {int(p["ticket"]): p for p in now or []}
    out = []
    for t, p in new.items():
        if t not in old:
            out.append(f"🔔 <b>New trade on your account</b>\n{p['side']} {T.e(T.name(p['symbol']))} {p['volume']:g} lot"
                       f" @ {T.px(p['price'])}\nStop {T.px(p['sl']) if p['sl'] else '-'}"
                       f" · Target {T.px(p['tp']) if p['tp'] else '-'}")
    for t, p in old.items():
        if t not in new:
            pl = float(p.get("profit") or 0)
            out.append(f"{'✅' if pl >= 0 else '❌'} <b>Trade closed</b>: {p['side']} {T.e(T.name(p['symbol']))} "
                       f"{'+' if pl >= 0 else ''}{pl:,.2f} {currency} (last price seen before it closed)")
    return out


async def sync_clients(session_factory=None, notify=None, notify_client=None) -> list[str]:
    """Write each provisioned client's control file and read back its status.
    Returns the notices sent (a stop firing, trading resuming)."""
    from backend.data.database import async_session
    sf = session_factory or async_session
    root = _shared()
    notices: list[str] = []
    client_msgs: list[tuple[int, str]] = []
    async with sf() as s:
        accs = (await s.execute(select(ClientAccount).where(ClientAccount.instance != ""))).scalars().all()
        for a in accs:
            st_file = root / f"{a.instance}.status.json"
            status = {}
            try:
                status = json.loads(st_file.read_text())
            except Exception:  # noqa: BLE001
                pass
            prev_guard = a.guard_state or ""
            if status.get("ok") and notify_client is not None and a.member_id and "positions" in status \
                    and "positions" in (a.live or {}):
                mem = await s.get(GroupMember, a.member_id)
                for text in trade_alerts((a.live or {}).get("positions"), status.get("positions"),
                                         status.get("currency") or ""):
                    if mem is not None and mem.telegram_id:
                        client_msgs.append((mem.telegram_id, text))
            if status.get("ok"):
                a.live = {k: status.get(k) for k in ("balance", "equity", "currency", "peak", "drawdown_pct",
                                                     "day_pnl", "open_positions", "bot_running", "updated",
                                                     "positions")}
                a.guard_state = status.get("state") or ""
                if a.guard_state != prev_guard:
                    if a.guard_state in ("DD_STOP", "DAY_STOP"):
                        if a.guard_state == "DD_STOP":
                            a.state = "PAUSED"
                        a.paused_reason = (status.get("reason") or "")[:200]
                        notices.append(f"⛔ VVIP client {a.mt5_login}: {a.paused_reason}. Open trades keep their stops.")
                    elif a.guard_state == "ACTIVE" and prev_guard in ("DD_STOP", "DAY_STOP"):
                        a.paused_reason = ""
                        notices.append(f"▶️ VVIP client {a.mt5_login}: trading again.")
            want = a.state if a.state in ("ACTIVE", "PAUSED", "STOPPED") else "PAUSED"
            ctl = {"state": want, "risk_pct": a.risk_pct, "daily_loss_pct": a.daily_loss_pct, "max_dd_pct": a.max_dd_pct}
            if want == "ACTIVE" and a.guard_state == "DD_STOP":
                ctl["resume_token"] = f"{a.id}:{int(datetime.utcnow().timestamp())}"
            try:
                root.mkdir(parents=True, exist_ok=True)
                f = root / f"{a.instance}.control.json"
                old = {}
                try:
                    old = json.loads(f.read_text())
                except Exception:  # noqa: BLE001
                    pass
                if ctl.get("resume_token") and old.get("resume_token") and old.get("state") == "ACTIVE":
                    ctl["resume_token"] = old["resume_token"]       # one resume per admin decision
                if ctl != old:
                    tmp = root / f".{a.instance}.control.tmp"
                    tmp.write_text(json.dumps(ctl))
                    os.replace(tmp, f)
            except OSError as e:
                logger.warning(f"[VVIP] control write for {a.instance} failed: {e}")
        await s.commit()
    for n in notices:
        if notify is not None:
            try:
                await notify(n)
            except Exception:  # noqa: BLE001
                pass
    for tg, text in client_msgs:
        try:
            await notify_client(tg, text)
        except Exception:  # noqa: BLE001
            pass
    return notices


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
