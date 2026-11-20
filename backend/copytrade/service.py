"""
backend/copytrade/service.py

Acts on Telegram signal messages: opens the trade a signal describes (split
across its TP levels), and applies the follow-ups the channel posts - breakeven,
move SL, move TP, close all or part, cancel pending orders.

    handle_message(...)   one message from a watched channel (new or edited)
    monitor_once()        housekeeping every few seconds: notice fills and
                          closes, expire unfilled pending orders, and move the
                          rest of a trade to breakeven after TP1/TP2 if the
                          rules say so

Which trade a follow-up belongs to:
  1. the message it REPLIES to (or that message's own signal, if the reply is to
     a follow-up), else
  2. the most recent active signal on the same channel - for the same symbol
     when the follow-up names one.
"close all trades" applies to every active signal on the channel.

Dry run (the default until the user turns it off) does everything except send
to MT5: plans are computed against live prices and logged, orders are recorded
as PAPER, and follow-ups update the paper orders - so a channel can be watched
for a few days before any money is at risk.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import desc, select

from backend.copytrade import mt5ops, notify
from backend.copytrade.models import CopyTradeChannel, CopyTradeOrder, CopyTradeSettings, CopyTradeSignal
from backend.copytrade.parser import parse_message
from backend.copytrade.rules import (breakeven_price, build_open_plan, legs_to_close, merge_rules, resolve_symbol,
                                     pending_at_market, stop_fits, tighten_stop, trail_target)
from backend.data.database import async_session
from backend.utils.logger import get_logger

logger = get_logger(__name__)

ACTIVE_SIGNAL = ("EXECUTED", "PARTIAL", "DRY_RUN")
MISSING_GRACE = timedelta(minutes=15)   # a ticket the account has no trace of for this long was placed elsewhere
LIVE_ORDER = ("OPEN", "PENDING", "PAPER", "PAPER_PENDING")


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


FOLLOW_UP_KINDS = ("BREAKEVEN", "MOVE_SL", "MOVE_TP", "CLOSE", "CANCEL")


def _trade_fields(pd: dict) -> tuple:
    """What an edit can change that matters for trading."""
    return tuple((k, json_safe(pd.get(k))) for k in ("kind", "side", "symbol", "order_type", "entry", "entry_low",
                                                    "entry_high", "sl", "tps", "new_price", "close_fraction",
                                                    "tp_index"))


def json_safe(v):
    return tuple(v) if isinstance(v, list) else v


def _follow_gate(pd: dict) -> str | None:
    kind = pd.get("kind")
    if kind == "CLOSE":
        return "follow_close" if (pd.get("close_fraction") or 1.0) >= 0.999 else "follow_partial_close"
    return {"BREAKEVEN": "follow_breakeven", "MOVE_SL": "follow_move_sl", "MOVE_TP": "follow_move_tp",
            "CANCEL": "follow_cancel"}.get(kind)


def added_lines(before: str, after: str) -> list[str]:
    """Lines in `after` that were not in `before` (emoji and spacing ignored)."""
    norm = lambda x: " ".join("".join(ch for ch in x if ch.isascii()).split()).upper()  # noqa: E731
    seen = {norm(x) for x in (before or "").splitlines()}
    return [x.strip() for x in (after or "").splitlines() if norm(x) and norm(x) not in seen]


def _entry_ref(pd: dict) -> float | None:
    lo, hi = pd.get("entry_low"), pd.get("entry_high")
    if lo is not None and hi is not None:
        return (float(lo) + float(hi)) / 2
    return float(pd["entry"]) if pd.get("entry") is not None else None


def remap_entry(price: float | None, old: dict, new: dict) -> float | None:
    """Where a waiting leg goes when the entry is edited: its place in the old
    zone mapped onto the new zone, else shifted by the entry's move."""
    if price is None:
        return None
    olo, ohi, nlo, nhi = old.get("entry_low"), old.get("entry_high"), new.get("entry_low"), new.get("entry_high")
    if None not in (olo, ohi, nlo, nhi) and float(ohi) > float(olo):
        f = (float(price) - float(olo)) / (float(ohi) - float(olo))
        return round(float(nlo) + f * (float(nhi) - float(nlo)), 5)
    a, b = _entry_ref(old), _entry_ref(new)
    if a is None or b is None:
        return None
    return round(float(price) + (b - a), 5)


def latency_breakdown(posted_at: datetime | None, received_at: float | None, t_lock: float, t_in: float,
                      t_done: float) -> dict:
    """Seconds spent in each step, from the provider pressing send to our
    orders being at the broker. Telegram dates have 1 s resolution."""
    out = {"waited_for_lock_s": round(t_in - t_lock, 3), "processing_s": round(t_done - t_in, 3)}
    if received_at:
        out["handler_s"] = round(t_lock - received_at, 3)
        if posted_at is not None:
            p = posted_at if posted_at.tzinfo else posted_at.replace(tzinfo=timezone.utc)
            out["telegram_delivery_s"] = round(received_at - p.timestamp(), 1)
            out["total_s"] = round(t_done - p.timestamp(), 1)
    return out


class CopyTradeService:
    def __init__(self) -> None:
        self.latency: list[dict] = []           # last 200 signals: where the time went
        self._lock = asyncio.Lock()        # one message at a time: follow-ups must see the orders of the signal before them
        self._tp_closed: dict[int, bool] = {}   # ticket -> closed by its take profit (history never changes)
        self._reached: dict[int, int] = {}      # signal id -> highest of its TPs price has reached
        self._trail_note: dict[int, float] = {} # order id -> stop we last failed to set (log once)
        self._pending_be: dict[int, float] = {} # order id -> breakeven he asked for that didn't fit the price yet
        self._gone_seen: dict[int, int] = {}    # ticket -> rounds in a row MT5 said it is gone
        self._missing_since: dict[int, datetime] = {}  # ticket -> first time this account had no trace of it
        self._ff_done: set[int] = set()         # signal ids whose first fill has been handled

    # ── settings ────────────────────────────────────────────────────────────
    async def get_settings(self, s, user_id: str) -> CopyTradeSettings:
        row = await s.get(CopyTradeSettings, user_id)
        if row is None:
            row = CopyTradeSettings(user_id=user_id, enabled=False, dry_run=True, rules={})
            s.add(row)
            await s.flush()
        return row

    # ── entry point ─────────────────────────────────────────────────────────
    async def handle_message(self, user_id: str, channel_id: int, msg_id: int, text: str,
                             reply_to: int | None = None, edited: bool = False,
                             force_dry_run: bool | None = None, posted_at: datetime | None = None,
                             received_at: float | None = None) -> dict:
        t_lock = time.time()
        async with self._lock:
            t_in = time.time()
            async with async_session() as s:
                out = await self._handle(s, user_id, channel_id, msg_id, text, reply_to, edited, force_dry_run)
                await s.commit()
            t_done = time.time()
            if out.get("kind") in ("OPEN", "BREAKEVEN", "MOVE_SL", "MOVE_TP", "CLOSE", "CANCEL"):
                lat = latency_breakdown(posted_at, received_at, t_lock, t_in, t_done)
                out["latency"] = lat
                self.latency.append(lat)
                del self.latency[:-200]
                logger.info(f"[COPYTRADE] latency #{msg_id} {out.get('kind')}: {lat}")
            return out

    async def _handle(self, s, user_id, channel_id, msg_id, text, reply_to, edited, force_dry_run) -> dict:
        ch = await s.get(CopyTradeChannel, channel_id)
        if ch is None or ch.user_id != user_id:
            return {"status": "IGNORED", "detail": "unknown channel"}
        st = await self.get_settings(s, user_id)
        rules = merge_rules(st.rules, ch.rules)
        dry = st.dry_run if force_dry_run is None else force_dry_run
        parsed = parse_message(text)
        pd = parsed.to_dict()

        prior = (await s.execute(select(CopyTradeSignal).where(
            CopyTradeSignal.chat_id == ch.chat_id, CopyTradeSignal.msg_id == msg_id,
            CopyTradeSignal.edited.is_(False)))).scalars().first()
        if prior is not None and not edited:
            return {"status": "IGNORED", "detail": "already handled", "signal_id": prior.id}

        sig = CopyTradeSignal(user_id=user_id, channel_id=ch.id, chat_id=ch.chat_id, msg_id=msg_id,
                              reply_to=reply_to, edited=bool(edited), text=text or "", kind=parsed.kind,
                              parsed=pd, status="SEEN", actions=[])
        s.add(sig)
        await s.flush()

        try:
            if edited and prior is not None:
                await self._on_edit(s, sig, prior, pd, rules, dry)
            elif parsed.kind == "OPEN":
                await self._open(s, sig, ch, pd, rules, dry)
            elif parsed.kind in ("BREAKEVEN", "MOVE_SL", "MOVE_TP", "CLOSE", "CANCEL", "INFO"):
                await self._follow_up(s, sig, ch, pd, rules, dry)
            else:
                sig.status, sig.detail = "IGNORED", "not a signal"
        except Exception as e:  # noqa: BLE001 - a bad message must never stop the watcher
            logger.exception(f"[COPYTRADE] message {ch.chat_id}/{msg_id} failed: {e}")
            sig.status, sig.detail = "ERROR", f"{type(e).__name__}: {e}"
        sig.actions = list(sig.actions or [])
        logger.info(f"[COPYTRADE] {ch.title or ch.chat_id} #{msg_id} {parsed.kind} -> {sig.status}: {sig.detail}")
        await self._notify_signal(s, sig, ch, rules)
        return {"status": sig.status, "detail": sig.detail, "signal_id": sig.id, "kind": parsed.kind,
                "parsed": pd, "actions": sig.actions}

    # ── OPEN ────────────────────────────────────────────────────────────────
    async def plan_for(self, pd: dict, rules: dict) -> tuple[Any, str | None]:
        """(OpenPlan or None, reason). Reads prices and specs from MT5, places nothing."""
        avail = await mt5ops.available_symbols()
        if not avail:
            return None, "MT5 is not connected - cannot read prices"
        sym = resolve_symbol(pd.get("symbol"), avail, rules)
        if not sym:
            return None, f"no broker symbol for {pd.get('symbol')!r} - add it to the symbol map"
        spec = await mt5ops.symbol_spec(sym)
        q = await mt5ops.quote(sym)
        if not spec or not q:
            return None, f"no price/spec for {sym}"
        if spec.get("trade_mode") in (0, 3):
            return None, f"{sym} is closed to new trades at the broker"
        bal = await mt5ops.balance()
        plan = build_open_plan(pd, q, spec, bal, rules, sym)
        plan._spec = spec  # type: ignore[attr-defined]
        return plan, None if plan.ok else plan.reason

    async def _open(self, s, sig, ch, pd, rules, dry) -> None:
        if pd.get("confidence", 0) < float(rules["min_confidence"]):
            sig.status, sig.detail = "SKIPPED", f"low confidence {pd.get('confidence')}"
            return
        allowed = rules.get("allowed_symbols") or []
        if allowed and pd.get("symbol") not in allowed:
            sig.status, sig.detail = "SKIPPED", f"{pd.get('symbol')} is not in the allowed symbols"
            return
        active = (await s.execute(select(CopyTradeSignal).where(
            CopyTradeSignal.channel_id == ch.id, CopyTradeSignal.kind == "OPEN",
            CopyTradeSignal.status.in_(ACTIVE_SIGNAL)))).scalars().all()
        if len(active) >= int(rules["max_open_signals"]):
            sig.status, sig.detail = "SKIPPED", f"{len(active)} signals already open on this channel (max {rules['max_open_signals']})"
            return
        if not pd.get("symbol"):
            if pd.get("add_on") and active:
                # "ADD MORE BUY 4198 / NEW SL 4190": another entry on the running trade, and its new stop
                root = max(active, key=lambda x: x.created_at or _now())
                rp = root.parsed or {}
                pd = {**pd, "symbol": rp.get("symbol")}
                if not pd.get("tps"):
                    pd["tps"], pd["tp_open"] = rp.get("tps") or [], rp.get("tp_open")
                if pd.get("sl") is not None and rules.get("follow_move_sl", True):
                    acts0: list[str] = []
                    await self._apply("MOVE_SL", {"kind": "MOVE_SL", "new_price": pd["sl"]}, root,
                                      await self._live_orders(s, root.id), rules, acts0, dry)
                    root.actions = list(root.actions or []) + [f"add-on #{sig.msg_id}: " + a for a in acts0]
            elif rules.get("default_symbol"):
                pd = {**pd, "symbol": str(rules["default_symbol"])}
        if not pd.get("symbol"):
            sig.status, sig.detail = "SKIPPED", "no symbol in the message"
            return
        sig.parsed = pd
        if rules.get("close_on_opposite"):
            for prev in active:
                pp = prev.parsed or {}
                if pp.get("symbol") == pd.get("symbol") and pp.get("side") and pp.get("side") != pd.get("side"):
                    orders = await self._live_orders(s, prev.id)
                    acts0: list[str] = []
                    await self._apply("CLOSE", {"close_fraction": 1.0}, prev, orders, rules, acts0, dry)
                    prev.status = "CLOSED"
                    prev.detail = (prev.detail or "") + f" | closed by opposite signal #{sig.msg_id}"
        plan, why = await self.plan_for(pd, rules)
        if plan is None or not plan.ok:
            sig.status, sig.detail = "SKIPPED", why or "no plan"
            if plan is not None:
                sig.actions = [f"plan: {plan.to_dict()}"]
            return
        sig.symbol = plan.symbol
        spec = plan._spec  # type: ignore[attr-defined]
        acts = [f"{plan.side} {plan.order_type} {plan.symbol} @ {plan.entry:g} SL {plan.sl:g} "
                f"risk ${plan.risk_dollars:.2f}"] + [f"note: {n}" for n in plan.notes]
        placed, failed = 0, 0
        for leg in plan.legs:
            comment = f"AE_CT{ch.id % 1000}_{sig.msg_id % 100000}_TP{leg['leg']}"
            kind = leg.get("kind") or plan.order_type
            entry = leg.get("entry") if leg.get("entry") is not None else plan.entry
            row = CopyTradeOrder(user_id=sig.user_id, signal_id=sig.id, channel_id=ch.id, leg=leg["leg"],
                                 kind=kind, symbol=plan.symbol, side=plan.side, volume=leg["volume"],
                                 entry=entry, sl=plan.sl, tp=leg["tp"])
            if dry:
                row.status = "PAPER" if kind == "MARKET" else "PAPER_PENDING"
                acts.append(f"[dry run] leg {leg['leg']}: {kind} {leg['volume']} lots @ {entry:g} TP {leg['tp']}")
                placed += 1
            else:
                note = ""
                if kind != "MARKET":
                    # Price already sits on this leg's entry (10 Nov GBPJPY: a buy limit at
                    # 212.495 with the ask at 212.49x - "10015 Invalid price", the leg lost).
                    q = await mt5ops.quote(plan.symbol)
                    if q and pending_at_market(kind, plan.side, float(entry), q, spec) and \
                            self._tp_ahead(leg["tp"], plan.side, q):
                        kind, note = "MARKET", f" (its {row.kind.lower()} {entry:g} was at the market - taken at market)"
                        row.kind = kind
                ok, detail, ticket = await mt5ops.place(
                    plan.symbol, plan.side, kind, leg["volume"], entry, plan.sl, leg["tp"],
                    int(rules["magic"]), comment, spec, float(rules.get("pending_expiry_hours") or 0))
                if not ok and kind != "MARKET":
                    # refused anyway: if price is now at / through the entry, take it at market
                    q = await mt5ops.quote(plan.symbol)
                    if q and pending_at_market(kind, plan.side, float(entry), q, spec) and \
                            self._tp_ahead(leg["tp"], plan.side, q):
                        ok, detail, ticket = await mt5ops.place(
                            plan.symbol, plan.side, "MARKET", leg["volume"], entry, plan.sl, leg["tp"],
                            int(rules["magic"]), comment, spec)
                        if ok:
                            note = f" ({kind.lower()} {entry:g} refused, price was there - taken at market)"
                            kind = row.kind = "MARKET"
                if ok and kind == "MARKET":
                    pos = await mt5ops.get_position(ticket)
                    if pos is not None and note:
                        row.entry = float(pos.price_open)
                row.ticket, row.detail = ticket, (detail or "") + note
                row.status = ("OPEN" if kind == "MARKET" else "PENDING") if ok else "FAILED"
                acts.append(f"leg {leg['leg']}: {kind} {leg['volume']} lots @ {entry:g} TP {leg['tp']} -> "
                            f"{'#' + str(ticket) if ok else 'FAILED ' + detail}")
                placed, failed = placed + ok, failed + (not ok)
            s.add(row)
        sig.actions = acts
        if dry:
            sig.status, sig.detail = "DRY_RUN", f"{len(plan.legs)} legs planned (dry run)"
        elif placed and not failed:
            sig.status, sig.detail = "EXECUTED", f"{placed} legs placed"
        elif placed:
            sig.status, sig.detail = "PARTIAL", f"{placed} placed, {failed} failed"
        else:
            sig.status, sig.detail = "ERROR", "every leg was refused by MT5"

    # ── linking follow-ups ──────────────────────────────────────────────────
    async def _roots_for(self, s, sig, ch, pd) -> list[CopyTradeSignal]:
        if pd.get("applies_to") == "all":
            return list((await s.execute(select(CopyTradeSignal).where(
                CopyTradeSignal.channel_id == ch.id, CopyTradeSignal.kind == "OPEN",
                CopyTradeSignal.status.in_(ACTIVE_SIGNAL)))).scalars().all())
        if sig.reply_to:
            target = (await s.execute(select(CopyTradeSignal).where(
                CopyTradeSignal.chat_id == ch.chat_id, CopyTradeSignal.msg_id == sig.reply_to)
                .order_by(desc(CopyTradeSignal.created_at)))).scalars().first()
            if target is not None:
                if target.kind != "OPEN" and target.root_signal_id:
                    target = await s.get(CopyTradeSignal, target.root_signal_id)
                if target is not None and target.kind == "OPEN":
                    return [target] if target.status in ACTIVE_SIGNAL else []
        q = select(CopyTradeSignal).where(CopyTradeSignal.channel_id == ch.id, CopyTradeSignal.kind == "OPEN",
                                          CopyTradeSignal.status.in_(ACTIVE_SIGNAL))
        cands = list((await s.execute(q.order_by(desc(CopyTradeSignal.created_at)))).scalars().all())
        if pd.get("symbol"):
            cands = [c for c in cands if (c.parsed or {}).get("symbol") == pd["symbol"]] or []
        return cands[:1]

    async def _live_orders(self, s, root_id: int) -> list[CopyTradeOrder]:
        rows = list((await s.execute(select(CopyTradeOrder).where(
            CopyTradeOrder.signal_id == root_id, CopyTradeOrder.status.in_(LIVE_ORDER))
            .order_by(CopyTradeOrder.leg))).scalars().all())
        for r in rows:
            await self._refresh(r)
        return [r for r in rows if r.status in LIVE_ORDER]

    async def _refresh(self, r: CopyTradeOrder, live: tuple[set, set] | None = None) -> None:
        if r.status in ("PAPER", "PAPER_PENDING") or not r.ticket:
            return
        # `live` = (pending order tickets, position tickets) read once for the round;
        # only a ticket in neither needs the slower history lookup
        if live is not None and int(r.ticket) in live[0]:
            state = "PENDING"
        elif live is not None and int(r.ticket) in live[1]:
            state = "OPEN"
        else:
            state = await mt5ops.ticket_state(r.ticket)
        if state != "GONE":
            self._gone_seen.pop(r.ticket, None)
        if state != "MISSING":
            self._missing_since.pop(r.ticket, None)
        else:
            # 12 Nov: live moved from Deriv to Exness; its Deriv trades are on no
            # account it can see, stayed "open" here for ever and filled every
            # channel's max-open-signals, so live skipped the channels' new signals.
            first = self._missing_since.setdefault(r.ticket, _now())
            if _now() - first < MISSING_GRACE:
                return
            self._missing_since.pop(r.ticket, None)
            was = r.status
            r.status = "CLOSED" if was == "OPEN" else "CANCELLED"
            r.detail = (r.detail or "") + " | not on this MT5 account (login switched?) - no longer tracked"
            logger.warning(f"[COPYTRADE] ticket {r.ticket} is on no account this terminal can see - "
                           f"marked {r.status} (was {was})")
            return
        if state == "OPEN" and r.status == "PENDING":
            r.status = "OPEN"
        elif state == "GONE":
            # Two rounds in a row before it counts: a terminal that is reconnecting
            # can answer "not there" for a moment while the trade is still live.
            self._gone_seen[r.ticket] = self._gone_seen.get(r.ticket, 0) + 1
            if self._gone_seen[r.ticket] < 2:
                return
            self._gone_seen.pop(r.ticket, None)
            if r.status == "OPEN":
                r.status = "CLOSED"
            elif await mt5ops.was_filled(r.ticket):
                # filled and closed between two checks (a fast move straight to its TP or stop)
                r.status, r.detail = "CLOSED", (r.detail or "") + " | filled and closed between checks"
            else:
                r.status = "CANCELLED"

    # ── follow-ups ──────────────────────────────────────────────────────────
    async def _follow_up(self, s, sig, ch, pd, rules, dry) -> None:
        kind = pd["kind"]
        gate = {"BREAKEVEN": "follow_breakeven", "MOVE_SL": "follow_move_sl", "MOVE_TP": "follow_move_tp",
                "CANCEL": "follow_cancel"}.get(kind)
        if kind == "CLOSE":
            gate = "follow_close" if (pd.get("close_fraction") or 1.0) >= 0.999 else "follow_partial_close"
        if gate and not rules.get(gate, True):
            sig.status, sig.detail = "IGNORED", f"{gate} is off"
            return
        roots = await self._roots_for(s, sig, ch, pd)
        if not roots:
            sig.status, sig.detail = "IGNORED", "no open signal on this channel to apply it to"
            return
        sig.root_signal_id = roots[0].id
        acts: list[str] = []
        any_done = False
        for root in roots:
            orders = await self._live_orders(s, root.id)
            if not orders:
                acts.append(f"signal #{root.msg_id}: nothing open any more")
                if all(o.status not in LIVE_ORDER for o in await self._all_orders(s, root.id)):
                    root.status = "CLOSED"
                continue
            done = await self._apply(kind, pd, root, orders, rules, acts, dry)
            any_done = any_done or done
        sig.actions = acts
        if kind == "INFO" and not any_done:
            sig.status, sig.detail = "IGNORED", "information only"
        else:
            sig.status = "EXECUTED" if any_done else "IGNORED"
            sig.detail = "; ".join(acts[:4]) if acts else "no change"
            if dry and any_done:
                sig.status = "DRY_RUN"

    async def _all_orders(self, s, root_id: int) -> list[CopyTradeOrder]:
        return list((await s.execute(select(CopyTradeOrder).where(CopyTradeOrder.signal_id == root_id))).scalars().all())

    async def _apply(self, kind, pd, root, orders, rules, acts, dry) -> bool:
        side = (root.parsed or {}).get("side")
        s_ = 1 if side == "BUY" else -1
        positions = [o for o in orders if o.status in ("OPEN", "PAPER")]
        pendings = [o for o in orders if o.status in ("PENDING", "PAPER_PENDING")]
        changed = False

        async def set_sl(o: CopyTradeOrder, new_sl: float, why: str) -> None:
            nonlocal changed
            if o.sl is not None and (new_sl - o.sl) * s_ < 0 and not rules.get("allow_widen_sl", True):
                acts.append(f"leg {o.leg}: not widening SL {o.sl} -> {new_sl} (allow_widen_sl is off)")
                return
            if dry or o.status.startswith("PAPER"):
                ok, detail = True, "dry run"
            elif o.status == "OPEN":
                ok, detail = await mt5ops.modify_position(o.ticket, sl=new_sl)
            else:
                ok, detail = await mt5ops.modify_order(o.ticket, sl=new_sl)
            acts.append(f"leg {o.leg}: SL {o.sl} -> {new_sl:g} ({why}) {'ok' if ok else 'FAILED ' + detail}")
            if ok:
                o.sl, changed = new_sl, True

        async def set_tp(o: CopyTradeOrder, new_tp: float) -> None:
            nonlocal changed
            if dry or o.status.startswith("PAPER"):
                ok, detail = True, "dry run"
            elif o.status == "OPEN":
                ok, detail = await mt5ops.modify_position(o.ticket, tp=new_tp)
            else:
                ok, detail = await mt5ops.modify_order(o.ticket, tp=new_tp)
            acts.append(f"leg {o.leg}: TP {o.tp} -> {new_tp:g} {'ok' if ok else 'FAILED ' + detail}")
            if ok:
                o.tp, changed = new_tp, True

        async def close(o: CopyTradeOrder, vol: float | None) -> None:
            nonlocal changed
            whole = vol is None or vol >= o.volume - 1e-9
            if dry or o.status.startswith("PAPER"):
                ok, detail = True, "dry run"
            else:
                ok, detail = await mt5ops.close_volume(o.ticket, None if whole else vol)
            acts.append(f"leg {o.leg}: close {'all' if whole else vol} {'ok' if ok else 'FAILED ' + detail}")
            if ok:
                changed = True
                if whole:
                    o.status = "CLOSED"
                else:
                    o.volume = round(o.volume - vol, 8)

        async def cancel(o: CopyTradeOrder) -> None:
            nonlocal changed
            if dry or o.status.startswith("PAPER"):
                ok, detail = True, "dry run"
            else:
                ok, detail = await mt5ops.cancel_order(o.ticket)
            acts.append(f"leg {o.leg}: cancel pending {'ok' if ok else 'FAILED ' + detail}")
            if ok:
                o.status, changed = "CANCELLED", True

        if kind == "BREAKEVEN":
            if rules.get("cancel_pending_on_be", True):
                for o in pendings:
                    await cancel(o)
            if not positions:
                acts.append(f"signal #{root.msg_id}: no filled position to move to breakeven")
            q = await mt5ops.quote(root.symbol) if (root.symbol and not dry) else None
            spec_ = await mt5ops.symbol_spec(root.symbol) if q else None
            for o in positions:
                entry = o.entry
                if not dry and not o.status.startswith("PAPER"):
                    pos = await mt5ops.get_position(o.ticket)
                    entry = pos.price_open if pos is not None else entry
                tgt = breakeven_price(entry, side, rules)
                if q and o.status == "OPEN" and not stop_fits(tgt, side, q, spec_):
                    if stop_fits(float(entry), side, q, spec_):
                        tgt = float(entry)          # the offset is past the price: plain breakeven
                    else:
                        # price is back behind the entry: the broker would refuse it. Set it as soon as it fits.
                        self._pending_be[o.id] = tgt
                        acts.append(f"leg {o.leg}: breakeven {tgt:g} is past the price now - set as soon as it fits")
                        changed = True
                        continue
                await set_sl(o, tgt, "breakeven")
        elif kind == "MOVE_SL":
            new = pd.get("new_price")
            if new is None:
                acts.append("no price for the new stop")
                return False
            new = float(new)
            entries = [o.entry for o in orders if o.entry]
            ref = (sum(entries) / len(entries)) if entries else ((root.parsed or {}).get("entry_high") or (root.parsed or {}).get("entry") or new)
            if ref and abs(new - float(ref)) / float(ref) * 100 > float(rules.get("max_sl_move_pct") or 3.0):
                acts.append(f"ignored SL {new:g}: {abs(new - float(ref)) / float(ref) * 100:.1f}% from the entry - looks like a typo")
                return False
            q = await mt5ops.quote(root.symbol) if root.symbol else None
            if q and positions and ((side == "BUY" and new >= q["bid"]) or (side == "SELL" and new <= q["ask"])):
                acts.append(f"ignored SL {new:g}: on the wrong side of the price ({q['bid']}/{q['ask']})")
                return False
            for o in positions + pendings:
                await set_sl(o, new, "moved")
        elif kind == "MOVE_TP":
            idx = pd.get("tp_index")
            for o in positions + pendings:
                if pd.get("new_price") is not None and (idx is None or o.leg == idx):
                    await set_tp(o, float(pd["new_price"]))
        elif kind == "CLOSE":
            frac = float(pd.get("close_fraction") or 1.0)
            if frac >= 0.999:
                for o in positions:
                    await close(o, None)
                for o in pendings:
                    await cancel(o)
            else:
                plan = legs_to_close([{"ticket": o.id, "volume": o.volume, "leg": o.leg} for o in positions], frac)
                by_id = {o.id: o for o in positions}
                for oid, vol in plan:
                    await close(by_id[oid], vol)
                if pd.get("also_breakeven") and rules.get("follow_breakeven", True):
                    for o in positions:
                        if o.status in ("OPEN", "PAPER"):
                            await set_sl(o, breakeven_price(o.entry, side, rules), "breakeven")
                    if rules.get("cancel_pending_on_be", True):
                        for o in pendings:
                            await cancel(o)
        elif kind == "CANCEL":
            if not pendings:
                acts.append(f"signal #{root.msg_id}: no pending order to cancel (filled positions are left open)")
            for o in pendings:
                await cancel(o)
        elif kind == "INFO":
            idx = pd.get("tp_index")
            be_after = int(rules.get("be_after_tp") or 0)
            if idx and be_after and idx >= be_after:
                for o in positions:
                    if o.leg > idx:
                        await set_sl(o, breakeven_price(o.entry, side, rules), f"TP{idx} hit")
        if all(o.status not in LIVE_ORDER for o in orders):
            root.status = "CLOSED"
        return changed

    # ── edited messages ─────────────────────────────────────────────────────
    async def _on_edit(self, s, sig, prior, pd, rules, dry) -> None:
        """A message we already handled was edited.

        Providers edit signals to: correct a number (stop, targets, entry - often a
        typo such as 4777 for 3777); flip a wrong direction; fill in a stop/target
        posted without one; or write an instruction into the signal itself ("TP1
        hit - move SL to entry", "close now"). Each case is handled here; before,
        only stop/target number changes were, and an edited stop that WIDENED was
        refused by allow_widen_sl (that rule is for "move SL" messages, not for
        corrections of the signal itself)."""
        sig.root_signal_id = prior.id
        ch = await s.get(CopyTradeChannel, sig.channel_id)
        window = timedelta(minutes=float(rules.get("edit_window_minutes") or 60))
        young = (_now() - (prior.created_at or _now())) < window
        new_kind = pd.get("kind")
        old = prior.parsed or {}

        # ── the original was not a signal we acted on ──
        if prior.kind != "OPEN" or prior.status in ("SKIPPED", "ERROR", "IGNORED"):
            if new_kind == "OPEN" and prior.status in ("SKIPPED", "ERROR", "IGNORED") and young and \
                    (prior.kind != "OPEN" or _trade_fields(pd) != _trade_fields(old)):
                # now complete / corrected: take it as the signal
                prior.status, prior.kind, prior.parsed = "SUPERSEDED", "OPEN", pd
                sig.edited = False
                await self._open(s, sig, ch, pd, rules, dry)
                sig.detail = f"edited signal taken: {sig.detail}"
                return
            if prior.kind != "OPEN" and new_kind in FOLLOW_UP_KINDS and \
                    _trade_fields(pd) != _trade_fields(old):
                # an edited instruction ("SL to 4050" -> "SL to 4055")
                prior.parsed = pd
                await self._follow_up(s, sig, ch, pd, rules, dry)
                sig.detail = f"edited instruction: {sig.detail}"
                return
            sig.status, sig.detail = "IGNORED", (
                "edit of a skipped signal, too late to take it" if new_kind == "OPEN" and not young
                else "edit does not change a signal")
            return
        if prior.status not in ACTIVE_SIGNAL or not rules.get("edit_updates", True):
            sig.status, sig.detail = "IGNORED", "edit of a signal that is not open"
            return

        orders = await self._live_orders(s, prior.id)
        acts: list[str] = []

        # ── an instruction written into the signal ──
        # What was ADDED to the message is read on its own: "GOLD SELL ... SL 3739"
        # + "TP1 HIT ✅ SL to entry" no longer reads as a signal as a whole (results
        # words), and the added line is the instruction. Added lines count only when
        # the signal's own numbers are unchanged, or the message stopped reading as a
        # signal - a corrected "SL 4151" line would otherwise read as "move SL".
        follow = None
        if new_kind != "OPEN" or _trade_fields(pd) == _trade_fields(old):
            added = added_lines(await self._previous_text(s, sig), sig.text or "")
            if added:
                fp = parse_message("\n".join(added)).to_dict()
                if fp.get("kind") in FOLLOW_UP_KINDS:
                    follow = fp
        if follow is None and new_kind in FOLLOW_UP_KINDS:
            follow = pd
        if follow is not None and orders:
            gate = _follow_gate(follow)
            if gate and not rules.get(gate, True):
                acts.append(f"edit says {follow['kind'].lower()} - {gate} is off")
            else:
                acts.append(f"edit says {follow['kind'].lower().replace('_', ' ')}")
                await self._apply(follow["kind"], follow, prior, orders, rules, acts, dry)
                orders = [o for o in orders if o.status in LIVE_ORDER]
        if new_kind != "OPEN":
            self._finish_edit(sig, acts, dry)
            return

        # ── direction or instrument changed: the original was wrong ──
        if (pd.get("side"), pd.get("symbol")) != (old.get("side"), old.get("symbol")):
            what = f"{old.get('side')} {old.get('symbol')} -> {pd.get('side')} {pd.get('symbol')}"
            if not (young and rules.get("edit_replace", True)):
                acts.append(f"edit changed the trade ({what}) after {window.seconds // 60} min - not followed")
                self._finish_edit(sig, acts, dry)
                return
            acts.append(f"edit changed the trade ({what}): closing the old one, taking the corrected signal")
            await self._apply("CLOSE", {"close_fraction": 1.0}, prior, orders, rules, acts, dry)
            if all(o.status not in LIVE_ORDER for o in await self._all_orders(s, prior.id)):
                prior.status = "SUPERSEDED"
                prior.parsed = pd
                sig.edited = False
                await self._open(s, sig, ch, pd, rules, dry)
                sig.actions = acts + list(sig.actions or [])
                sig.detail = f"replaced ({what}): {sig.detail}"
            else:
                acts.append("some legs could not be closed - corrected signal NOT taken, old one still managed")
                self._finish_edit(sig, acts, dry)
            return

        side = pd.get("side")
        sgn = 1 if side == "BUY" else -1
        q = await mt5ops.quote(prior.symbol) if (prior.symbol and not dry) else None
        eff_new = tighten_stop(pd, q or {}, rules)[0]            # our stop for the edited numbers
        eff_old = tighten_stop(old, q or {}, rules)[0]
        sl_changed = pd.get("sl") is not None and pd.get("sl") != old.get("sl")
        new_sl = float(eff_new["sl"]) if sl_changed and eff_new.get("sl") is not None else None

        # ── entry changed: move the orders still waiting ──
        moved: set[int] = set()
        if _entry_ref(pd) is not None and _entry_ref(old) is not None and _entry_ref(pd) != _entry_ref(old):
            pend = [o for o in orders if o.status in ("PENDING", "PAPER_PENDING")]
            if not pend:
                acts.append(f"entry edited {_entry_ref(old):g} -> {_entry_ref(pd):g}: every leg already filled, nothing to move")
            for o in pend:
                px = remap_entry(o.entry, old, pd)
                if px is None:
                    continue
                if dry or o.status.startswith("PAPER"):
                    ok, detail = True, "dry run"
                else:
                    ok, detail = await mt5ops.modify_order(o.ticket, price=px, sl=new_sl)
                acts.append(f"leg {o.leg}: entry {o.entry:g} -> {px:g}"
                            + (f", SL {o.sl} -> {new_sl:g}" if new_sl is not None else "")
                            + f" {'ok' if ok else 'FAILED ' + detail}")
                if ok:
                    o.entry = px
                    if new_sl is not None:
                        o.sl = new_sl
                    moved.add(o.id)
            if moved:
                acts.append("lots unchanged - the $ risk moves with the new entry")

        # ── stop changed (a correction: widening allowed; our stop fraction applied) ──
        if new_sl is not None:
            rest = [o for o in orders if o.id not in moved and o.status in LIVE_ORDER]
            if rest:
                note = f"edited SL {pd['sl']:g}" + (f" (ours {new_sl:g})" if new_sl != pd["sl"] else "")
                acts.append(note)
                await self._apply("MOVE_SL", {"new_price": new_sl}, prior, rest,
                                  dict(rules, allow_widen_sl=True), acts, dry)
            if eff_old.get("sl") is not None and (new_sl - float(eff_old["sl"])) * sgn < 0:
                acts.append("stop is further away than before: the $ risk of these lots went up")

        # ── targets changed ──
        new_tps = pd.get("tps") or []
        if new_tps and new_tps != (old.get("tps") or []):
            for o in orders:
                if o.status in LIVE_ORDER and o.leg <= len(new_tps) and o.tp is not None \
                        and o.tp != new_tps[o.leg - 1]:
                    await self._apply("MOVE_TP", {"new_price": new_tps[o.leg - 1], "tp_index": o.leg},
                                      prior, [o], rules, acts, dry)
        prior.parsed = pd
        self._finish_edit(sig, acts, dry)

    @staticmethod
    def _finish_edit(sig, acts: list[str], dry: bool) -> None:
        sig.actions = acts
        sig.status = ("DRY_RUN" if dry else "EXECUTED") if acts else "IGNORED"
        sig.detail = "; ".join(acts[:4]) if acts else "edit changed nothing that is traded"

    async def _previous_text(self, s, sig) -> str:
        """The message as it read before this edit (the latest earlier version)."""
        row = (await s.execute(select(CopyTradeSignal.text).where(
            CopyTradeSignal.chat_id == sig.chat_id, CopyTradeSignal.msg_id == sig.msg_id,
            CopyTradeSignal.id != sig.id).order_by(desc(CopyTradeSignal.id)).limit(1))).scalars().first()
        return row or ""

    # ── Telegram alerts ─────────────────────────────────────────────────────
    @staticmethod
    async def _digits(symbol: str | None) -> int:
        if not symbol:
            return 2
        try:
            spec = await mt5ops.symbol_spec(symbol)
            return int((spec or {}).get("digits") or 2)
        except Exception:  # noqa: BLE001
            return 2

    async def _notify_signal(self, s, sig, ch, rules) -> None:
        if rules.get("notify") is False:
            return
        try:
            orders = await self._all_orders(s, sig.id) if sig.kind == "OPEN" else []
            sym = orders[0].symbol if orders else (sig.parsed or {}).get("symbol")
            notify.alerts.post(notify.signal_message(ch.title or str(ch.chat_id), sig, orders,
                                                     await self._digits(sym)))
        except Exception as e:  # noqa: BLE001 - alerts never break trading
            logger.warning(f"[COPYTRADE] signal alert failed: {e}")

    async def _notify_round(self, s, root, ch, rules, snap, was_active) -> None:
        if rules.get("notify") is False or root.status == "DRY_RUN":
            return
        try:
            rows = await self._all_orders(s, root.id)       # includes legs added at market this round
            changed = [r for r in rows if snap.get(r.id) != (r.status, r.sl, r.tp)]
            if not changed and not (root.status == "CLOSED" and was_active != "CLOSED"):
                return
            closed: dict[int, dict] = {}
            for r in changed:
                prev = snap.get(r.id)
                if prev and prev[0] in ("OPEN", "PENDING") and r.status == "CLOSED" and r.ticket:
                    closed[r.id] = await mt5ops.closed_deal(r.ticket) or {}
            digits = await self._digits(rows[0].symbol if rows else None)
            lines = notify.order_events(snap, rows, closed, digits)
            total = None
            if root.status == "CLOSED" and was_active != "CLOSED":
                nets = []
                for r in rows:
                    if r.ticket and r.status == "CLOSED":
                        c = closed.get(r.id) or await mt5ops.closed_deal(r.ticket) or {}
                        if c.get("net") is not None:
                            nets.append(c["net"])
                total = round(sum(nets), 2) if nets else None
            notify.alerts.post(notify.round_message(ch.title if ch else str(root.chat_id), root, lines, total))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[COPYTRADE] order alert failed: {e}")

    # ── housekeeping ────────────────────────────────────────────────────────
    async def monitor_once(self) -> None:
        """One housekeeping round over every active signal.

        Speed: every order's state comes from ONE orders_get() + ONE
        positions_get() for the whole round (was 2-5 MT5 calls per order), and
        the lock is taken per signal, not for the whole round - a Telegram
        signal arriving mid-round waits for one signal's housekeeping, not all."""
        async with async_session() as s:
            root_ids = list((await s.execute(select(CopyTradeSignal.id).where(
                CopyTradeSignal.kind == "OPEN", CopyTradeSignal.status.in_(ACTIVE_SIGNAL)))).scalars().all())
        if not root_ids:
            return
        live = await mt5ops.live_tickets()
        for rid in root_ids:
            async with self._lock:
                async with async_session() as s:
                    root = await s.get(CopyTradeSignal, rid)
                    if root is None or root.status not in ACTIVE_SIGNAL:
                        continue
                    await self._monitor_root(s, root, live)
                    await s.commit()

    async def _monitor_root(self, s, root, live) -> None:
        st = await self.get_settings(s, root.user_id)
        ch = await s.get(CopyTradeChannel, root.channel_id)
        rules = merge_rules(st.rules, ch.rules if ch else None)
        rows = await self._all_orders(s, root.id)
        if root.status == "DRY_RUN":
            await self._simulate_paper(root, rows, rules, s)
            return
        before = {r.id: r.status for r in rows}
        snap = notify.snapshot(rows)
        for r in rows:
            if r.status in ("OPEN", "PENDING"):
                await self._refresh(r, live)
        # pending orders past their expiry
        exp_h = float(rules.get("pending_expiry_hours") or 0)
        if exp_h > 0 and root.created_at and _now() - root.created_at > timedelta(hours=exp_h):
            for r in rows:
                if r.status == "PENDING" and r.ticket:
                    ok, _ = await mt5ops.cancel_order(r.ticket)
                    if ok:
                        r.status, r.detail = "CANCELLED", "expired"
        # unfilled zone orders after TPn filled
        cancel_after = int(rules.get("cancel_pending_after_tp") or 0)
        if cancel_after:
            for r in [r for r in rows if before[r.id] in ("OPEN", "PENDING") and r.status == "CLOSED"
                      and r.leg >= cancel_after]:
                if r.ticket and await self._closed_tp(r.ticket):
                    for o in rows:
                        if o.status == "PENDING" and o.ticket:
                            ok, _ = await mt5ops.cancel_order(o.ticket)
                            if ok:
                                o.status, o.detail = "CANCELLED", f"TP{r.leg} filled"
                    break
        # first fill: the rest of the zone at market
        # Once per signal, as soon as ANY leg is in - including leg 1 filled
        # at market the moment the signal was copied (price already in the
        # zone). The old test needed a PENDING leg to turn OPEN, so a signal
        # whose first leg went in at market never got its market adds and
        # the limits were left waiting behind price.
        if str(rules.get("on_first_fill") or "keep") in ("market", "market_add") and \
                root.id not in self._ff_done and \
                any(r.status == "PENDING" and r.ticket for r in rows) and \
                any(r.status in ("OPEN", "CLOSED") for r in rows):
            self._ff_done.add(root.id)
            if not any("first leg filled" in (r.detail or "") for r in rows):   # not done before a restart
                await self._rest_at_market(s, root, rows, rules)
        # breakeven he asked for while price was behind the entry
        if self._pending_be:
            await self._apply_pending_be(root, rows)
        # breakeven after TPn / TP ladder trail - every round until the stop sticks
        if int(rules.get("be_after_tp") or 0) or rules.get("trail_ladder"):
            await self._trail(root, rows, rules, s)
        was_active = root.status
        if all(r.status not in LIVE_ORDER for r in rows):
            root.status = "CLOSED"
        await self._notify_round(s, root, ch, rules, snap, was_active)

    @staticmethod
    def _tp_ahead(tp, side: str, q: dict) -> bool:
        """The leg's target is still in front of the price (or it has none)."""
        if tp is None:
            return True
        px = float(q["ask"] if side == "BUY" else q["bid"])
        return (float(tp) - px) * (1 if side == "BUY" else -1) > 0

    async def _closed_tp(self, ticket: int) -> bool:
        if ticket not in self._tp_closed:
            res = await mt5ops.closed_by_tp(ticket)
            if res is None:
                return False                  # terminal didn't answer - ask again next round
            self._tp_closed[ticket] = bool(res)
        return self._tp_closed[ticket]

    async def _rest_at_market(self, s, root: CopyTradeSignal, rows: list[CopyTradeOrder], rules: dict) -> None:
        """The first zone leg filled. "market": cancel the legs still waiting deeper
        in the zone and take their volume at market now. "market_add": take the
        same volume at market now AND leave the limits waiting (they still go once
        TP1 fills, with cancel_pending_after_tp) - a tap of the zone that runs
        straight to TP3 carries every leg. Not done once price is already at TP1."""
        mode = str(rules.get("on_first_fill") or "keep")
        spec = await mt5ops.symbol_spec(root.symbol) if root.symbol else None
        q = await mt5ops.quote(root.symbol) if root.symbol else None
        if not spec or not q:
            return
        side = (root.parsed or {}).get("side")
        sgn = 1 if side == "BUY" else -1
        px = float(q["ask"] if side == "BUY" else q["bid"])
        tp1 = min((float(r.tp) for r in rows if r.tp), key=lambda t: (t - px) * sgn, default=None)
        if tp1 is not None and (tp1 - px) * sgn <= 0:
            logger.info(f"[COPYTRADE] signal #{root.msg_id}: first fill, but price is already at TP1 - no market legs")
            return
        for r in list(rows):
            if r.status != "PENDING" or not r.ticket:
                continue
            if r.tp and (float(r.tp) - px) * sgn <= 0:
                continue                                   # its target is already behind the price
            if mode == "market":
                ok, _ = await mt5ops.cancel_order(r.ticket)
                if not ok:
                    continue
            ok, detail, ticket = await mt5ops.place(root.symbol, r.side, "MARKET", r.volume, 0.0, r.sl, r.tp,
                                                    int(rules["magic"]), f"AE_CT_{root.msg_id % 100000}_TP{r.leg}m",
                                                    spec)
            pos = await mt5ops.get_position(ticket) if ok else None
            entry = float(pos.price_open) if pos is not None else px
            if mode == "market":
                if ok:
                    r.ticket, r.kind, r.status, r.entry = ticket, "MARKET", "OPEN", entry
                    r.detail = f"first leg filled - taken at market {entry:g}"
                else:
                    r.status, r.detail = "CANCELLED", f"first leg filled; market entry failed: {detail}"
                logger.info(f"[COPYTRADE] signal #{root.msg_id} leg {r.leg}: {r.detail}")
            else:
                if not ok:
                    logger.warning(f"[COPYTRADE] signal #{root.msg_id} leg {r.leg}: market add failed: {detail}")
                    continue
                add = CopyTradeOrder(user_id=r.user_id, signal_id=r.signal_id, channel_id=r.channel_id, leg=r.leg,
                                     ticket=ticket, kind="MARKET", symbol=r.symbol, side=r.side, volume=r.volume,
                                     entry=entry, sl=r.sl, tp=r.tp, status="OPEN",
                                     detail=f"first leg filled - added at market {entry:g}, limit {r.entry:g} still waits")
                s.add(add)
                logger.info(f"[COPYTRADE] signal #{root.msg_id} leg {r.leg}: {add.detail}")

    async def _apply_pending_be(self, root: CopyTradeSignal, rows: list[CopyTradeOrder]) -> None:
        side = (root.parsed or {}).get("side")
        mine = [r for r in rows if r.id in self._pending_be]
        if not mine or side not in ("BUY", "SELL") or not root.symbol:
            return
        q = await mt5ops.quote(root.symbol)
        spec = await mt5ops.symbol_spec(root.symbol)
        s = 1 if side == "BUY" else -1
        for r in mine:
            tgt = self._pending_be[r.id]
            if r.status != "OPEN":
                self._pending_be.pop(r.id, None)
                continue
            if not q or not stop_fits(tgt, side, q, spec):
                continue
            if r.sl is not None and (tgt - r.sl) * s <= 0:
                self._pending_be.pop(r.id, None)
                continue
            ok, msg = await mt5ops.modify_position(r.ticket, sl=tgt)
            if ok:
                r.sl = tgt
                self._pending_be.pop(r.id, None)
                logger.info(f"[COPYTRADE] signal #{root.msg_id} leg {r.leg}: breakeven {tgt:g} set (it fits now)")

    async def _stack(self, root, rows, open_rows, rules, q, spec, mult, sess=None) -> None:
        """TP1 reached: add `mult` x the open volume at market, its stop at the
        first entry (breakeven of the trade) - it risks only the TP1 distance -
        and no target: the ladder trail closes it with the rest."""
        side = (root.parsed or {}).get("side")
        first = min(open_rows, key=lambda r: r.leg)
        pos = await mt5ops.get_position(first.ticket)
        be = float(pos.price_open) if pos is not None else float(first.entry or 0)
        if not be or not stop_fits(be, side, q, spec):
            return
        step = float(spec.get("volume_step") or 0.01)
        live_vol = 0.0
        for r in open_rows:                  # only what is really still open (a leg may have just hit TP1)
            p = await mt5ops.get_position(r.ticket)
            if p is not None:
                live_vol += float(p.volume)
        vol = round(int(live_vol * mult / step + 1e-9) * step, 8)
        if vol < float(spec.get("volume_min") or step):
            return
        ok, detail, ticket = await mt5ops.place(root.symbol, side, "MARKET", vol, 0.0, be, None, int(rules["magic"]),
                                                f"AE_CT_{root.msg_id % 100000}_STK", spec)
        if not ok:
            logger.warning(f"[COPYTRADE] signal #{root.msg_id}: stack at TP1 failed: {detail}")
            return
        p2 = await mt5ops.get_position(ticket)
        add = CopyTradeOrder(user_id=first.user_id, signal_id=first.signal_id, channel_id=first.channel_id,
                             leg=max(r.leg for r in rows) + 1, ticket=ticket, kind="MARKET", symbol=root.symbol,
                             side=side, volume=vol, entry=float(p2.price_open) if p2 is not None else None, sl=be,
                             tp=None, status="OPEN", detail=f"stack at TP1: {vol} lots, stop {be:g}")
        if sess is not None:
            sess.add(add)
        rows.append(add)
        logger.info(f"[COPYTRADE] signal #{root.msg_id}: {add.detail}")

    async def _trail(self, root: CopyTradeSignal, rows: list[CopyTradeOrder], rules: dict, sess=None) -> None:
        """Breakeven after TPn, or with the ladder TP1 -> breakeven, TP2 -> TP1 ...
        Recomputed every round from what has happened (legs closed at their TP,
        the furthest target price reached), so a stop the broker refused - too
        close to the price - is simply tried again on the next round."""
        side = (root.parsed or {}).get("side")
        if side not in ("BUY", "SELL") or not root.symbol:
            return
        s = 1 if side == "BUY" else -1
        open_rows = [r for r in rows if r.status == "OPEN" and r.ticket]
        if not open_rows:
            return
        ladder = bool(rules.get("trail_ladder"))
        tps = [float(t) for t in ((root.parsed or {}).get("tps") or [])]
        level = 0
        for r in rows:
            if r.status == "CLOSED" and r.ticket and await self._closed_tp(r.ticket):
                level = max(level, int(r.leg))
        q = await mt5ops.quote(root.symbol)
        if q and ladder:
            px = float(q["bid"] if s == 1 else q["ask"])
            k = self._reached.get(root.id, 0)
            while k < len(tps) and (px - tps[k]) * s >= 0:
                k += 1
            self._reached[root.id] = k
            level = max(level, k)
        if level < 1 or not q:
            return
        spec = await mt5ops.symbol_spec(root.symbol)
        mult = float(rules.get("stack_at_tp1") or 0)
        if mult > 0 and spec and not any("stack at TP1" in (r.detail or "") for r in rows):
            await self._stack(root, rows, open_rows, rules, q, spec, mult, sess)
        for r in open_rows:
            pos = await mt5ops.get_position(r.ticket)
            if pos is None:
                continue
            entry = float(pos.price_open)
            cur = float(pos.sl or 0) or None
            tgt = trail_target(level, entry, side, tps, rules, ladder)
            if tgt is None or (cur is not None and (tgt - cur) * s <= 1e-9):
                continue
            if not stop_fits(tgt, side, q, spec):
                # the offset (or the target) is past the price right now: plain breakeven if that fits, else wait
                alt = entry
                if stop_fits(alt, side, q, spec) and (cur is None or (alt - cur) * s > 1e-9):
                    tgt = alt
                else:
                    if self._trail_note.get(r.id) != tgt:
                        self._trail_note[r.id] = tgt
                        logger.info(f"[COPYTRADE] signal #{root.msg_id} leg {r.leg}: stop {tgt:g} is past the price "
                                    f"now - retrying")
                    continue
            ok, msg = await mt5ops.modify_position(r.ticket, sl=tgt)
            if ok:
                r.sl = tgt
                self._trail_note.pop(r.id, None)
                logger.info(f"[COPYTRADE] signal #{root.msg_id} leg {r.leg}: TP{level} reached - stop to {tgt:g}")
            elif self._trail_note.get(r.id) != tgt:
                self._trail_note[r.id] = tgt
                logger.warning(f"[COPYTRADE] signal #{root.msg_id} leg {r.leg}: stop to {tgt:g} refused: {msg}")

    async def _simulate_paper(self, root: CopyTradeSignal, rows: list[CopyTradeOrder], rules: dict, s=None) -> None:
        """Dry run: fill paper pending orders and close paper positions at their
        SL/TP against the live price, so a dry-run channel shows how its signals
        would have played out (and frees the open-signal slots)."""
        live = [r for r in rows if r.status in ("PAPER", "PAPER_PENDING")]
        if not live or not root.symbol:
            if not live:
                root.status = "CLOSED"
            return
        q = await mt5ops.quote(root.symbol)
        if not q:
            return
        side = (root.parsed or {}).get("side")
        buy = side == "BUY"
        px_close = q["bid"] if buy else q["ask"]       # what a position would close at
        px_open = q["ask"] if buy else q["bid"]        # what an order would fill at
        exp_h = float(rules.get("pending_expiry_hours") or 0)
        for r in live:
            if r.status == "PAPER_PENDING":
                fill = (px_open <= r.entry) if (r.kind == "LIMIT") == buy else (px_open >= r.entry)
                if fill:
                    r.status, r.detail = "PAPER", f"paper fill at {r.entry:g}"
                elif exp_h > 0 and root.created_at and _now() - root.created_at > timedelta(hours=exp_h):
                    r.status, r.detail = "CANCELLED", "paper order expired"
                continue
            hit_sl = r.sl and ((px_close <= r.sl) if buy else (px_close >= r.sl))
            hit_tp = r.tp and ((px_close >= r.tp) if buy else (px_close <= r.tp))
            if hit_sl or hit_tp:
                exit_px = r.sl if hit_sl else r.tp
                move = (exit_px - r.entry) * (1 if buy else -1)
                r.status = "CLOSED"
                r.detail = f"paper {'SL' if hit_sl else 'TP'} at {exit_px:g} ({move:+g} per unit)"
                cancel_after = int(rules.get("cancel_pending_after_tp") or 0)
                if hit_tp and cancel_after and r.leg >= cancel_after:
                    for o in live:
                        if o.status == "PAPER_PENDING":
                            o.status, o.detail = "CANCELLED", f"TP{r.leg} filled"
        # paper first fill: the rest of the zone at market
        mode_ff = str(rules.get("on_first_fill") or "keep")
        if mode_ff in ("market", "market_add") and any(r.status == "PAPER" for r in live) \
                and not any("first leg filled" in (r.detail or "") for r in rows) \
                and not any(r.status == "CLOSED" for r in rows):
            for r in live:
                if r.status != "PAPER_PENDING" or (r.tp and (float(r.tp) - px_open) * (1 if buy else -1) <= 0):
                    continue
                if mode_ff == "market":
                    r.status, r.entry, r.detail = "PAPER", px_open, f"first leg filled - paper market at {px_open:g}"
                elif s is not None:
                    s.add(CopyTradeOrder(user_id=r.user_id, signal_id=r.signal_id, channel_id=r.channel_id, leg=r.leg,
                                         kind="MARKET", symbol=r.symbol, side=r.side, volume=r.volume, entry=px_open,
                                         sl=r.sl, tp=r.tp, status="PAPER",
                                         detail=f"first leg filled - paper add at market {px_open:g}"))
        # paper breakeven after TPn / ladder trail
        if int(rules.get("be_after_tp") or 0) or rules.get("trail_ladder"):
            s_ = 1 if buy else -1
            ladder = bool(rules.get("trail_ladder"))
            tps = [float(t) for t in ((root.parsed or {}).get("tps") or [])]
            level = max([int(r.leg) for r in rows if r.status == "CLOSED" and "paper TP" in (r.detail or "")] or [0])
            if ladder:
                k = self._reached.get(root.id, 0)
                while k < len(tps) and (px_close - tps[k]) * s_ >= 0:
                    k += 1
                self._reached[root.id] = k
                level = max(level, k)
            for o in live:
                if o.status == "PAPER" and level >= 1:
                    tgt = trail_target(level, o.entry, side, tps, rules, ladder)
                    if tgt is not None and (o.sl is None or (tgt - o.sl) * s_ > 0) and (px_close - tgt) * s_ > 0:
                        o.sl = tgt
        if all(r.status not in LIVE_ORDER for r in rows):
            root.status = "CLOSED"


copytrade_service = CopyTradeService()
