"""
backend/groups/router.py

Turns trading events into group posts.

Sources call four entry points (all never raise into the trading path):

    on_open(source_type, source_key, key, symbol, side, entry, sl, tps, ...)
    on_headsup(source_type, source_key, key, symbol, side, level, cross, stop)
    on_withdrawn(key)
    on_event(key, {"type": "tp" | "be" | "sl_moved" | "closed" | "cancelled" | "in_zone", ...})

`source_type` is "strategy" (key = the live trade's group id) or "channel"
(key = the copier's root signal id). Matching routes decide which groups get
the post; sampling and delay make General a teaser of VIP. Posts are written
to group_posts as QUEUED; the bot's outbox sends them in order, threads every
update under the signal's first post and keeps a status line on it.

The source never appears in a post (templates.py).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import desc, select

from backend.groups import templates as T
from backend.groups.models import GroupPost, GroupSignal, SignalGroup, SignalRoute
from backend.utils.logger import get_logger

logger = get_logger(__name__)

DEDUP_WINDOW = timedelta(minutes=10)
DEDUP_PCT = 0.15


def _now() -> datetime:
    return datetime.utcnow()


def _session_factory():
    from backend.data.database import async_session
    return async_session


class GroupRouter:
    def __init__(self, session_factory=None):
        self._sf = session_factory
        self.admin_notify = None          # async callable(text) - the bot sets it

    @property
    def sf(self):
        return self._sf or _session_factory()

    # -- matching ------------------------------------------------------------
    async def _routes(self, s, source_type: str, source_key: str, symbol: str) -> list[tuple[SignalRoute, SignalGroup]]:
        q = select(SignalRoute, SignalGroup).join(SignalGroup, SignalGroup.id == SignalRoute.group_id).where(
            SignalRoute.source_type == source_type, SignalRoute.source_key == str(source_key),
            SignalRoute.enabled.is_(True), SignalGroup.posting.is_(True))
        out = []
        for route, group in (await s.execute(q)).all():
            if route.paused_reason:
                continue
            if route.symbol and not _same_symbol(route.symbol, symbol):
                continue
            out.append((route, group))
        return out

    async def _duplicate(self, s, group_id: int, symbol: str, side: str, price: float | None) -> GroupSignal | None:
        if price is None:
            return None
        rows = (await s.execute(select(GroupSignal).where(
            GroupSignal.group_id == group_id, GroupSignal.kind == "SIGNAL", GroupSignal.side == side,
            GroupSignal.status.in_(("QUEUED", "OPEN")), GroupSignal.created_at >= _now() - DEDUP_WINDOW))).scalars().all()
        for g in rows:
            if _same_symbol(g.symbol, symbol) and g.entry and abs(g.entry - price) / price * 100 <= DEDUP_PCT:
                return g
        return None

    def _queue(self, s, chat_id: int, kind: str, text: str, signal: GroupSignal | None, send_at: datetime) -> GroupPost:
        p = GroupPost(chat_id=chat_id, kind=kind, text=text, status="QUEUED", send_at=send_at,
                      signal_id=signal.id if signal is not None else None)
        s.add(p)
        return p

    # -- entry points ----------------------------------------------------------
    async def on_open(self, source_type: str, source_key: str, key: str, symbol: str, side: str,
                      entry: float | None, sl: float | None, tps: list[float] | None, *,
                      zone: tuple[float, float] | None = None, limit_order: bool = False,
                      chase_pct: float = 0.05) -> int:
        """Post a new signal to every matching group. Returns how many groups got it."""
        try:
            return await self._on_open(source_type, source_key, key, symbol, side, entry, sl, list(tps or []),
                                       zone, limit_order, chase_pct)
        except Exception as e:  # noqa: BLE001 - never break trading
            logger.warning(f"[GROUPS] on_open failed: {e}")
            return 0

    async def _on_open(self, source_type, source_key, key, symbol, side, entry, sl, tps, zone, limit_order, chase_pct):
        n = 0
        async with self.sf() as s:
            for route, group in await self._routes(s, source_type, source_key, symbol):
                route.counter = (route.counter or 0) + 1
                every = max(1, int(route.sample_every or 1))
                if (route.counter - 1) % every:
                    continue
                if await self._duplicate(s, group.id, symbol, side, entry):
                    continue
                send_at = _now() + timedelta(seconds=int(route.delay_s or 0))
                # an earlier heads-up from the same source becomes this signal's thread
                hu = (await s.execute(select(GroupSignal).where(
                    GroupSignal.group_id == group.id, GroupSignal.kind == "HEADSUP", GroupSignal.status == "OPEN",
                    GroupSignal.symbol == symbol, GroupSignal.side == side,
                    GroupSignal.route_id == route.id).order_by(desc(GroupSignal.created_at)))).scalars().first()
                chase = None
                if entry is not None and not limit_order and not zone:
                    chase = entry * (1 + chase_pct / 100.0) if side == "BUY" else entry * (1 - chase_pct / 100.0)
                text = T.entry(symbol, side, entry, sl, tps, zone=zone, chase_limit=chase, limit_order=limit_order)
                if hu is not None:
                    hu.status = "CLOSED"
                    text = text + "\n<i>(the setup from the heads-up above)</i>"
                g = GroupSignal(group_id=group.id, route_id=route.id, key=str(key), kind="SIGNAL", symbol=symbol,
                                side=side, entry=entry, sl=sl, tps=tps, tps_hit=[], status="QUEUED",
                                entry_low=min(zone) if zone else None, entry_high=max(zone) if zone else None,
                                text=text, send_at=send_at)
                s.add(g)
                await s.flush()
                p = self._queue(s, group.chat_id, "signal", text, g, send_at)
                if hu is not None and hu.message_id:
                    p.reply_to = hu.message_id
                n += 1
            await s.commit()
        return n

    async def on_headsup(self, source_type: str, source_key: str, key: str, symbol: str, side: str,
                         level: float, cross: str, stop: float | None) -> int:
        try:
            n = 0
            async with self.sf() as s:
                for route, group in await self._routes(s, source_type, source_key, symbol):
                    if not route.headsups or route.lifecycle != "full":
                        continue
                    open_hu = (await s.execute(select(GroupSignal).where(
                        GroupSignal.group_id == group.id, GroupSignal.kind == "HEADSUP", GroupSignal.status == "OPEN",
                        GroupSignal.symbol == symbol, GroupSignal.side == side))).scalars().first()
                    if open_hu is not None:
                        continue
                    text = T.headsup(symbol, side, level, cross, stop)
                    send_at = _now() + timedelta(seconds=int(route.delay_s or 0))
                    g = GroupSignal(group_id=group.id, route_id=route.id, key=str(key), kind="HEADSUP", symbol=symbol,
                                    side=side, entry=level, sl=stop, tps=[], status="OPEN", text=text, send_at=send_at)
                    s.add(g)
                    await s.flush()
                    self._queue(s, group.chat_id, "headsup", text, g, send_at)
                    n += 1
                await s.commit()
            return n
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[GROUPS] on_headsup failed: {e}")
            return 0

    async def on_withdrawn(self, key: str) -> None:
        try:
            async with self.sf() as s:
                rows = (await s.execute(select(GroupSignal).where(
                    GroupSignal.key == str(key), GroupSignal.kind == "HEADSUP", GroupSignal.status == "OPEN"))).scalars().all()
                for g in rows:
                    g.status = "WITHDRAWN"
                    grp = await s.get(SignalGroup, g.group_id)
                    if grp is not None:
                        self._queue(s, grp.chat_id, "withdrawn", T.withdrawn(g.symbol, g.side), g, _now())
                await s.commit()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[GROUPS] on_withdrawn failed: {e}")

    async def on_event(self, key: str, ev: dict[str, Any]) -> None:
        try:
            await self._on_event(str(key), ev)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[GROUPS] on_event failed: {e}")

    async def _on_event(self, key: str, ev: dict[str, Any]) -> None:
        typ = ev.get("type")
        async with self.sf() as s:
            rows = (await s.execute(select(GroupSignal).where(
                GroupSignal.key == key, GroupSignal.kind == "SIGNAL",
                GroupSignal.status.in_(("QUEUED", "OPEN"))))).scalars().all()
            for g in rows:
                grp = await s.get(SignalGroup, g.group_id)
                route = await s.get(SignalRoute, g.route_id) if g.route_id else None
                if grp is None:
                    continue
                basic = route is not None and route.lifecycle == "basic"
                text, status = None, None
                if typ == "tp":
                    n = int(ev.get("n") or (len(g.tps_hit or []) + 1))
                    hit = list(g.tps_hit or [])
                    if n in hit:
                        continue
                    hit.append(n)
                    g.tps_hit = hit
                    last = bool(g.tps) and n >= len(g.tps)
                    text = T.tp_hit(n, ev.get("price"), T.r_of(g.side, g.entry, g.sl, ev.get("price")),
                                    move_be=(n == 1 and not last), last=last)
                    status = f"TP{n} hit" + (", all targets done" if last else ", running")
                elif typ == "be":
                    text, status = T.breakeven(), "stop at entry, running"
                elif typ == "sl_moved":
                    text, status = T.stop_moved(ev.get("price")), "stop moved, running"
                elif typ == "in_zone" and g.entry_low and g.entry_high:
                    text = T.in_zone(g.symbol, g.side, (g.entry_low, g.entry_high))
                elif typ == "cancelled":
                    g.status = "CANCELLED"
                    g.closed_at = _now()
                    text, status = T.cancelled(g.symbol, ev.get("why") or "entry never reached"), "cancelled"
                elif typ == "closed":
                    r = ev.get("r")
                    if r is None:
                        exits = [x for x in (ev.get("exits") or []) if x is not None]
                        if not exits and ev.get("price") is not None:
                            exits = [ev["price"]]
                        rs = [T.r_of(g.side, g.entry, g.sl, x) for x in exits]
                        rs = [x for x in rs if x is not None]
                        r = sum(rs) / len(rs) if rs else None
                    g.result_r = r
                    g.status = "CLOSED"
                    g.closed_at = _now()
                    text = T.closed(r, ev.get("how") or "closed")
                    status = f"closed {T.fmt_r(r)}" if r is not None else "closed"
                if text is None:
                    continue
                if basic and typ not in ("closed", "cancelled"):
                    continue
                if g.status == "QUEUED" and typ in ("tp", "be", "sl_moved", "in_zone"):
                    pass                                   # the reply waits in the queue behind its signal
                self._queue(s, grp.chat_id, typ, text, g, max(_now(), g.send_at or _now()))
                if status:
                    self._queue(s, grp.chat_id, "edit", status, g, max(_now(), g.send_at or _now()))
                if typ == "closed" and route is not None:
                    await self._maybe_pause(s, route)
            await s.commit()

    async def _maybe_pause(self, s, route: SignalRoute) -> None:
        n = int(route.max_losing_streak or 0)
        if n <= 0:
            return
        last = (await s.execute(select(GroupSignal).where(
            GroupSignal.route_id == route.id, GroupSignal.status == "CLOSED").order_by(
            desc(GroupSignal.closed_at)).limit(n))).scalars().all()
        if len(last) >= n and all((g.result_r or 0) < -0.05 for g in last):
            route.paused_reason = f"{n} losing signals in a row"
            msg = (f"⏸ Group route paused: {route.source_type} {route.source_key}"
                   f"{' ' + route.symbol if route.symbol else ''} after {n} losses in a row. "
                   f"Resume it on the Groups page when you're ready.")
            logger.warning(f"[GROUPS] {msg}")
            if self.admin_notify is not None:
                try:
                    await self.admin_notify(msg)
                except Exception:  # noqa: BLE001
                    pass


def _same_symbol(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return False
    norm = lambda x: T.name(x)          # "XAUUSDm", "XAUUSD", "GOLD" -> one display name
    return norm(a) == norm(b) or a.upper() == b.upper()


group_router = GroupRouter()
