"""
backend/strategies/strategy_roboquant/replay.py

A frame for porting a STATEFUL roboquant.dev template line for line.

roboquant strategies are written as `on_bar(&mut self, ctx, bar)`: they keep
state across bars and across days (a range, "traded today", pivot levels, an
open position that blocks new entries). This app's engines must give the same
answer live and in a backtest from the same window of bars, so instead of
keeping that state between calls, `ReplayStrategy` REPLAYS the template from an
anchor:

    anchor = the first bar of the ET calendar day ANCHOR_DAYS distinct trading
             days before the window's last day.

The template's state at bar i is then a function of the bars from the anchor to
i only - the same in a backtest and live, after any restart - and consecutive
calls with the same anchor continue the replay instead of redoing it, so a
backtest costs about one template step per bar.

The template's `ctx.position()` is simulated (`Ctx`): an order sent on bar k
fills at bar k+1's open (roboquant's rule for bar strategies), SL/TP are
checked on every bar from the fill bar on (the stop first when one bar touches
both) and `ctx.close_position()` fills at the next bar's open. The simulation
exists so position-dependent rules ("no entry while a trade is open", "stop
after the first winner") behave as upstream; the app's real fills are the
backtester's / broker's.

Templates that run on a bar size this app has no feed for (3-minute bars) set
TEMPLATE_MINUTES: the engine is fed M1 and steps the template on clock-aligned
N-minute bars built from it; the order is emitted on the M1 bar that completes
the N-minute bar, so it fills at the next N-minute open exactly as upstream.

A subclass implements:
    init_state()               -> dict    fresh template state (store bar TIMES, never indices)
    prepare(bars)              -> None    vectorised indicators into bars.ind
    step(st, k, bars, ctx)     -> None    the template's on_bar for bar k
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from backend.strategies.base_strategy import TradeAction
from backend.strategies.strategy_roboquant import common as c
from backend.strategies.strategy_roboquant.engine import _RQBase

_MAX_EVENTS = 4000


@dataclass
class Bars:
    """One stretch of bars as arrays, plus the clocks templates read."""
    t: np.ndarray
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray  # noqa: E741
    c: np.ndarray
    v: np.ndarray
    et_day: np.ndarray
    et_hhmm: np.ndarray
    et_wd: np.ndarray            # ISO weekday of the ET date (Mon=1)
    utc_day: np.ndarray
    utc_hhmm: np.ndarray
    ind: dict[str, np.ndarray] = field(default_factory=dict)

    @classmethod
    def from_arrays(cls, t, o, h, l, cl, v) -> Bars:  # noqa: E741
        t = np.asarray(t, dtype="int64")
        et = pd.DatetimeIndex(pd.to_datetime(t, unit="s", utc=True)).tz_convert(c.ET)
        ut = pd.DatetimeIndex(pd.to_datetime(t, unit="s", utc=True))
        return cls(
            t=t, o=np.asarray(o, float), h=np.asarray(h, float), l=np.asarray(l, float),
            c=np.asarray(cl, float), v=np.asarray(v, float),
            et_day=np.asarray(et.year * 10000 + et.month * 100 + et.day, dtype="int64"),
            et_hhmm=np.asarray(et.hour * 100 + et.minute, dtype="int64"),
            et_wd=np.asarray(et.dayofweek + 1, dtype="int64"),
            utc_day=np.asarray(ut.year * 10000 + ut.month * 100 + ut.day, dtype="int64"),
            utc_hhmm=np.asarray(ut.hour * 100 + ut.minute, dtype="int64"),
        )

    @classmethod
    def of(cls, candles: pd.DataFrame, minutes: int = 1) -> Bars:
        t = c.epochs(candles)
        f = lambda k: candles[k].to_numpy(dtype=float)   # noqa: E731
        o, h, l, cl, v = f("open"), f("high"), f("low"), f("close"), c.volume(candles)  # noqa: E741
        if minutes > 1:
            t, o, h, l, cl, v = aggregate(t, o, h, l, cl, v, minutes)
        return cls.from_arrays(t, o, h, l, cl, v)

    def slice(self, a: int, b: int) -> Bars:
        out = Bars(*(getattr(self, k)[a:b] for k in
                     ("t", "o", "h", "l", "c", "v", "et_day", "et_hhmm", "et_wd", "utc_day", "utc_hhmm")))
        out.ind = {k: v[a:b] for k, v in self.ind.items()}
        return out

    def __len__(self) -> int:
        return len(self.t)


def aggregate(t, o, h, l, cl, v, minutes: int):  # noqa: E741
    """Clock-aligned N-minute bars from M1 bars; only COMPLETE buckets (one whose
    final minute is present, or that a later bucket follows)."""
    t = np.asarray(t, dtype="int64")
    if not len(t):
        return t, o, h, l, cl, v
    key = t // (60 * minutes)
    starts = np.r_[0, np.flatnonzero(np.diff(key)) + 1]
    ends = np.r_[starts[1:], len(t)]
    last_complete = (t[-1] // 60) % minutes == minutes - 1
    if not last_complete:
        starts, ends = starts[:-1], ends[:-1]
    if not len(starts):
        e = np.array([], dtype="int64")
        return e, e.astype(float), e.astype(float), e.astype(float), e.astype(float), e.astype(float)
    return (key[starts] * 60 * minutes, np.asarray(o)[starts],
            np.maximum.reduceat(np.asarray(h, float), starts), np.minimum.reduceat(np.asarray(l, float), starts),
            np.asarray(cl)[ends - 1], np.add.reduceat(np.asarray(v, float), starts))


def atr_fast(h, l, cl, n: int) -> np.ndarray:  # noqa: E741
    """Wilder ATR (RMA of true range), NaN for the first n bars."""
    h, l, cl = (np.asarray(x, dtype=float) for x in (h, l, cl))  # noqa: E741
    if not len(cl):
        return np.array([], dtype=float)
    prev = np.r_[cl[0], cl[:-1]]
    tr = np.maximum(h - l, np.maximum(np.abs(h - prev), np.abs(l - prev)))
    tr[0] = h[0] - l[0]
    # copy: under pandas 3 copy-on-write `to_numpy()` can hand back a read-only
    # view, and the in-place NaN fill below then raises on every call
    out = pd.Series(tr).ewm(alpha=1.0 / n, adjust=False).mean().to_numpy(copy=True)
    out[:n] = np.nan
    return out


def ema_fast(x, n: int) -> np.ndarray:
    return c.ema(x, n)


@dataclass
class SimPosition:
    side: int = 0                    # +1 long, -1 short, 0 flat
    entry: float = 0.0
    sl: float = 0.0                  # 0 = none
    tp: float = 0.0                  # 0 = none
    entry_t: int = 0                 # fill bar's open time
    trail_offset: float = 0.0        # Trail::offset(x).activate_after(y): 0 = off
    trail_activate: float = 0.0
    best: float = 0.0


class Ctx:
    """The slice of roboquant's `ctx` the ported templates use."""

    def __init__(self, strat: ReplayStrategy):
        self.s = strat
        self.pos = SimPosition()
        self.pending: dict | None = None
        self.pending_close = False
        self.k = -1
        self.bars: Bars | None = None
        self.orders: dict[int, dict] = {}     # bar time -> order sent on that bar
        self.closes: dict[int, str] = {}      # bar time -> reason the template closed on that bar
        self.closed: list[dict] = []          # closed sim trades, newest last

    # -- what templates call -------------------------------------------------
    def position(self) -> int:
        return self.pos.side

    def entry_price(self) -> float | None:
        return self.pos.entry if self.pos.side else None

    def time(self) -> int:
        return int(self.bars.t[self.k])

    def buy(self, sl: float = 0.0, tp: float = 0.0, **kw) -> None:
        self._order(1, sl, tp, kw)

    def sell(self, sl: float = 0.0, tp: float = 0.0, **kw) -> None:
        self._order(-1, sl, tp, kw)

    def close_position(self, reason: str = "RQ_STRATEGY_EXIT") -> None:
        if self.pos.side:
            self.pending_close = True
            self.closes[self.time()] = reason
            self._trim(self.closes)
        self.pending = None

    def cancel_all(self) -> None:
        self.pending = None

    def set_sl_tp(self, sl: float | None, tp: float | None) -> None:
        if self.pos.side:
            if sl is not None:
                self.pos.sl = float(sl)
            if tp is not None:
                self.pos.tp = float(tp)

    # -- the simulator -----------------------------------------------------------
    @staticmethod
    def _trim(d: dict) -> None:
        if len(d) > _MAX_EVENTS:
            for k in sorted(d)[: len(d) - _MAX_EVENTS]:
                del d[k]

    def _order(self, side, sl, tp, kw) -> None:
        trail_offset = float(kw.pop("trail_offset", 0.0) or 0.0)
        trail_activate = float(kw.pop("trail_activate", 0.0) or 0.0)
        o = dict(side=side, sl=float(sl or 0.0), tp=float(tp or 0.0), ref=float(self.bars.c[self.k]),
                 trail_offset=trail_offset, trail_activate=trail_activate, meta=kw)
        self.pending = o
        self.orders[self.time()] = o
        self._trim(self.orders)

    def _book(self, exit_px: float, why: str) -> None:
        p = self.pos
        self.closed.append(dict(side=p.side, entry=p.entry, exit=exit_px, pnl=(exit_px - p.entry) * p.side,
                                entry_t=p.entry_t, exit_t=self.time(), why=why))
        if len(self.closed) > 64:
            del self.closed[:-64]
        self.pos = SimPosition()
        self.s.on_sim_close(self, why)

    def open_bar(self, k: int) -> None:
        """Fills due at bar k's open, then SL / TP / trailing over bar k."""
        b = self.bars
        self.k = k
        op = float(b.o[k])
        if self.pending_close and self.pos.side:
            self._book(op, "close")
        self.pending_close = False
        if self.pending is not None:
            o, self.pending = self.pending, None
            if not self.pos.side:
                self.pos = SimPosition(side=o["side"], entry=op, sl=o["sl"], tp=o["tp"], entry_t=int(b.t[k]),
                                       trail_offset=o["trail_offset"], trail_activate=o["trail_activate"],
                                       best=op)
                self.s.on_sim_fill(self)
        p = self.pos
        if not p.side:
            return
        hi, lo = float(b.h[k]), float(b.l[k])
        if p.side > 0:
            if p.sl and lo <= p.sl:
                return self._book(min(op, p.sl), "sl")
            if p.tp and hi >= p.tp:
                return self._book(max(op, p.tp), "tp")
            p.best = max(p.best, hi)
        else:
            if p.sl and hi >= p.sl:
                return self._book(max(op, p.sl), "sl")
            if p.tp and lo <= p.tp:
                return self._book(min(op, p.tp), "tp")
            p.best = min(p.best, lo)
        if p.trail_offset > 0 and (p.best - p.entry) * p.side >= p.trail_activate:
            new = p.best - p.side * p.trail_offset
            if not p.sl or (new - p.sl) * p.side > 0:
                p.sl = new


class ReplayStrategy(_RQBase):
    ANCHOR_DAYS = 3
    """Replay starts at the first bar of the ET day this many trading days back."""
    TEMPLATE_MINUTES = 1
    """>1: the template runs on N-minute bars built from an M1 feed."""
    DISASTER_STOP_PCT = 2.0
    """Stop given to the app for a template order that has none (sizing + crash guard)."""
    FAR_TARGET_R = 10.0

    def __init__(self, config: Any):
        super().__init__(config)
        self._cache: dict[str, tuple] = {}
        self._full: dict[str, Bars] = {}

    @property
    def POSITION_BAR_WINDOW(self) -> int:  # noqa: N802 — read by both backtesters and live
        return self.WINDOW

    # -- subclass hooks --------------------------------------------------------
    def init_state(self) -> dict:
        return {}

    def prepare(self, bars: Bars) -> None:
        return None

    def step(self, st: dict, k: int, bars: Bars, ctx: Ctx) -> None:
        raise NotImplementedError

    def on_sim_fill(self, ctx: Ctx) -> None:
        return None

    def on_sim_close(self, ctx: Ctx, why: str) -> None:
        return None

    def forced_close(self, bars: Bars, k: int, position: dict) -> str | None:
        """A template's clock-based flatten (session end), applied to the app's
        position whatever the simulation holds - so it can never be skipped."""
        return None

    # -- bars ------------------------------------------------------------------------
    def prepare_backtest(self, symbol: str, frames_by_tf: dict) -> None:
        """Backtest only: the whole history once, so each call is a slice."""
        frame = (frames_by_tf or {}).get(self.params.timeframe)
        if frame is None or len(frame) < 60:
            return
        bars = Bars.of(frame, self.TEMPLATE_MINUTES)
        self.prepare(bars)
        self._full[str(symbol)] = bars

    def _window(self, symbol: str, candles: pd.DataFrame) -> Bars:
        full = self._full.get(str(symbol))
        if full is not None:
            t = c.epochs(candles)
            a = int(np.searchsorted(full.t, t[0], side="left"))
            # The last bar the window may contain: one that has CLOSED by the
            # window's last M1 bar's close.
            last_close = int(t[-1]) + 60 * (1 if self.TEMPLATE_MINUTES > 1 else 0)
            if self.TEMPLATE_MINUTES > 1:
                b = int(np.searchsorted(full.t, last_close - 60 * self.TEMPLATE_MINUTES, side="right"))
            else:
                b = int(np.searchsorted(full.t, t[-1], side="right"))
            if 0 <= a < b <= len(full.t) and (self.TEMPLATE_MINUTES > 1 or full.t[b - 1] == t[-1]):
                return full.slice(a, b)
        bars = Bars.of(candles, self.TEMPLATE_MINUTES)
        self.prepare(bars)
        return bars

    def _anchor(self, bars: Bars) -> int | None:
        if not len(bars):
            return None
        days = np.unique(bars.et_day)
        if len(days) <= self.ANCHOR_DAYS:
            return None
        return int(np.searchsorted(bars.et_day, days[-1 - self.ANCHOR_DAYS], side="left"))

    def _replay(self, symbol: str, candles: pd.DataFrame) -> tuple[Ctx, dict, Bars] | None:
        bars = self._window(symbol, candles)
        a = self._anchor(bars)
        if a is None:
            return None
        key = str(symbol)
        cached = self._cache.get(key)
        start = a
        st = ctx = None
        if cached and cached[0] == int(bars.t[a]):
            _, last_t, st, ctx = cached
            j = int(np.searchsorted(bars.t, last_t))
            if j < len(bars) and bars.t[j] == last_t:
                start = j + 1
            else:
                st = ctx = None
        if st is None:
            st, ctx, start = self.init_state(), Ctx(self), a
        ctx.bars = bars
        ctx.st = st
        for k in range(start, len(bars)):
            ctx.open_bar(k)
            self.step(st, k, bars, ctx)
        self._cache[key] = (int(bars.t[a]), int(bars.t[-1]), st, ctx)
        return ctx, st, bars

    def _last_template_bar(self, candles: pd.DataFrame) -> int | None:
        """Open time of the template bar that closed with the window's last bar
        (None when that M1 bar does not complete an N-minute bar)."""
        t = int(c.epochs(candles)[-1])
        n = self.TEMPLATE_MINUTES
        if n <= 1:
            return t
        if (t // 60) % n != n - 1:
            return None
        return (t // (60 * n)) * 60 * n

    # -- the app's two entry points ------------------------------------------------
    async def on_bar(self, symbol, timeframe, candles):
        if not self._start(symbol, timeframe, candles, 40):
            return None
        tb = self._last_template_bar(candles)
        if tb is None:
            return None
        r = self._replay(symbol, candles)
        if r is None:
            return None
        ctx, _, _ = r
        o = ctx.orders.get(tb)
        if not self.gate("template_entry", o is not None, "the template sends no order on this bar"):
            return None
        direction = "BUY" if o["side"] > 0 else "SELL"
        entry = o["ref"]
        sl, tp = self.protective_levels(direction, entry, o["sl"], o["tp"])
        if not self.gate("protective_levels", sl is not None, "stop/target on the wrong side of the entry"):
            return None
        return self._signal(symbol, timeframe, candles, direction, entry, sl, tp, **(o.get("meta") or {}))

    def protective_levels(self, direction: str, entry: float, sl: float, tp: float) -> tuple:
        """The app needs a stop and a target on every order. A template order
        without one gets DISASTER_STOP_PCT / FAR_TARGET_R; one with them keeps them."""
        s = 1 if direction == "BUY" else -1
        if not sl:
            sl = entry * (1 - s * self.DISASTER_STOP_PCT / 100.0)
        if (entry - sl) * s <= 0:
            return None, None
        if not tp:
            tp = entry + s * self.FAR_TARGET_R * abs(entry - sl)
        if (tp - entry) * s <= 0:
            return None, None
        return sl, tp

    def on_position_bar(self, symbol, timeframe, candles, position):
        # Both backtesters pass the POSITION's timeframe, which positions do not
        # carry (""): rejecting anything but our own timeframe silently dropped
        # every trail and strategy exit in a backtest (2026-10-20).
        if (timeframe and timeframe != self.params.timeframe) or candles is None or len(candles) < 40:
            return None
        tb = self._last_template_bar(candles)
        if tb is None:
            return None
        r = self._replay(symbol, candles)
        if r is None:
            return None
        ctx, _, bars = r
        why = ctx.closes.get(tb) or self.forced_close(bars, len(bars) - 1, position)
        long_pos = str(position.get("direction", "BUY")).upper() == "BUY"
        if why:
            return self._close(position, why)
        p = ctx.pos
        if p.side and p.trail_offset > 0 and p.sl and (p.side > 0) == long_pos:
            cur = float(position.get("stop_loss") or position.get("sl") or 0.0)
            if not cur or (p.sl - cur) * p.side > 0:
                return TradeAction(ticket=int(position.get("ticket") or 0), action="MODIFY_SL",
                                   new_sl=float(p.sl), close_reason="RQ_TRAIL")
        return None


def day_key(bars: Bars, boundary: str = "et") -> np.ndarray:
    """Per-bar key of the trading day a bar belongs to: the ET calendar day
    ("et"), the UTC day ("utc") or the CME session rolling at 18:00 ET ("cme")."""
    if boundary == "utc":
        return bars.utc_day
    if boundary == "cme":
        shifted = pd.DatetimeIndex(pd.to_datetime(bars.t + 6 * 3600, unit="s", utc=True)).tz_convert(c.ET)
        return np.asarray(shifted.year * 10000 + shifted.month * 100 + shifted.day, dtype="int64")
    return bars.et_day


def daily_asof(d1: pd.DataFrame | None, t: np.ndarray, value: str, n: int = 14) -> np.ndarray:
    """For each bar open time in `t`, a value of the last D1 bar that had CLOSED
    by then (roboquant's `.timeframe("1D")` reads completed daily bars only):
    "atr" (Wilder, n), "high" or "low". NaN where none is known."""
    out = np.full(len(t), np.nan)
    if d1 is None or not len(d1):
        return out
    dt = c.epochs(d1)
    if value == "atr":
        series = atr_fast(d1["high"].to_numpy(float), d1["low"].to_numpy(float), d1["close"].to_numpy(float), n)
    else:
        series = d1[value].to_numpy(float)
    closes = dt + 86400
    j = np.searchsorted(closes, np.asarray(t, dtype="int64"), side="right") - 1
    # The bar feed hands an engine a timeframe only once MIN_WINDOW_BARS of it
    # exist, so live never sees fewer; a backtest must not either.
    from backend.strategies.bar_feed import MIN_WINDOW_BARS
    ok = j >= MIN_WINDOW_BARS - 1
    out[ok] = series[j[ok]]
    return out
