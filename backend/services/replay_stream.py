"""
backend/services/replay_stream.py

[Phase 13 §C.3] Replay event stream for backtests.

What this streams, and what it honestly cannot:

The backtest is two-phase. Phase 1 walks bars calling `strategy_engine.on_bar()`
and collects `TradeSignal`s; Phase 2 hands the whole signal list to the
simulation engine in a worker thread and gets trades back at the end. **Trades do
not exist during Phase 1.** So a stream that promised live `trade_opened` events
would be inventing them.

What IS live and real during Phase 1 is the bar being processed and the signal
the moment a strategy fires it — which is most of the value, because a signal is
where the strategy's decision is made. This module streams exactly that, and the
frontend flips the same chart into a full trade replay once Phase 2 returns.

Two mechanisms keep a fast engine from drowning the socket:

  * **Batching** — bars accumulate and flush every `batch_size` bars, so one
    message carries hundreds of bars instead of one.
  * **Throttling** — a flush is suppressed if the previous one for that leg went
    out less than `min_interval_s` ago. A backtest can process tens of thousands
    of bars per second; a screen can show ~60 frames. The throttle is what makes
    the difference not matter.

Signals are never throttled or dropped. Bars are scenery; signals are the
information.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

from backend.utils.logger import get_logger

logger = get_logger(__name__)

# Bars per flush. 400 x ~48 bytes of JSON is a ~19 KB message — large enough
# that per-message overhead is negligible, small enough to stay well inside a
# single TCP window.
DEFAULT_BATCH_SIZE = 400

# Floor between flushes for one leg. 120 ms is ~8 updates/second: visibly
# animated, far below the rate at which the browser would start dropping frames.
DEFAULT_MIN_INTERVAL_S = 0.12

# Ceiling on the continuous per-leg series retained for replay-mode scrubbing.
# Above this the series is decimated by a stride (see `downsample`), which
# preserves the shape of the whole run rather than truncating it to the first
# N bars the way the per-trade chart slice does.
MAX_SERIES_BARS = 6000


def downsample(bars: list[dict], limit: int = MAX_SERIES_BARS) -> list[dict]:
    """
    Reduce a bar series to at most `limit` entries while keeping the run's full
    time span.

    Decimation is by stride with OHLC aggregation inside each bucket, not a
    plain "take every Nth bar" — dropping bars outright would erase the highs
    and lows that trades actually resolved against, which is the one thing a
    replay chart must not lie about.
    """
    n = len(bars)
    if n <= limit:
        return bars
    stride = (n + limit - 1) // limit
    out: list[dict] = []
    for i in range(0, n, stride):
        bucket = bars[i:i + stride]
        if not bucket:
            continue
        out.append({
            "time": bucket[0]["time"],
            "open": bucket[0]["open"],
            "high": max(b["high"] for b in bucket),
            "low": min(b["low"] for b in bucket),
            "close": bucket[-1]["close"],
        })
    return out


class ReplayStreamer:
    """
    One instance per backtest run. Not thread-safe: it is driven from the
    single asyncio task that owns the Phase-1 loop.

    Every emit is fire-and-forget and individually guarded. A replay stream is a
    visualisation, and a failure to render must never be able to take down a
    backtest — so a broken socket costs the animation, not the run.
    """

    def __init__(
        self,
        manager: Any,
        user_id: str,
        mode: str = "single",
        batch_size: int = DEFAULT_BATCH_SIZE,
        min_interval_s: float = DEFAULT_MIN_INTERVAL_S,
        enabled: bool = True,
    ):
        self.manager = manager
        self.user_id = user_id
        self.mode = mode
        self.run_id = uuid.uuid4().hex[:12]
        self.batch_size = max(1, batch_size)
        self.min_interval_s = max(0.0, min_interval_s)
        self.enabled = enabled

        self._legs: list[dict] = []
        self._buffer: dict[str, list[dict]] = {}
        self._last_flush: dict[str, float] = {}
        self._cursor: dict[str, int] = {}
        self._total: dict[str, int] = {}
        self._signal_count: dict[str, int] = {}
        # Continuous per-leg series retained for replay-mode scrubbing (§C.7).
        self._series: dict[str, list[dict]] = {}
        self._dropped_sends = 0
        # Strong references to in-flight send tasks — see `_send`.
        self._inflight: set[asyncio.Task] = set()

    # ── plumbing ────────────────────────────────────────────────────────
    async def _deliver(self, payload: dict) -> None:
        """
        The actual send, with the failure handled INSIDE the task.

        This wrapper is not optional. `create_task` returns the moment the
        coroutine is scheduled, so a `try` around the `create_task` call only
        ever catches "there is no running loop" — an exception raised later,
        when the send actually runs, escapes into the task and surfaces as
        asyncio's "Task exception was never retrieved" warning. Once a client
        socket dies mid-run that is one warning-plus-traceback per bar batch,
        which buries the log the user is trying to read. Catching here keeps a
        dead socket to a counter and three debug lines.
        """
        try:
            await self.manager.broadcast_to_user(self.user_id, payload)
        except Exception as e:
            self._note_drop(e)

    def _note_drop(self, e: Exception) -> None:
        self._dropped_sends += 1
        # Log at 1, 10, 100 rather than every failure: a dead socket fails on
        # every send, and the information is "it broke", not "it broke 4,000
        # times".
        if self._dropped_sends in (1, 10, 100):
            logger.debug(f"[replay] send dropped ({self._dropped_sends}): {e}")

    def _send(self, payload: dict) -> None:
        """
        Schedule a broadcast without awaiting it.

        `create_task` rather than `await` deliberately: awaiting a socket write
        inside the bar loop would couple simulation speed to network speed, so a
        slow client would slow the backtest itself.
        """
        if not self.enabled:
            return
        try:
            task = asyncio.create_task(self._deliver(payload))
            # Hold a reference until completion. Without one, the event loop
            # only keeps a weak reference and a task can be garbage-collected
            # mid-flight (documented asyncio behaviour), which silently loses
            # bar batches under load.
            self._inflight.add(task)
            task.add_done_callback(self._inflight.discard)
        except RuntimeError as e:
            # No running loop — the streamer was driven from sync code.
            self._note_drop(e)

    # ── lifecycle ───────────────────────────────────────────────────────
    def init(self, legs: list[dict]) -> None:
        """
        Announce the run's shape up front so the frontend can build its tabs
        before any data arrives.

        `legs` entries: {slot_id, symbol, strategy_id, timeframe}. slot_id is
        the portfolio engine's own slot key, so the same symbol under two
        strategies stays two independent legs rather than being merged into one
        tab (Visualization plan §2, "independent legs").
        """
        self._legs = legs
        self._send({
            "type": "replay_init",
            "run_id": self.run_id,
            "mode": self.mode,
            "legs": legs,
        })

    def leg_start(self, slot_id: str, total_bars: int = 0) -> None:
        self._buffer[slot_id] = []
        self._cursor[slot_id] = 0
        self._total[slot_id] = total_bars
        self._signal_count.setdefault(slot_id, 0)
        self._series.setdefault(slot_id, [])
        self._last_flush[slot_id] = 0.0
        self._send({
            "type": "replay_leg_start",
            "run_id": self.run_id,
            "slot_id": slot_id,
            "total": total_bars,
        })

    def bar(self, slot_id: str, bar: dict) -> None:
        """Feed one processed bar. Flushes when the batch fills and the throttle allows."""
        if not self.enabled:
            return
        buf = self._buffer.setdefault(slot_id, [])
        buf.append(bar)
        self._series.setdefault(slot_id, []).append(bar)
        self._cursor[slot_id] = self._cursor.get(slot_id, 0) + 1
        if len(buf) >= self.batch_size:
            self.flush(slot_id)

    def flush(self, slot_id: str, force: bool = False) -> None:
        buf = self._buffer.get(slot_id)
        if not buf:
            return
        now = time.monotonic()
        if not force and (now - self._last_flush.get(slot_id, 0.0)) < self.min_interval_s:
            # Throttled: keep accumulating rather than dropping. The batch grows
            # past batch_size, which is correct — the client wants the bars, it
            # just doesn't need them in this many separate messages.
            return
        self._last_flush[slot_id] = now
        self._send({
            "type": "replay_bars",
            "run_id": self.run_id,
            "slot_id": slot_id,
            "bars": buf,
            "cursor": self._cursor.get(slot_id, 0),
            "total": self._total.get(slot_id, 0),
        })
        self._buffer[slot_id] = []

    def signal(self, slot_id: str, signal: Any, markings: list | None = None) -> None:
        """
        Emit a detected signal immediately.

        Flushes the leg's pending bars first (forced, ignoring the throttle) so
        the marker never arrives before the bar it belongs on — out-of-order
        arrival would make the chart briefly draw a signal in empty space.
        """
        if not self.enabled:
            return
        self.flush(slot_id, force=True)
        self._signal_count[slot_id] = self._signal_count.get(slot_id, 0) + 1

        get = (lambda k, d=None: signal.get(k, d)) if isinstance(signal, dict) \
            else (lambda k, d=None: getattr(signal, k, d))
        meta = get("metadata") or {}

        self._send({
            "type": "replay_signal",
            "run_id": self.run_id,
            "slot_id": slot_id,
            "time": int(get("time") or get("timestamp") or 0),
            "direction": get("direction"),
            "entry": get("entry_price"),
            "sl": get("stop_loss"),
            "tp": get("take_profit"),
            "confluence_score": get("confluence_score"),
            "signal_type": get("signal_type"),
            "strategy_id": get("strategy_id"),
            # Markings are what make the chart show WHY the signal fired, not
            # just that it did. See strategies/core/markings.py.
            "markings": markings if markings is not None else meta.get("markings", []),
            "confluence_summary": meta.get("confluence_summary", {}),
        })

    def leg_done(self, slot_id: str) -> None:
        self.flush(slot_id, force=True)
        self._send({
            "type": "replay_leg_done",
            "run_id": self.run_id,
            "slot_id": slot_id,
            "signal_count": self._signal_count.get(slot_id, 0),
        })

    def done(self) -> None:
        for slot_id in list(self._buffer):
            self.flush(slot_id, force=True)
        self._send({
            "type": "replay_done",
            "run_id": self.run_id,
            "legs": len(self._legs),
            "signals": sum(self._signal_count.values()),
        })

    # ── replay-mode payload ─────────────────────────────────────────────
    def series_payload(self) -> dict[str, Any]:
        """
        The continuous per-leg series, downsampled, for attachment to the
        finished result.

        This is what fixes V4: `trade_grouper` only ever produced a +/-30-bar
        slice around each individual trade, capped at 500 bars, so there was no
        way to scroll the whole run and see every trade in sequence. This is
        that missing series.
        """
        return {
            "run_id": self.run_id,
            "mode": self.mode,
            "legs": self._legs,
            "series": {
                slot_id: downsample(bars)
                for slot_id, bars in self._series.items()
                if bars
            },
        }

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "legs": len(self._legs),
            "bars": {k: len(v) for k, v in self._series.items()},
            "signals": dict(self._signal_count),
            "dropped_sends": self._dropped_sends,
        }


def bar_from_row(t: Any, row: Any) -> dict:
    """
    Build a chart bar from a DataFrame index value + row.

    Kept here rather than inline in the route so the wire shape has exactly one
    definition — the replay chart and the per-trade chart must agree on it or
    the two views disagree about the same candle.
    """
    from backend.strategies.core.markings import ts as _ts
    return {
        "time": _ts(t),
        "open": float(row["open"]),
        "high": float(row["high"]),
        "low": float(row["low"]),
        "close": float(row["close"]),
    }
