"""
backend/strategies/bar_feed.py

The ONE way a strategy engine is handed bars — used by the single-symbol and
portfolio backtest routes, scripts/run_app_backtest.py and the live scan loop.

At primary bar i the engine sees the primary bars up to i-1 (bar i is still
forming) and, for every higher timeframe, the bars that had fully closed by
bar i's open. Each timeframe is handed to the engine only when it has produced
a NEW closed bar, so every closed bar reaches the engine exactly once, in time
order.

Why it exists (2026-09-15). The backtest routes did exactly that, but the live
loop called `on_bar` for EVERY timeframe on EVERY 60-second scan, re-feeding
bars the engine had already processed, and silently stepped over any bar that
closed while a scan ran long or the bot was offline. Engines that keep state
advanced differently live than in a backtest on the same data:

  * HTFFVGFlip / BiasIFVG / APA count bars to expire setups — ~5x too fast live
    on M5 (five scans per bar);
  * DriftJumpAlpha / BoomDriftJump track bars since the last jump and a gap
    history that re-appended the same bar on every scan;
  * VWAP counted a re-emitted signal against its daily cap on every scan.

`LiveBarFeed` gives live the backtest's exact call sequence: it steps every
primary bar it has not stepped yet (catching up missed bars in order), primes a
freshly started engine over the same warm-up span a backtest uses, and only the
newest bar's signal is actionable — a signal on a bar the bot was too late for
is reported as missed, never traded late.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from backend.strategies.windows import WARMUP_BASE_DAYS, warmup_days, window_bars

TF_MINUTES = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440}
TF_DELTA = {tf: np.timedelta64(m, "m") for tf, m in TF_MINUTES.items()}

# The backtest loops start at primary bar 300 of their fetched history.
FIRST_STEP_INDEX = 300
MIN_WINDOW_BARS = 20


def primary_timeframe(timeframes: list[str]) -> str:
    """The fastest timeframe — the clock both backtest routes drive."""
    return sorted(timeframes, key=lambda t: TF_MINUTES.get(t, 999))[0]


class BarFeed:
    """Hands an engine one primary bar at a time, exactly as a backtest does."""

    def __init__(self, engine: Any, symbol: str, timeframes: list[str] | None = None):
        self.engine = engine
        self.symbol = symbol
        self.timeframes = list(timeframes or engine.get_required_timeframes())
        self.primary = primary_timeframe(self.timeframes)
        self.prev_time_by_tf: dict[str, Any] = {tf: None for tf in self.timeframes}
        self.frames: dict[str, pd.DataFrame] = {}
        self.times: dict[str, np.ndarray] = {}

    def bind(self, frames_by_tf: dict[str, pd.DataFrame]) -> None:
        """Frames indexed by bar-open time, sorted ascending, one per timeframe."""
        self.frames = frames_by_tf
        self.times = {tf: frames_by_tf[tf].index.values for tf in self.timeframes}

    async def step(self, i: int):
        """Feed everything that is new as of primary bar i's open; return the
        engine's signal, if any timeframe produced one."""
        current_time = self.times[self.primary][i]
        sig = None
        for tf in self.timeframes:
            tf_times = self.times[tf]
            if tf == self.primary:
                tf_end, last_tf_time = i, current_time
            else:
                cutoff = current_time - TF_DELTA.get(tf, TF_DELTA["M5"])
                tf_end = int(np.searchsorted(tf_times, cutoff, side="right"))
                last_tf_time = tf_times[tf_end - 1] if tf_end > 0 else None
            if last_tf_time is None or last_tf_time == self.prev_time_by_tf[tf]:
                continue
            window = self.frames[tf].iloc[max(0, tf_end - window_bars(tf, self.engine)):tf_end]
            if len(window) < MIN_WINDOW_BARS:
                continue
            s = await self.engine.on_bar(self.symbol, tf, window)
            if s:
                sig = s
            self.prev_time_by_tf[tf] = last_tf_time
        return sig


@dataclass
class FeedResult:
    signal: Any = None                 # the newest bar's signal — the only actionable one
    stepped: int = 0                   # primary bars handed to the engine this call
    primed: bool = False               # this call warmed a fresh engine up
    missed: list[tuple[Any, Any]] = field(default_factory=list)  # (bar time, signal) on bars stepped late
    history_gap: bool = False          # fetched history no longer reaches the last stepped bar


FETCH_MARGIN_BARS = 20
MAX_FETCH_BARS = 10_000


class LiveBarFeed(BarFeed):
    """The live half: called once per scan with freshly fetched frames whose
    last primary row is the bar still forming."""

    def __init__(self, engine: Any, symbol: str, timeframes: list[str] | None = None):
        super().__init__(engine, symbol, timeframes)
        self.last_time = None  # primary bar-open time of the newest step

    def fetch_count(self, tf: str, now_s: float | None = None) -> int:
        """Bars of `tf` the next `advance` needs — not a fixed 5,000. Building
        5,000-bar frames for every timeframe of every slot on every scan stalled
        the server's event loop ~0.4 s at a time (measured 2026-09-15).

        A fresh feed needs the warm-up span plus a full window before its first
        bar; a running one needs a full window before the oldest bar it has not
        stepped, the bars since then, and the one still forming."""
        tf = str(tf).upper()
        tf_s = TF_MINUTES.get(tf, 5) * 60
        win = window_bars(tf, self.engine)
        if self.last_time is None:
            days = warmup_days(self.primary, self.engine, WARMUP_BASE_DAYS.get(self.primary, 5))
            need = int(days * 86400 // tf_s) + win
        else:
            now_s = time.time() if now_s is None else now_s
            last_s = int(pd.Timestamp(self.last_time).timestamp())
            need = win + max(0, int((now_s - last_s) // tf_s)) + 2
        need += FETCH_MARGIN_BARS
        need = -(-need // 250) * 250  # round up so the fetch cache keys stay stable
        return int(min(need, MAX_FETCH_BARS))

    async def advance(self, frames_by_tf: dict[str, pd.DataFrame], *, prime_days: float | None = None,
                      yield_after_s: float = 0.02) -> FeedResult:
        self.bind(frames_by_tf)
        times = self.times[self.primary]
        n = len(times)
        out = FeedResult()
        if n == 0:
            return out
        last = n - 1

        if self.last_time is None:
            days = prime_days if prime_days is not None else warmup_days(
                self.primary, self.engine, WARMUP_BASE_DAYS.get(self.primary, 5))
            first = int(np.searchsorted(times, times[last] - np.timedelta64(int(days * 86400), "s"), side="left"))
            start = max(min(FIRST_STEP_INDEX, last), first)
            out.primed = True
        else:
            k = int(np.searchsorted(times, self.last_time, side="left"))
            if k >= n or times[k] != self.last_time:
                # Offline longer than the fetched history: the engine's state no
                # longer connects to these bars. The caller rebuilds engine + feed.
                out.history_gap = True
                return out
            start = k + 1
        if start > last:
            return out

        # Warm-up bars are history: keep the engine's INFO chatter out of the live
        # activity feed while they replay, exactly as a backtest does.
        was_bt = getattr(self.engine, "is_backtesting", False)
        if out.primed:
            self.engine.is_backtesting = True
        # Warm-up replays hundreds of bars inside the server's event loop, which
        # also serves the API and WebSockets. Hand control back every
        # `yield_after_s` of CPU so a slow strategy cannot freeze the UI.
        last_yield = time.monotonic()
        try:
            for j in range(start, last + 1):
                if out.primed and j == last:
                    self.engine.is_backtesting = was_bt
                s = await self.step(j)
                out.stepped += 1
                if j == last:
                    out.signal = s
                elif s is not None and not out.primed:
                    out.missed.append((times[j], s))
                if yield_after_s and time.monotonic() - last_yield > yield_after_s:
                    await asyncio.sleep(0)
                    last_yield = time.monotonic()
        finally:
            self.engine.is_backtesting = was_bt
        self.last_time = times[last]
        return out
