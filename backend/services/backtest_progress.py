"""
backend/services/backtest_progress.py

One owner for a backtest run's 0-100 progress scale.

Why this exists
---------------
Before it, two layers broadcast on the same `backtest_progress` WebSocket
channel with incompatible scales:

  * `api/routes/backtest.py` drove the bar 10 -> 90 across signal generation
    and then set it to 95 ("Finalizing backtest...").
  * `backtester/runner.py::run_backtest`, called immediately after, broadcast
    its OWN absolute percentages: 0 ("starting"), 10 ("running"),
    70 ("engine_complete") — and, on the `save_mode="DISCARD"` path both routes
    use, `stage="complete", pct=100` **before the route had built the response**.

So the bar the user saw went 10 -> 19 -> ... -> 90 -> 95 -> **0** -> 10 -> (long
silence through the actual simulation) -> 70 -> 100, and the UI declared the run
complete while the result was still being assembled. That is the "progress bar
sits on a few numbers and barely moves", and the premature `complete` with no
`result` attached is why a manual refresh was needed to see the trades.

The rules this class enforces
----------------------------
1. **One scale.** The route creates one reporter and hands sub-ranges to the
   phases. A phase reports its own 0.0-1.0 completion and never an absolute
   percentage, so no phase can contradict another.
2. **Monotonic.** A lower percentage than the one already sent is dropped. A
   progress bar that goes backwards is worse than one that stalls.
3. **Time-paced, not work-paced.** Emission is throttled to
   `min_interval_s`, so a fast phase does not flood the socket and a slow phase
   still reports. The previous code emitted every 600 bars, which on a 5,000-bar
   run is eight updates for the whole of phase 1 and none at all for phase 2.
4. **Callable from a worker thread.** The simulation runs under
   `asyncio.to_thread`, so it cannot await. `note()` only writes; the async
   `pump()` task does the broadcasting.
"""

from __future__ import annotations

import asyncio
import time
from typing import Awaitable, Callable

from backend.utils.logger import get_logger

logger = get_logger(__name__)

# Floor between progress broadcasts. ~3/second is smooth to the eye and
# negligible on the socket.
DEFAULT_MIN_INTERVAL_S = 0.30

# How often the pump wakes to see whether a worker thread has noted new
# progress. Shorter than the emit interval so the throttle, not the poll, is
# what paces output.
_POLL_INTERVAL_S = 0.10


class BacktestProgress:
    """
    Owns the absolute 0-100 scale for one run.

    Typical use from a route::

        progress = BacktestProgress(user_id, broadcast, save_state)
        pump = asyncio.create_task(progress.pump())
        ...
        await progress.phase(0.0, 0.05, "Loading data...").set(1.0)
        sig = progress.phase(0.05, 0.60, "Generating signals...")
        ...
        sig.note(i / total)                     # cheap, safe in a tight loop
        ...
        await progress.finish()                 # emits stage="complete", pct=100
        pump.cancel()
    """

    def __init__(
        self,
        user_id: str,
        broadcast: Callable[[str, dict], Awaitable[None]],
        save_state: Callable[[dict], Awaitable[None]] | None = None,
        min_interval_s: float = DEFAULT_MIN_INTERVAL_S,
    ):
        self.user_id = user_id
        self._broadcast = broadcast
        self._save_state = save_state
        self.min_interval_s = max(0.0, min_interval_s)

        self._pct: float = 0.0          # highest percentage actually sent
        self._pending: dict | None = None  # {"pct", "stage", "message"} awaiting a flush
        self._last_emit: float = 0.0
        self._closed = False

    # ── recording (safe from any thread) ──────────────────────────────────
    def record(self, pct: float, stage: str, message: str | None = None) -> None:
        """
        Note an absolute percentage. Pure assignment — no I/O, no awaiting — so
        this is safe to call from the `asyncio.to_thread` worker running the
        simulation, and cheap enough to call inside a per-bar loop.
        """
        if self._closed:
            return
        pct = max(0.0, min(100.0, float(pct)))
        if pct < self._pct:
            return  # never go backwards
        self._pending = {"pct": pct, "stage": stage, "message": message}

    def phase(self, lo: float, hi: float, stage: str) -> "_Phase":
        """A sub-range of the scale that reports 0.0-1.0 within [lo, hi]."""
        return _Phase(self, lo, hi, stage)

    # ── emission (async only) ─────────────────────────────────────────────
    async def flush(self, force: bool = False) -> None:
        """Broadcast the pending value if the throttle allows."""
        if self._closed or not self._pending:
            return
        now = time.monotonic()
        if not force and (now - self._last_emit) < self.min_interval_s:
            return
        payload = self._pending
        self._pending = None
        self._last_emit = now
        self._pct = payload["pct"]

        msg = {"stage": payload["stage"], "pct": int(round(payload["pct"]))}
        if payload.get("message"):
            msg["message"] = payload["message"]

        if self._save_state is not None:
            try:
                await self._save_state(dict(msg))
            except Exception as e:  # persistence is best-effort; the bar is not
                logger.debug(f"[progress] state persist failed: {e}")
        try:
            await self._broadcast(self.user_id, {"type": "backtest_progress", **msg})
        except Exception as e:
            logger.debug(f"[progress] broadcast failed: {e}")

    async def set(self, pct: float, stage: str, message: str | None = None,
                  force: bool = True) -> None:
        """Record and emit in one step — for phase boundaries."""
        self.record(pct, stage, message)
        await self.flush(force=force)

    async def pump(self) -> None:
        """
        Background task: publishes whatever `record()` last noted.

        This is what lets the simulation — which runs in a worker thread and
        cannot await — still move the bar.
        """
        try:
            while not self._closed:
                await self.flush()
                await asyncio.sleep(_POLL_INTERVAL_S)
        except asyncio.CancelledError:
            raise

    async def finish(self) -> None:
        """
        Emit 100%. Deliberately NOT `stage="complete"` — that stage is the
        route's signal that the result payload is attached, and emitting it from
        anywhere else is the bug this module was written to remove.
        """
        await self.set(100.0, "Finalizing...", force=True)

    def close(self) -> None:
        self._closed = True


class _Phase:
    """A [lo, hi] slice of a `BacktestProgress` scale, addressed as 0.0-1.0."""

    __slots__ = ("_parent", "_lo", "_span", "_stage")

    def __init__(self, parent: BacktestProgress, lo: float, hi: float, stage: str):
        self._parent = parent
        self._lo = lo
        self._span = max(0.0, hi - lo)
        self._stage = stage

    def note(self, fraction: float, message: str | None = None) -> None:
        """Record completion of this phase as 0.0-1.0. Thread-safe, no I/O."""
        f = max(0.0, min(1.0, float(fraction)))
        self._parent.record(self._lo + f * self._span, self._stage, message)

    async def set(self, fraction: float, message: str | None = None) -> None:
        """Record and force an immediate emit — for phase start/end."""
        f = max(0.0, min(1.0, float(fraction)))
        await self._parent.set(self._lo + f * self._span, self._stage, message)
