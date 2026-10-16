"""
backend/services/trade_aftermath.py

What happened after someone closed a trade by hand.

THE QUESTION THIS ANSWERS
-------------------------
A trade that hits its stop or its target was decided by the plan. A trade a
person or an agent CLOSED has an unanswered question attached: would it have
reached the target, and how far did it get before it turned? The journal
records the exit and nothing after it, so the one decision made by hand is the
one decision never scored.

Three things are measured, in the window the trade could still have been held:

  1. did it go on to reach the TAKE PROFIT, and how long did that take
  2. the furthest it got IN FAVOUR before it reached the STOP
  3. where it stood when the holding window ran out, if it reached neither

THE SPLIT IN THIS MODULE
------------------------
`measure` is pure: bars in, numbers out. It takes a DataFrame and never
touches MT5, a session or the clock, so the arithmetic is testable without a
broker. `sweep` is the part that knows about the database and the terminal.
Keeping them apart matters because the arithmetic is the part that is easy to
get quietly wrong - an inverted sign on a SELL is invisible in aggregate and
reverses every conclusion drawn from the table.

A BAR THAT CONTAINS BOTH
------------------------
When one bar's range covers the stop AND the target there is no way to know
which came first; the bar does not record the path. This records the STOP,
flags `ambiguous_bar`, and says so in the note. Assuming the target would be
assuming the flattering answer, and this table exists to grade decisions.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np
import pandas as pd

from backend.utils.logger import get_logger

logger = get_logger(__name__)

# How long a trade could still have been held, when nothing else says.
# Not a trading rule - the horizon over which "would it have come back" is a
# fair question. 0 would make the table empty; a month would make every trade
# look recoverable.
DEFAULT_WATCH_HOURS = 72.0

# A close is only worth scoring if the market, not the plan, was left to
# decide. Anything whose reason names a stop or a target was the plan working.
_PLANNED_TOKENS = ("TP", "SL", "TAKE_PROFIT", "STOP_LOSS", "TRAIL", "BREAKEVEN", "BE")

# Who pressed it. The strings are the ones already written to
# `Trade.exit_reason` around the codebase.
_CLOSED_BY = {
    "MANUAL": "USER",
    "MANUAL_OVERRIDE": "USER",
    "CLIENT": "BOT",
    "AGENT": "AGENT",
    "NO_POSITIONS": "SYSTEM",
    "MT5_SYNC_CLOSED": "SYSTEM",
    "GHOST_UNRESOLVED": "SYSTEM",
    "PARTIAL_GHOST": "SYSTEM",
}

# How much better the trade had to go before "you closed too early" is a claim
# worth making rather than noise.
FORGONE_R_BAND = 0.5


def was_planned_exit(exit_reason: str | None) -> bool:
    """True when the stop or the target closed it, so there is nothing to score."""
    r = (exit_reason or "").upper()
    if not r:
        return False
    return any(tok in r for tok in _PLANNED_TOKENS)


def closed_by(exit_reason: str | None) -> str:
    return _CLOSED_BY.get((exit_reason or "").upper(), "UNKNOWN")


def _r_of(price: float, entry: float, stop: float, direction: str) -> float | None:
    """A price as a multiple of the risk taken. None when risk is undefined."""
    if entry is None or stop is None:
        return None
    risk = abs(float(entry) - float(stop))
    if risk <= 0:
        return None
    move = (float(price) - float(entry)) if direction == "BUY" else (float(entry) - float(price))
    return move / risk


def measure(bars: pd.DataFrame, *, direction: str, entry_price: float,
            stop_loss: float | None, take_profit: float | None,
            exit_price: float | None, closed_at: datetime,
            watch_until: datetime) -> dict[str, Any]:
    """Walk the bars after the close and report what the trade went on to do.

    `bars` must carry `time` (epoch SECONDS - see datasets.normalised_times;
    reading them as any other unit puts every bar in 1970), `high`, `low` and
    `close`, ordered ascending, and should already be clipped to the window.

    Nothing here mutates `bars`.
    """
    out: dict[str, Any] = {
        "bars_seen": 0, "hit_tp": False, "hit_sl": False,
        "tp_at": None, "sl_at": None, "tp_minutes": None, "sl_minutes": None,
        "best_price": None, "best_r": None, "best_at": None, "best_minutes": None,
        "r_at_watch_end": None, "ambiguous_bar": False, "note": "",
    }
    if bars is None or not len(bars):
        out["note"] = "no bars after the close - nothing could be measured"
        return out

    side = str(direction or "").upper()
    if side not in ("BUY", "SELL"):
        # Never guess. Treating "anything but BUY" as a SELL would score a blank
        # or malformed direction with an inverted sign - a number that looks
        # exactly like a real result and reverses every conclusion drawn from it.
        out["note"] = f"direction {direction!r} is neither BUY nor SELL - not measured"
        return out
    up = side == "BUY"
    t = bars["time"].to_numpy(dtype="int64")
    hi = bars["high"].to_numpy(dtype=float)
    lo = bars["low"].to_numpy(dtype=float)
    cl = bars["close"].to_numpy(dtype=float)
    out["bars_seen"] = int(len(t))

    def when(i: int) -> datetime:
        return datetime.fromtimestamp(int(t[i]), tz=timezone.utc).replace(tzinfo=None)

    def minutes(i: int) -> int:
        return int(max(0, (when(i) - closed_at).total_seconds() // 60))

    # The furthest IN FAVOUR each bar reached: the high for a long, the low
    # for a short. Vectorised so a 72-hour M5 window is one pass.
    favour = hi if up else lo
    best_i = int(np.argmax(favour) if up else np.argmin(favour))

    sl = None if stop_loss in (None, 0) else float(stop_loss)
    tp = None if take_profit in (None, 0) else float(take_profit)

    # First touch of each, independently; whichever comes first ends the trade.
    sl_hits = np.flatnonzero(lo <= sl) if (sl is not None and up) else (
        np.flatnonzero(hi >= sl) if sl is not None else np.empty(0, dtype=int))
    tp_hits = np.flatnonzero(hi >= tp) if (tp is not None and up) else (
        np.flatnonzero(lo <= tp) if tp is not None else np.empty(0, dtype=int))
    i_sl = int(sl_hits[0]) if len(sl_hits) else None
    i_tp = int(tp_hits[0]) if len(tp_hits) else None

    end_i = len(t) - 1
    if i_sl is not None and i_tp is not None:
        if i_sl < i_tp:
            first, out["hit_sl"], end_i = "SL", True, i_sl
        elif i_tp < i_sl:
            first, out["hit_tp"], end_i = "TP", True, i_tp
        else:
            # The same bar covers both and a bar does not record its own path.
            # The stop is recorded: assuming the target would be assuming the
            # flattering answer, in a table whose whole purpose is grading.
            first, out["hit_sl"], end_i = "SL", True, i_sl
            out["ambiguous_bar"] = True
            out["note"] = ("one bar's range covered both the stop and the target; "
                           "the stop is recorded because a bar cannot say which "
                           "came first")
    elif i_sl is not None:
        first, out["hit_sl"], end_i = "SL", True, i_sl
    elif i_tp is not None:
        first, out["hit_tp"], end_i = "TP", True, i_tp
    else:
        first = None

    if out["hit_sl"]:
        out["sl_at"], out["sl_minutes"] = when(i_sl), minutes(i_sl)
    if out["hit_tp"]:
        out["tp_at"], out["tp_minutes"] = when(i_tp), minutes(i_tp)

    # The best point only counts while the trade was still alive, so the search
    # is capped at the bar that ended it. Reporting a high that happened two
    # days AFTER the stop was hit would be measuring a trade that no longer
    # existed.
    if best_i > end_i:
        seg = favour[:end_i + 1]
        best_i = int(np.argmax(seg) if up else np.argmin(seg))
    out["best_price"] = float(favour[best_i])
    out["best_at"], out["best_minutes"] = when(best_i), minutes(best_i)
    out["best_r"] = _r_of(out["best_price"], entry_price, sl, "BUY" if up else "SELL")

    if first is None:
        out["r_at_watch_end"] = _r_of(float(cl[end_i]), entry_price, sl,
                                      "BUY" if up else "SELL")
        if when(end_i) < watch_until - timedelta(minutes=1):
            out["note"] = (out["note"] or
                           "the window has not fully elapsed yet - this row is "
                           "provisional and will be measured again")
    return out


def verdict_of(closed_r: float | None, best_r: float | None, *, hit_tp: bool,
               hit_sl: bool) -> tuple[str, float | None]:
    """One word for what the close was worth, and the R it gave up.

    `forgone_r` is the ceiling that was available after the close minus what
    the close actually banked. Negative means the close did better than
    anything the market offered afterwards.
    """
    if closed_r is None or best_r is None:
        return ("UNKNOWN", None)
    forgone = float(best_r) - float(closed_r)
    if hit_tp and forgone > 0:
        return ("LEFT_MONEY", forgone)
    if hit_sl and forgone <= FORGONE_R_BAND:
        # It went on to the stop and never offered much more: closing was right.
        return ("SAVED_LOSS", forgone)
    if forgone > FORGONE_R_BAND:
        return ("LEFT_MONEY", forgone)
    return ("FAIR", forgone)


# -- the half that knows about the database and the terminal ----------------


def _watch_hours_for(trade, default_hours: float) -> float:
    """How long this trade could still have been held.

    A strategy that sets `max_hold_bars` has already answered the question, in
    M5 bars; anything else falls back to the caller's window. The point is
    that the horizon matches the system's own rules rather than being a round
    number someone liked.
    """
    try:
        from backend.strategies.registry import get_all_strategies
        strat = (get_all_strategies() or {}).get(getattr(trade, "strategy_id", "") or "")
        params = getattr(strat, "params", None) or getattr(strat, "default_params", None)
        bars_ = int(getattr(params, "max_hold_bars", 0) or 0)
        if bars_ > 0:
            return max(1.0, bars_ * 5.0 / 60.0)      # M5 bars -> hours
    except Exception:                                # noqa: BLE001
        pass
    return float(default_hours)


async def measure_trade(trade, *, watch_hours: float = DEFAULT_WATCH_HOURS,
                        timeframe: str = "M5") -> dict[str, Any] | None:
    """Fetch the bars after this trade's close and measure them.

    Returns the measurement plus the window it used, or None when the trade
    is not one this table is for.
    """
    from backend.mt5.data_fetcher import DataFetcher

    closed_at = getattr(trade, "exit_time", None)
    if closed_at is None or was_planned_exit(getattr(trade, "exit_reason", None)):
        return None

    hours = _watch_hours_for(trade, watch_hours)
    watch_until = closed_at + timedelta(hours=hours)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    end = min(watch_until, now)
    if end <= closed_at:
        return None

    try:
        frame = await DataFetcher.get_data_range(
            trade.symbol, timeframe,
            closed_at.replace(tzinfo=timezone.utc),
            end.replace(tzinfo=timezone.utc))
    except Exception as exc:                         # noqa: BLE001
        logger.warning(f"[AFTERMATH] {trade.symbol}: no bars after the close ({exc})")
        return None

    out = measure(frame, direction=trade.direction, entry_price=trade.entry_price,
                  stop_loss=trade.stop_loss, take_profit=trade.take_profit,
                  exit_price=trade.exit_price, closed_at=closed_at,
                  watch_until=watch_until)
    out["watch_until"] = watch_until
    out["watch_hours"] = hours

    # THE CLOSE TIME MAY BE ON THE BROKER'S CLOCK. The journal stamps
    # `exit_time` two ways: `datetime.utcnow()` when the bot closes a trade
    # itself, and `utcfromtimestamp(deal.time)` when the time comes from an
    # MT5 deal - and MT5 deal times are SERVER time, not UTC. The bars above
    # are true UTC. On a broker whose server clock is UTC (Deriv, measured
    # +0.0h) the two agree; on one that is not (FundedNext, +3h) a deal-sourced
    # close is that many hours "later" than it really was, and the window
    # skips the first hours after the close - usually the ones that matter.
    # Which writer stamped a given trade is not recorded, so this cannot be
    # corrected per trade. It is SAID instead, on the row, rather than letting
    # a shifted measurement pass as a clean one.
    try:
        from backend.mt5.data_fetcher import server_offset_status
        off = server_offset_status().get("hours")
        off = float(off) if off not in (None, "") else None
    except Exception:                                # noqa: BLE001
        off = None
    if off:
        warn = (f"broker server clock is UTC{off:+g}h; if this close time came "
                f"from an MT5 deal it is on that clock, so the window may start "
                f"up to {abs(off):g}h off")
        out["note"] = f"{out['note']}; {warn}" if out.get("note") else warn
    # Complete only when the window has actually elapsed. A trade closed an
    # hour ago must be measured again tomorrow rather than judged on the one
    # hour that happened to exist when the sweep ran.
    out["watch_complete"] = bool(now >= watch_until or out["hit_tp"] or out["hit_sl"])
    return out


async def sweep(session, *, user_id: str | None = None, lookback_days: int = 30,
                watch_hours: float = DEFAULT_WATCH_HOURS, timeframe: str = "M5",
                limit: int = 300) -> dict[str, Any]:
    """Measure every hand-closed trade that still needs it.

    Idempotent and resumable: rows whose window has elapsed are skipped, and
    everything else is re-measured, so running this daily converges rather
    than duplicating. That is also what makes it usable as a BACKFILL - point
    it at a longer `lookback_days` and it fills in history.
    """
    from sqlalchemy import select

    from backend.data.models import Trade, TradeAftermath

    since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=lookback_days)
    q = select(Trade).where(Trade.exit_time.isnot(None), Trade.exit_time >= since)
    if user_id:
        q = q.where(Trade.user_id == user_id)
    trades = (await session.execute(q.order_by(Trade.exit_time.desc()))).scalars().all()

    done: dict[int, Any] = {}
    if trades:
        existing = (await session.execute(
            select(TradeAftermath).where(
                TradeAftermath.trade_id.in_([t.id for t in trades])))).scalars().all()
        done = {r.trade_id: r for r in existing}

    stats = {"considered": len(trades), "skipped_planned": 0, "measured": 0,
             "already_complete": 0, "no_bars": 0, "errors": 0}
    for trade in trades:
        if stats["measured"] >= limit:
            break
        if was_planned_exit(trade.exit_reason):
            stats["skipped_planned"] += 1
            continue
        row = done.get(trade.id)
        if row is not None and row.watch_complete:
            stats["already_complete"] += 1
            continue
        try:
            m = await measure_trade(trade, watch_hours=watch_hours, timeframe=timeframe)
        except Exception as exc:                     # noqa: BLE001
            logger.warning(f"[AFTERMATH] trade {trade.id}: {exc}")
            stats["errors"] += 1
            continue
        if m is None:
            stats["no_bars"] += 1
            continue

        closed_r = _r_of(trade.exit_price, trade.entry_price, trade.stop_loss,
                         str(trade.direction).upper()) if trade.exit_price else None
        verdict, forgone = verdict_of(closed_r, m["best_r"],
                                      hit_tp=m["hit_tp"], hit_sl=m["hit_sl"])
        fields = dict(
            user_id=trade.user_id, symbol=trade.symbol,
            strategy_id=trade.strategy_id, direction=trade.direction,
            closed_at=trade.exit_time, close_reason=(trade.exit_reason or ""),
            closed_by=closed_by(trade.exit_reason),
            entry_price=trade.entry_price, exit_price=trade.exit_price,
            stop_loss=trade.stop_loss, take_profit=trade.take_profit,
            closed_r=closed_r, watch_until=m["watch_until"],
            watch_hours=m["watch_hours"], watch_complete=m["watch_complete"],
            bars_seen=m["bars_seen"], hit_tp=m["hit_tp"], tp_at=m["tp_at"],
            tp_minutes=m["tp_minutes"], hit_sl=m["hit_sl"], sl_at=m["sl_at"],
            sl_minutes=m["sl_minutes"], best_price=m["best_price"],
            best_r=m["best_r"], best_at=m["best_at"],
            best_minutes=m["best_minutes"], r_at_watch_end=m["r_at_watch_end"],
            verdict=verdict, forgone_r=forgone, note=m["note"] or "",
        )
        if row is None:
            session.add(TradeAftermath(trade_id=trade.id, **fields))
        else:
            for k, v in fields.items():
                setattr(row, k, v)
        stats["measured"] += 1

    await session.commit()
    logger.info(f"[AFTERMATH] {stats}")
    return stats
