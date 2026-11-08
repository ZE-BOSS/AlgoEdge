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
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import desc, select

from backend.copytrade import mt5ops
from backend.copytrade.models import CopyTradeChannel, CopyTradeOrder, CopyTradeSettings, CopyTradeSignal
from backend.copytrade.parser import parse_message
from backend.copytrade.rules import breakeven_price, build_open_plan, legs_to_close, merge_rules, resolve_symbol
from backend.data.database import async_session
from backend.utils.logger import get_logger

logger = get_logger(__name__)

ACTIVE_SIGNAL = ("EXECUTED", "PARTIAL", "DRY_RUN")
LIVE_ORDER = ("OPEN", "PENDING", "PAPER", "PAPER_PENDING")


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class CopyTradeService:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()        # one message at a time: follow-ups must see the orders of the signal before them

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
                             force_dry_run: bool | None = None) -> dict:
        async with self._lock:
            async with async_session() as s:
                out = await self._handle(s, user_id, channel_id, msg_id, text, reply_to, edited, force_dry_run)
                await s.commit()
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
            sig.status, sig.detail = "SKIPPED", "no symbol in the message"
            return
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
                ok, detail, ticket = await mt5ops.place(
                    plan.symbol, plan.side, kind, leg["volume"], entry, plan.sl, leg["tp"],
                    int(rules["magic"]), comment, spec, float(rules.get("pending_expiry_hours") or 0))
                row.ticket, row.detail = ticket, detail
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

    async def _refresh(self, r: CopyTradeOrder) -> None:
        if r.status in ("PAPER", "PAPER_PENDING") or not r.ticket:
            return
        state = await mt5ops.ticket_state(r.ticket)
        if state == "OPEN" and r.status == "PENDING":
            r.status = "OPEN"
        elif state == "GONE":
            r.status = "CLOSED" if r.status == "OPEN" else "CANCELLED"

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
            for o in positions:
                entry = o.entry
                if not dry and not o.status.startswith("PAPER"):
                    pos = await mt5ops.get_position(o.ticket)
                    entry = pos.price_open if pos is not None else entry
                await set_sl(o, breakeven_price(entry, side, rules), "breakeven")
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
        sig.root_signal_id = prior.id
        if prior.kind != "OPEN" or pd.get("kind") != "OPEN":
            if prior.status in ("SKIPPED", "IGNORED") and pd.get("kind") == "OPEN" and \
                    (_now() - (prior.created_at or _now())) < timedelta(minutes=15):
                prior.status, prior.kind, prior.parsed = "SUPERSEDED", "OPEN", pd
                sig.edited = False   # treat as the signal itself
                ch = await s.get(CopyTradeChannel, sig.channel_id)
                await self._open(s, sig, ch, pd, rules, dry)
                return
            sig.status, sig.detail = "IGNORED", "edit does not change a signal"
            return
        if prior.status in ("SKIPPED",) and (_now() - (prior.created_at or _now())) < timedelta(minutes=15):
            prior.status = "SUPERSEDED"
            sig.edited = False
            ch = await s.get(CopyTradeChannel, sig.channel_id)
            await self._open(s, sig, ch, pd, rules, dry)
            return
        if prior.status not in ACTIVE_SIGNAL or not rules.get("edit_updates", True):
            sig.status, sig.detail = "IGNORED", "edit of a signal that is not open"
            return
        orders = await self._live_orders(s, prior.id)
        acts: list[str] = []
        old = prior.parsed or {}
        if pd.get("sl") is not None and pd["sl"] != old.get("sl"):
            await self._apply("MOVE_SL", {"new_price": pd["sl"]}, prior, orders, rules, acts, dry)
        new_tps = pd.get("tps") or []
        if new_tps and new_tps != (old.get("tps") or []):
            for o in orders:
                if o.leg <= len(new_tps) and o.tp != new_tps[o.leg - 1]:
                    await self._apply("MOVE_TP", {"new_price": new_tps[o.leg - 1], "tp_index": o.leg},
                                      prior, [o], rules, acts, dry)
        prior.parsed = pd
        sig.actions = acts
        sig.status = ("DRY_RUN" if dry else "EXECUTED") if acts else "IGNORED"
        sig.detail = "; ".join(acts[:4]) if acts else "edit changed nothing that is traded"

    # ── housekeeping ────────────────────────────────────────────────────────
    async def monitor_once(self) -> None:
        async with self._lock:
            async with async_session() as s:
                roots = list((await s.execute(select(CopyTradeSignal).where(
                    CopyTradeSignal.kind == "OPEN", CopyTradeSignal.status.in_(ACTIVE_SIGNAL)))).scalars().all())
                for root in roots:
                    st = await self.get_settings(s, root.user_id)
                    ch = await s.get(CopyTradeChannel, root.channel_id)
                    rules = merge_rules(st.rules, ch.rules if ch else None)
                    rows = await self._all_orders(s, root.id)
                    if root.status == "DRY_RUN":
                        await self._simulate_paper(root, rows, rules)
                        continue
                    before = {r.id: r.status for r in rows}
                    for r in rows:
                        if r.status in ("OPEN", "PENDING"):
                            await self._refresh(r)
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
                        for r in [r for r in rows if before[r.id] == "OPEN" and r.status == "CLOSED" and r.leg >= cancel_after]:
                            if r.ticket and await mt5ops.closed_by_tp(r.ticket):
                                for o in rows:
                                    if o.status == "PENDING" and o.ticket:
                                        ok, _ = await mt5ops.cancel_order(o.ticket)
                                        if ok:
                                            o.status, o.detail = "CANCELLED", f"TP{r.leg} filled"
                                break
                    # breakeven after TPn
                    be_after = int(rules.get("be_after_tp") or 0)
                    if be_after:
                        newly_closed = [r for r in rows if before[r.id] == "OPEN" and r.status == "CLOSED"]
                        for r in newly_closed:
                            if r.leg >= be_after and r.ticket and await mt5ops.closed_by_tp(r.ticket):
                                side = (root.parsed or {}).get("side")
                                for o in rows:
                                    if o.status == "OPEN" and o.leg > r.leg:
                                        pos = await mt5ops.get_position(o.ticket)
                                        if pos is not None:
                                            be = breakeven_price(pos.price_open, side, rules)
                                            ok, _ = await mt5ops.modify_position(o.ticket, sl=be)
                                            if ok:
                                                o.sl = be
                                logger.info(f"[COPYTRADE] TP{r.leg} filled on signal #{root.msg_id} - rest at breakeven")
                    if all(r.status not in LIVE_ORDER for r in rows):
                        root.status = "CLOSED"
                await s.commit()

    async def _simulate_paper(self, root: CopyTradeSignal, rows: list[CopyTradeOrder], rules: dict) -> None:
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
                be_after = int(rules.get("be_after_tp") or 0)
                if hit_tp and be_after and r.leg >= be_after:
                    for o in live:
                        if o.status == "PAPER" and o.leg > r.leg:
                            o.sl = breakeven_price(o.entry, side, rules)
        if all(r.status not in LIVE_ORDER for r in rows):
            root.status = "CLOSED"


copytrade_service = CopyTradeService()
