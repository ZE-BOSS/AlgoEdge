"""
backend/groups/usdt.py

VVIP payments in USDT on Tron (TRC20), straight to the admin's own wallet.

  * An invoice asks for a unique amount (the plan price plus a few ten-thousandths,
    e.g. 300.0137 USDT) to the receiving address in settings, valid for an hour.
  * Every minute the bot reads the address's confirmed incoming USDT transfers from
    TronGrid and matches each to the open invoice with exactly that amount. A match
    makes the member VVIP for the period, records the payment, sends the invite and
    a receipt, and tells the admin. A transfer that matches nothing is recorded as
    UNMATCHED and the admin is told, to settle it by hand.
  * Only the receiving ADDRESS is ever used here - never a key or a seed phrase.
"""
from __future__ import annotations

import os
import random
from datetime import datetime, timedelta

import httpx
from sqlalchemy import select

from backend.groups.models import GroupMember, Invoice, Payment, SignalGroup
from backend.utils.logger import get_logger

logger = get_logger(__name__)

USDT_TRC20 = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"     # Tether's USDT contract on Tron
TRONGRID = "https://api.trongrid.io"
WINDOW_MIN = 60


def valid_address(a: str) -> bool:
    return bool(a) and a.startswith("T") and len(a) == 34 and a.isalnum()


async def new_invoice(s, cfg: dict, member: GroupMember | None, user_id: str | None, level: str) -> Invoice:
    level = level.upper()
    prices = cfg.get("vvip_levels") or {}
    if level not in prices:
        raise ValueError("unknown plan")
    addr = (cfg.get("usdt_trc20_address") or "").strip()
    if not valid_address(addr):
        raise ValueError("crypto payments are not set up yet")
    taken = {round(float(x), 4) for (x,) in (await s.execute(
        select(Invoice.amount).where(Invoice.status == "OPEN"))).all()}
    for _ in range(200):
        amt = round(float(prices[level]) + random.randint(1, 999) / 10_000, 4)
        if amt not in taken:
            break
    inv = Invoice(member_id=member.id if member else None, user_id=user_id, telegram_id=member.telegram_id if member else None,
                  level=level, amount=amt, address=addr, status="OPEN",
                  expires_at=datetime.utcnow() + timedelta(minutes=WINDOW_MIN))
    s.add(inv)
    await s.flush()
    return inv


def invoice_text(inv: Invoice, period_days: int) -> str:
    return (f"<b>VVIP {inv.level.title()}: {period_days} days</b>\n"
            f"Send exactly <code>{inv.amount:.4f}</code> USDT on the <b>TRON (TRC20)</b> network to:\n"
            f"<code>{inv.address}</code>\n\n"
            f"• The exact amount, decimals included: that is how we know it is yours.\n"
            f"• TRC20 only - USDT sent on another network cannot be recovered.\n"
            f"• Valid for {WINDOW_MIN} minutes. You'll get a message here as soon as it arrives.")


async def fetch_transfers(address: str, since_ms: int) -> list[dict]:
    headers = {"TRON-PRO-API-KEY": os.getenv("TRONGRID_API_KEY")} if os.getenv("TRONGRID_API_KEY") else {}
    url = f"{TRONGRID}/v1/accounts/{address}/transactions/trc20"
    params = {"only_to": "true", "only_confirmed": "true", "limit": 200, "contract_address": USDT_TRC20,
              "min_timestamp": since_ms, "order_by": "block_timestamp,asc"}
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(url, params=params, headers=headers)
        r.raise_for_status()
        rows = r.json().get("data") or []
    out = []
    for t in rows:
        tok = t.get("token_info") or {}
        if tok.get("address") != USDT_TRC20 or t.get("to") != address:
            continue
        dec = int(tok.get("decimals") or 6)
        out.append({"txid": t.get("transaction_id"), "from": t.get("from"),
                    "amount": int(t.get("value") or 0) / 10 ** dec, "ts": int(t.get("block_timestamp") or 0)})
    return out


async def activate(s, mem: GroupMember | None, level: str, days: int) -> None:
    if mem is None:
        return
    base = mem.expires_at if mem.expires_at and mem.expires_at > datetime.utcnow() else datetime.utcnow()
    mem.tier, mem.level, mem.status, mem.expires_at = "VVIP", level, "ACTIVE", base + timedelta(days=days)


async def check_payments(sf, cfg: dict, api=None, notify=None, transfers=None) -> list[str]:
    """One pass: expire old invoices, match new transfers. `transfers` is for tests."""
    addr = (cfg.get("usdt_trc20_address") or "").strip()
    if not valid_address(addr):
        return []
    days = int(cfg.get("vvip_period_days") or 30)
    done: list[str] = []
    dms: list[tuple[int, str]] = []
    async with sf() as s:
        now = datetime.utcnow()
        for inv in (await s.execute(select(Invoice).where(Invoice.status == "OPEN",
                                                          Invoice.expires_at < now - timedelta(minutes=30)))).scalars():
            inv.status = "EXPIRED"
        open_inv = (await s.execute(select(Invoice).where(Invoice.status == "OPEN"))).scalars().all()
        oldest = min([i.created_at or now for i in open_inv], default=now - timedelta(hours=2))
        if transfers is None:
            try:
                transfers = await fetch_transfers(addr, int((oldest - timedelta(minutes=5)).timestamp() * 1000))
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[USDT] TronGrid: {e}")
                return []
        seen = {r for (r,) in (await s.execute(select(Payment.reference).where(Payment.provider == "usdt"))).all()}
        for t in transfers:
            if not t["txid"] or t["txid"] in seen:
                continue
            seen.add(t["txid"])
            inv = next((i for i in open_inv if i.status == "OPEN" and abs(i.amount - t["amount"]) < 0.00005), None)
            if inv is None:
                s.add(Payment(provider="usdt", reference=t["txid"], amount=t["amount"], currency="USDT", status="UNMATCHED",
                              raw=t))
                done.append(f"⚠️ USDT received with no matching invoice: {t['amount']:.4f} USDT from {t['from']} "
                            f"(tx {t['txid'][:12]}…). Match it on the Members page.")
                continue
            inv.status, inv.txid, inv.paid_at = "PAID", t["txid"], datetime.utcnow()
            mem = await s.get(GroupMember, inv.member_id) if inv.member_id else None
            s.add(Payment(member_id=inv.member_id, user_id=inv.user_id, provider="usdt", reference=t["txid"],
                          amount=t["amount"], currency="USDT", level=inv.level, period_days=days, status="PAID", raw=t))
            await activate(s, mem, inv.level, days)
            done.append(f"💳 VVIP {inv.level} paid: {t['amount']:.4f} USDT"
                        f" from {(mem.name or mem.username or mem.telegram_id) if mem else inv.user_id} (tx {t['txid'][:12]}…)")
            if mem is not None and mem.telegram_id:
                links = []
                if api is not None:
                    for g in (await s.execute(select(SignalGroup).where(SignalGroup.tier == "VVIP"))).scalars():
                        try:
                            links.append(await api.invite_link(g.chat_id, f"VVIP {mem.id}"))
                        except Exception:  # noqa: BLE001
                            pass
                dms.append((mem.telegram_id,
                            f"✅ Payment received: {t['amount']:.4f} USDT - VVIP {inv.level.title()} until "
                            f"{mem.expires_at:%d %b %Y}.\nConnect your MT5 account on the portal to start."
                            + (("\nYour group invite (one use, 24 h):\n" + "\n".join(links)) if links else "")))
        await s.commit()
    for tg, text in dms:
        try:
            await api.send(tg, text)
        except Exception:  # noqa: BLE001
            pass
    for d in done:
        if notify is not None:
            try:
                await notify(d)
            except Exception:  # noqa: BLE001
                pass
    return done
