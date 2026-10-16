"""
backend/services/signal_outcome.py

What every signal's own plan would have done - and therefore what each gate
that skipped one cost.

The arithmetic is `trade_aftermath.measure`, deliberately: one walk over the
bars, one rule for a bar that covers both stop and target, one sign
convention. Two copies of that would drift, and a SELL scored with the wrong
sign in one table and the right sign in the other is exactly the disagreement
nobody notices.

WHAT THE NUMBERS ARE AND ARE NOT
--------------------------------
The fill is assumed at the signal's `entry_price` at signal time. That is
right for a market entry and optimistic for anything that waited for a
retest: a skipped signal whose entry was never reached is scored as if it had
filled. So `gate_cost` reports what a gate gave up UNDER THAT ASSUMPTION, and
a gate that only ever skips limit-style entries can look worse than it is.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from backend.services.trade_aftermath import DEFAULT_WATCH_HOURS, _r_of, measure
from backend.utils.logger import get_logger

logger = get_logger(__name__)


def gate_of(skip_reason: str | None) -> str:
    """The gate that fired: the first clause of a "; "-joined reason list.

    Keying on the whole string would split one gate across as many keys as
    there are combinations of the others that happened to fire alongside it.
    """
    if not skip_reason:
        return ""
    return str(skip_reason).split(";")[0].strip()[:80]


def outcome_r(m: dict, *, entry: float, stop: float | None, tp: float | None,
              direction: str) -> float | None:
    """What following the plan to the letter would have banked, in R."""
    if m.get("hit_tp") and tp is not None:
        return _r_of(tp, entry, stop, direction)
    if m.get("hit_sl"):
        return -1.0 if stop is not None else None
    return m.get("r_at_watch_end")


async def measure_signal(sig, *, watch_hours: float = DEFAULT_WATCH_HOURS,
                         timeframe: str = "M5") -> dict[str, Any] | None:
    from backend.mt5.data_fetcher import DataFetcher

    start = getattr(sig, "signal_time", None) or getattr(sig, "created_at", None)
    if start is None or not sig.entry_price or not sig.stop_loss:
        return None                       # nothing to score without a plan
    watch_until = start + timedelta(hours=watch_hours)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    end = min(watch_until, now)
    if end <= start:
        return None
    try:
        frame = await DataFetcher.get_data_range(
            sig.symbol, timeframe, start.replace(tzinfo=timezone.utc),
            end.replace(tzinfo=timezone.utc))
    except Exception as exc:              # noqa: BLE001
        logger.warning(f"[SIGNAL-OUTCOME] {sig.symbol}: no bars ({exc})")
        return None

    direction = str(sig.direction or "").upper()
    if direction in ("LONG", "BULLISH"):
        direction = "BUY"
    elif direction in ("SHORT", "BEARISH"):
        direction = "SELL"
    m = measure(frame, direction=direction, entry_price=sig.entry_price,
                stop_loss=sig.stop_loss, take_profit=sig.tp1_price,
                exit_price=None, closed_at=start, watch_until=watch_until)
    m["direction"] = direction
    m["watch_until"] = watch_until
    m["watch_hours"] = watch_hours
    m["watch_complete"] = bool(now >= watch_until or m["hit_tp"] or m["hit_sl"])
    m["outcome_r"] = outcome_r(m, entry=sig.entry_price, stop=sig.stop_loss,
                               tp=sig.tp1_price, direction=direction)
    note = m.get("note") or ""
    if not sig.acted_on:
        note = ("fill assumed at entry_price at signal time; " + note).strip("; ")
    m["note"] = note
    return m


async def sweep(session, *, user_id: str | None = None, lookback_days: int = 30,
                watch_hours: float = DEFAULT_WATCH_HOURS, timeframe: str = "M5",
                limit: int = 300) -> dict[str, Any]:
    """Score every signal still needing it. Idempotent; doubles as a backfill."""
    from sqlalchemy import select

    from backend.data.models import Signal, SignalOutcome

    since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=lookback_days)
    q = select(Signal).where(Signal.created_at >= since)
    if user_id:
        q = q.where(Signal.user_id == user_id)
    sigs = (await session.execute(q.order_by(Signal.created_at.desc()))).scalars().all()

    done: dict[int, Any] = {}
    if sigs:
        existing = (await session.execute(select(SignalOutcome).where(
            SignalOutcome.signal_id.in_([s_.id for s_ in sigs])))).scalars().all()
        done = {r.signal_id: r for r in existing}

    stats = {"considered": len(sigs), "measured": 0, "already_complete": 0,
             "no_plan_or_bars": 0, "errors": 0}
    for sg in sigs:
        if stats["measured"] >= limit:
            break
        row = done.get(sg.id)
        if row is not None and row.watch_complete:
            stats["already_complete"] += 1
            continue
        try:
            m = await measure_signal(sg, watch_hours=watch_hours, timeframe=timeframe)
        except Exception as exc:          # noqa: BLE001
            logger.warning(f"[SIGNAL-OUTCOME] signal {sg.id}: {exc}")
            stats["errors"] += 1
            continue
        if m is None:
            stats["no_plan_or_bars"] += 1
            continue
        fields = dict(
            user_id=sg.user_id, symbol=sg.symbol, strategy_id=sg.strategy_id,
            timeframe=sg.timeframe, direction=m["direction"],
            acted_on=bool(sg.acted_on), skip_gate=gate_of(sg.skip_reason),
            signal_time=sg.signal_time or sg.created_at,
            entry_price=sg.entry_price, stop_loss=sg.stop_loss,
            take_profit=sg.tp1_price, watch_until=m["watch_until"],
            watch_hours=m["watch_hours"], watch_complete=m["watch_complete"],
            bars_seen=m["bars_seen"], hit_tp=m["hit_tp"],
            tp_minutes=m["tp_minutes"], hit_sl=m["hit_sl"],
            sl_minutes=m["sl_minutes"], best_r=m["best_r"],
            outcome_r=m["outcome_r"], ambiguous_bar=m["ambiguous_bar"],
            note=m["note"] or "")
        if row is None:
            session.add(SignalOutcome(signal_id=sg.id, **fields))
        else:
            for k, v in fields.items():
                setattr(row, k, v)
        stats["measured"] += 1
    await session.commit()
    logger.info(f"[SIGNAL-OUTCOME] {stats}")
    return stats


def gate_cost(rows: list) -> list[dict[str, Any]]:
    """Per gate: how many signals it skipped and what they would have done.

    Pure, over already-loaded SignalOutcome rows. `net_r` is the sum of what
    the skipped signals would have banked: POSITIVE means the gate threw away
    money, negative means it saved money. Only finished rows count - a
    provisional outcome is a guess about a window that has not happened yet.
    """
    by: dict[str, dict] = {}
    for r in rows:
        if r.acted_on or not r.watch_complete or r.outcome_r is None:
            continue
        g = r.skip_gate or "(no reason recorded)"
        d = by.setdefault(g, {"gate": g, "skipped": 0, "would_win": 0,
                              "would_lose": 0, "net_r": 0.0})
        d["skipped"] += 1
        d["net_r"] += float(r.outcome_r)
        if r.outcome_r > 0:
            d["would_win"] += 1
        elif r.outcome_r < 0:
            d["would_lose"] += 1
    out = []
    for d in by.values():
        d["net_r"] = round(d["net_r"], 3)
        d["mean_r"] = round(d["net_r"] / d["skipped"], 4) if d["skipped"] else None
        d["win_rate"] = round(100.0 * d["would_win"] / d["skipped"], 1) if d["skipped"] else None
        d["verdict"] = ("COSTING_MONEY" if d["net_r"] > 0 else
                        "SAVING_MONEY" if d["net_r"] < 0 else "NEUTRAL")
        out.append(d)
    return sorted(out, key=lambda d: -d["net_r"])
