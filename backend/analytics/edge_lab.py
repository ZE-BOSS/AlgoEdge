"""
backend/analytics/edge_lab.py

Published intraday VWAP and price-action setups, taken apart confluence by
confluence and tested on the same markets with the same parameters.

WHERE EACH FAMILY COMES FROM
----------------------------
  vwap_trend     Zarattini & Aziz (2023), "VWAP: The Holy Grail for Day Trading
                 Systems", SSRN 4631351. Long when price crosses above the
                 session VWAP, short below; out when it crosses back or at the close.
  noise_mom      Zarattini, Aziz & Barbon (2024), "Beat the Market: An Effective
                 Intraday Momentum Strategy for SPY", SSRN 4824172. A "noise area"
                 around the open sized by the average absolute move from the open
                 at that time of day over the last 14 sessions (gap-adjusted with
                 the prior close). Checked on the half hour; enter on a close
                 outside it; trail at max(boundary, VWAP); flat at the close.
  vwap_pullback  the classic institutional VWAP pullback (AlgoEdge's VWAP_v1 idea,
                 in the strategy-search form): with the VWAP slope, a bar dips to
                 VWAP and closes back on the trend side.
  last_half      Gao, Han, Li & Zhou (2018), "Market Intraday Momentum", JFE /
                 SSRN 2552752: the first half hour's return (from the prior close)
                 predicts the last half hour's. Enter 30 min before the close.
  orb_candle     Zarattini, Barbon & Aziz (2024), "A Profitable Day Trading
                 Strategy for the U.S. Equity Market", SSRN 4729284: trade in the
                 direction of the first 5-minute candle, stop 10% of the 14-day
                 ATR, target 10R or the close; relative volume does the selecting.
  orb_break      Crabel (1990), "Day Trading with Short Term Price Patterns and
                 Opening Range Breakout": close beyond the opening range; narrow-
                 range (NR7) and inside days as the setup filter.
  pdhl_sweep     the price-action "liquidity sweep" / Connors & Raschke's "Turtle
                 Soup" (1995): price trades through the prior session's high (low)
                 and closes back inside -> fade it.

HOW IT IS TESTED
----------------
Each family emits PERMISSIVE candidates on M5: every confluence is recorded as
a boolean, never enforced, and every exit is scored in R on the same candidate.
Any combination of confluences and exit is then a filter over one list:

  * one trade per session (the first candidate that passes the filter), so
    trades never overlap and the average cannot be inflated by overlap;
  * entry at the NEXT bar's open; the entry bar's spread charged; stops fill at
    the stop, or at the open when a bar gaps through it, plus half a spread of
    slippage; a bar touching both stop and target is a loss;
  * stops below 4x the spread are floored to it, and stops below 2% of the
    session ATR are discarded (a near-zero denominator is not a trade).

Selection is walk-forward and SHARED ACROSS MARKETS: one configuration (session,
confluences, exit) is chosen on 2024-01-22..2025-12-31 using all markets at
once, and reported unchanged on 2026-01-01..2026-09-12 and on the 2022-2023
holdout that selection never saw.
"""

from __future__ import annotations

import itertools
import math
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Iterator

import numpy as np
import pandas as pd
import pytz

from backend.analytics.strategy_search import Bars, atr, ema

TF = 300
SESSIONS = {
    "london": ("Europe/London", (8, 0), (16, 30)),
    "ny": ("America/New_York", (9, 30), (16, 0)),
}
_NATIVE = {"GBPJPY": "london", "EURJPY": "london", "GBPUSD": "london", "EURUSD": "london",
           "EURGBP": "london", "GBPCHF": "london", "GBPAUD": "london", "EURAUD": "london",
           "USDCHF": "london", "GERMANY 40": "london", "UK 100": "london"}

RR = (1.0, 1.5, 2.0, 3.0, 5.0, 10.0)
EXITS: tuple[str, ...] = tuple([f"1:{r:g}" for r in RR] + ["eod", "trail"])
STOP_SLIP_SPREADS = 0.5
MIN_STOP_SPREADS = 4.0
MIN_STOP_ATR_D = 0.02
MAX_COMBO = 3

T = lambda y, m, d: int(datetime(y, m, d, tzinfo=timezone.utc).timestamp())  # noqa: E731
WINDOWS: dict[str, tuple[int, int]] = {
    "holdout_2022_23": (T(2022, 1, 3), T(2024, 1, 22)),
    "select_2024_25": (T(2024, 1, 22), T(2026, 1, 1)),
    "last_8m_2026": (T(2026, 1, 1), T(2026, 9, 12)),
}
SELECT = "select_2024_25"


def native_session(symbol: str) -> str:
    return _NATIVE.get(symbol.upper(), "ny")


def session_for(symbol: str, which: str) -> str:
    nat = native_session(symbol)
    return nat if which == "native" else ("ny" if nat == "london" else "london")


# ── per-market context ───────────────────────────────────────────────────────

@dataclass
class Session:
    k: int
    open_ts: int
    close_ts: int
    r0: int              # first bar inside the session
    r1: int              # one past the last bar inside the session
    open_px: float
    prev_close: float
    hi: float
    lo: float
    vwap: np.ndarray     # per bar r0..r1-1, cumulative from the open
    n_end: int = 0       # bars the session has when complete (live: today's may still be forming)


@dataclass
class Ctx:
    b: Bars
    session: str
    sessions: list[Session]
    atr5: np.ndarray
    ema_htf: np.ndarray
    vol: np.ndarray
    vol_avg20: np.ndarray
    atr_d: np.ndarray        # per session: mean range of the prior 14 sessions
    atr_d_med: np.ndarray    # per session: median atr_d of the prior 60 sessions
    nr7: np.ndarray
    inside: np.ndarray
    prev_up: np.ndarray      # prior session closed above its open
    open_vol: dict[int, np.ndarray]    # minutes -> per-session relative opening volume
    sigma: list[np.ndarray]  # per session: noise sigma by bar offset (NaN when unknown)


def build_ctx(b: Bars, session: str, live: bool = False) -> Ctx:
    """Sessions, VWAPs and per-session context for one market.

    live=True keeps the LAST session even though it is still forming (a live
    engine only ever has today's bars so far) and gives it its full expected
    length, so "not in the last 30 minutes" is measured against the real close
    rather than against the bars that happen to exist yet.
    """
    tzname, (oh, om), (ch, cm) = SESSIONS[session]
    tz = pytz.timezone(tzname)
    n = len(b)
    vol = np.maximum(b.volume if b.volume is not None else np.ones(n), 1.0)
    tp = (b.high + b.low + b.close) / 3.0
    days = sorted({datetime.fromtimestamp(int(t), tz=timezone.utc).astimezone(tz).date()
                   for t in b.time[:: 12]} | {datetime.fromtimestamp(int(b.time[-1]), tz=timezone.utc)
                                              .astimezone(tz).date()})
    sessions: list[Session] = []
    prev_close = float("nan")
    full = int(((ch * 60 + cm) - (oh * 60 + om)) * 60 // TF)
    for day in days:
        if day.weekday() >= 5:
            continue
        o = int(tz.localize(datetime(day.year, day.month, day.day, oh, om)).timestamp())
        c = int(tz.localize(datetime(day.year, day.month, day.day, ch, cm)).timestamp())
        r0 = int(np.searchsorted(b.time, o, side="left"))
        r1 = int(np.searchsorted(b.time, c, side="left"))
        if r0 >= n or int(b.time[r0]) - o >= 600:
            continue
        forming = live and r1 >= n and int(b.time[-1]) < c
        if (r1 - r0) < 0.8 * full and not forming:
            continue
        seg = slice(r0, r1)
        vw = np.cumsum(tp[seg] * vol[seg]) / np.cumsum(vol[seg])
        sessions.append(Session(len(sessions), o, c, r0, r1, float(b.open[r0]), prev_close,
                                float(b.high[seg].max()), float(b.low[seg].min()), vw,
                                full if forming else r1 - r0))
        prev_close = float(b.close[r1 - 1])

    S = len(sessions)
    rng = np.array([s.hi - s.lo for s in sessions])
    atr_d = np.full(S, np.nan)
    atr_d_med = np.full(S, np.nan)
    nr7 = np.zeros(S, bool)
    inside = np.zeros(S, bool)
    prev_up = np.zeros(S, bool)
    for k in range(S):
        if k >= 14:
            atr_d[k] = rng[k - 14:k].mean()
        if k >= 34:
            atr_d_med[k] = np.nanmedian(atr_d[max(14, k - 60):k])
        if k >= 7:
            nr7[k] = rng[k - 1] <= rng[k - 7:k].min()
        if k >= 2:
            inside[k] = sessions[k - 1].hi <= sessions[k - 2].hi and sessions[k - 1].lo >= sessions[k - 2].lo
        if k >= 1:
            p = sessions[k - 1]
            prev_up[k] = float(b.close[p.r1 - 1]) > p.open_px

    open_vol: dict[int, np.ndarray] = {}
    for mins in (5, 15, 30, 60):
        m = max(1, mins * 60 // TF)
        raw = np.array([vol[s.r0:s.r0 + m].sum() for s in sessions])
        rel = np.full(S, np.nan)
        for k in range(14, S):
            base = raw[k - 14:k].mean()
            rel[k] = raw[k] / base if base > 0 else np.nan
        open_vol[mins] = rel

    # noise area: mean |close/open - 1| at the same bar offset over the prior 14 sessions
    L = max((s.r1 - s.r0) for s in sessions) if sessions else 0
    move = np.full((S, L), np.nan)
    for s in sessions:
        seg = b.close[s.r0:s.r1]
        move[s.k, :len(seg)] = np.abs(seg / s.open_px - 1.0)
    sigma: list[np.ndarray] = []
    with np.errstate(all="ignore"):
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", category=RuntimeWarning)
            for s in sessions:
                if s.k < 14:
                    sigma.append(np.full(s.r1 - s.r0, np.nan))
                else:
                    sigma.append(np.nanmean(move[s.k - 14:s.k, :s.r1 - s.r0], axis=0))

    a5 = atr(b, 14)
    return Ctx(b, session, sessions, a5, ema(b.close, 600), vol,
               pd.Series(vol).rolling(20).mean().shift(1).to_numpy(),
               atr_d, atr_d_med, nr7, inside, prev_up, open_vol, sigma)


# ── outcomes ────────────────────────────────────────────────────────────────

def score_exits(ctx: Ctx, i: int, d: int, stop_dist: float, end: int,
                trail: np.ndarray | None) -> tuple[np.ndarray, np.ndarray] | None:
    """R and exit time for every exit in EXITS, entering at bar i+1's open.

    `trail[j]` (aligned to bars i+1..end) is a close-based exit level: the trade
    is closed at a bar's close that finishes on the wrong side of it. NaN = no
    check on that bar. Without a trail the "trail" exit equals "eod".
    """
    b = ctx.b
    e = i + 1
    if e > end:
        return None
    entry = float(b.open[e])
    hi, lo, op, cl = b.high[e:end + 1], b.low[e:end + 1], b.open[e:end + 1], b.close[e:end + 1]
    L = len(cl)
    stop = entry - d * stop_dist
    s_hit = (lo <= stop) if d > 0 else (hi >= stop)
    s_idx = int(np.argmax(s_hit)) if s_hit.any() else L
    cost = float(b.spread[e]) / stop_dist

    def at_stop(j: int) -> float:
        px = stop
        if j > 0 and ((d > 0 and op[j] < stop) or (d < 0 and op[j] > stop)):
            px = float(op[j])
        return d * (px - entry) / stop_dist - cost - STOP_SLIP_SPREADS * float(b.spread[e + j]) / stop_dist

    def at(px: float) -> float:
        return d * (px - entry) / stop_dist - cost

    r = np.empty(len(EXITS))
    tx = np.empty(len(EXITS), dtype=np.int64)
    for k, rr in enumerate(RR):
        target = entry + d * rr * stop_dist
        t_hit = (hi >= target) if d > 0 else (lo <= target)
        t_idx = int(np.argmax(t_hit)) if t_hit.any() else L
        if s_idx < L and s_idx <= t_idx:
            r[k], tx[k] = at_stop(s_idx), b.time[e + s_idx]
        elif t_idx < L:
            r[k], tx[k] = at(target), b.time[e + t_idx]
        else:
            r[k], tx[k] = at(float(cl[-1])), b.time[end]
    k_eod = len(RR)
    if s_idx < L:
        r[k_eod], tx[k_eod] = at_stop(s_idx), b.time[e + s_idx]
    else:
        r[k_eod], tx[k_eod] = at(float(cl[-1])), b.time[end]
    k_tr = k_eod + 1
    r[k_tr], tx[k_tr] = r[k_eod], tx[k_eod]
    if trail is not None:
        lim = min(s_idx, L - 1)
        for j in range(0, lim + 1):
            if j == s_idx:
                break
            lv = trail[j]
            if np.isfinite(lv) and d * (cl[j] - lv) < 0:
                r[k_tr], tx[k_tr] = at(float(cl[j])), b.time[e + j]
                break
    return r, tx


# ── candidates ──────────────────────────────────────────────────────────────

@dataclass
class CandSet:
    symbol: str
    family: str
    axis: str
    features: tuple[str, ...]
    sess: np.ndarray
    t_entry: np.ndarray
    direction: np.ndarray
    stop_dist: np.ndarray
    feats: np.ndarray        # (n, F) bool
    r: np.ndarray            # (n, E)
    tx: np.ndarray           # (n, E)

    def __len__(self) -> int:
        return len(self.sess)


class _Collector:
    def __init__(self, ctx: Ctx, family: str, axis: str, features: tuple[str, ...]):
        self.ctx, self.family, self.axis, self.features = ctx, family, axis, features
        self.rows: list[tuple] = []

    def add(self, k: int, i: int, d: int, stop_dist: float, end: int, feats: dict[str, bool],
            trail: np.ndarray | None = None) -> None:
        ctx, b = self.ctx, self.ctx.b
        if i + 1 > end:
            return
        stop_dist = accept_stop(ctx, k, i, stop_dist)
        if stop_dist is None:
            return
        out = score_exits(ctx, i, d, stop_dist, end, trail)
        if out is None:
            return
        self.rows.append((k, int(b.time[i + 1]), d, stop_dist,
                          [bool(feats[f]) for f in self.features], out[0], out[1]))

    def done(self) -> CandSet:
        F, E = len(self.features), len(EXITS)
        if not self.rows:
            z = np.zeros(0)
            return CandSet(self.ctx.b.symbol, self.family, self.axis, self.features, z.astype(int),
                           z.astype(np.int64), z.astype(int), z, np.zeros((0, F), bool),
                           np.zeros((0, E)), np.zeros((0, E), np.int64))
        k, te, d, sd, f, r, tx = zip(*self.rows)
        return CandSet(self.ctx.b.symbol, self.family, self.axis, self.features,
                       np.array(k), np.array(te, np.int64), np.array(d), np.array(sd),
                       np.array(f, bool).reshape(-1, F), np.vstack(r), np.vstack(tx))


def accept_stop(ctx: Ctx, k: int, i: int, stop_dist: float) -> float | None:
    """The stop a candidate is scored (and traded) with, or None to discard it:
    floored at 4x the bar's spread, discarded below 2% of the session ATR."""
    if not (np.isfinite(stop_dist) and stop_dist > 0):
        return None
    stop_dist = max(stop_dist, MIN_STOP_SPREADS * float(ctx.b.spread[i]))
    ad = ctx.atr_d[k]
    if np.isfinite(ad) and stop_dist < MIN_STOP_ATR_D * ad:
        return None
    return float(stop_dist)


def _f(x) -> bool:
    return bool(x) if not (isinstance(x, float) and math.isnan(x)) else False


def _common(ctx: Ctx, s: Session, i: int, d: int) -> dict[str, bool]:
    b = ctx.b
    j = i - s.r0
    c, o, h, lo = float(b.close[i]), float(b.open[i]), float(b.high[i]), float(b.low[i])
    vw = s.vwap[j]
    eh = ctx.ema_htf
    sig = ctx.sigma[s.k][j] if j < len(ctx.sigma[s.k]) else np.nan
    up_b = max(s.open_px, s.prev_close) * (1 + sig) if np.isfinite(s.prev_close) else s.open_px * (1 + sig)
    dn_b = min(s.open_px, s.prev_close) * (1 - sig) if np.isfinite(s.prev_close) else s.open_px * (1 - sig)
    return {
        "htf_trend": _f(i >= 12 and d * (c - eh[i]) > 0 and d * (eh[i] - eh[i - 12]) > 0),
        "vwap_side": _f(d * (c - vw) > 0),
        "vwap_slope": _f(j >= 6 and d * (vw - s.vwap[j - 6]) > 0),
        "day_dir": _f(d * (c - s.open_px) > 0),
        "gap_dir": _f(np.isfinite(s.prev_close) and d * (s.open_px - s.prev_close) > 0),
        "prev_day_dir": _f(s.k >= 1 and (ctx.prev_up[s.k] if d > 0 else not ctx.prev_up[s.k])),
        "vol_surge": _f(np.isfinite(ctx.vol_avg20[i]) and ctx.vol[i] >= 1.5 * ctx.vol_avg20[i]),
        "rel_vol_open": _f(np.isfinite(ctx.open_vol[30][s.k]) and ctx.open_vol[30][s.k] >= 1.0),
        "high_vol": _f(np.isfinite(ctx.atr_d_med[s.k]) and ctx.atr_d[s.k] > ctx.atr_d_med[s.k]),
        "nr7": _f(ctx.nr7[s.k]),
        "inside_day": _f(ctx.inside[s.k]),
        "early": _f((int(b.time[i]) - s.open_ts) < 120 * 60),
        "strong_body": _f(h > lo and abs(c - o) >= 0.6 * (h - lo)),
        "noise_out": _f(np.isfinite(sig) and ((d > 0 and c > up_b) or (d < 0 and c < dn_b))),
    }


def _bounds(ctx: Ctx, s: Session, j: int) -> tuple[float, float]:
    sig = ctx.sigma[s.k][j]
    pc = s.prev_close if np.isfinite(s.prev_close) else s.open_px
    return max(s.open_px, pc) * (1 + sig), min(s.open_px, pc) * (1 - sig)


# Each family: (features, builder(ctx, axis) -> CandSet, axes)

VWAP_TREND_F = ("htf_trend", "vwap_slope", "day_dir", "gap_dir", "vol_surge", "rel_vol_open",
                "high_vol", "early", "strong_body", "noise_out")


def gen_vwap_trend(ctx: Ctx, s: Session) -> Iterator[tuple[int, int, float, dict, np.ndarray]]:
    """Every VWAP cross in one session: (bar, direction, stop, features, trail)."""
    b = ctx.b
    end, last = s.r1 - 1, -99
    for j in range(3, min(s.r1 - s.r0, s.n_end - 6)):
        i = s.r0 + j
        c, cp, vw, vwp = b.close[i], b.close[i - 1], s.vwap[j], s.vwap[j - 1]
        d = 1 if (c > vw and cp <= vwp) else (-1 if (c < vw and cp >= vwp) else 0)
        if d == 0 or i - last < 3 or not np.isfinite(ctx.atr5[i]):
            continue
        last = i
        yield i, d, abs(c - vw) + float(ctx.atr5[i]), _common(ctx, s, i, d), s.vwap[j + 1:end - s.r0 + 1]


def fam_vwap_trend(ctx: Ctx, axis: str) -> CandSet:
    col = _Collector(ctx, "vwap_trend", axis, VWAP_TREND_F)
    for s in ctx.sessions[20:]:
        for i, d, stop, feats, trail in gen_vwap_trend(ctx, s):
            col.add(s.k, i, d, stop, s.r1 - 1, feats, trail)
    return col.done()


NOISE_F = ("htf_trend", "vwap_slope", "gap_dir", "prev_day_dir", "vol_surge", "rel_vol_open",
           "high_vol", "nr7", "early", "strong_body")


def fam_noise_mom(ctx: Ctx, axis: str) -> CandSet:
    col = _Collector(ctx, "noise_mom", axis, NOISE_F)
    b = ctx.b
    for s in ctx.sessions[20:]:
        end, n_in, last = s.r1 - 1, s.r1 - s.r0, -99
        half = np.array([((int(b.time[s.r0 + j]) + TF - s.open_ts) % 1800 == 0) for j in range(n_in)])
        for j in range(5, n_in - 6):
            if not half[j]:
                continue
            i = s.r0 + j
            if not np.isfinite(ctx.sigma[s.k][j]) or not np.isfinite(ctx.atr5[i]) or i - last < 6:
                continue
            ub, lb = _bounds(ctx, s, j)
            c = float(b.close[i])
            d = 1 if c > ub else (-1 if c < lb else 0)
            if d == 0:
                continue
            last = i
            level = max(ub, s.vwap[j]) if d > 0 else min(lb, s.vwap[j])
            stop = max(d * (c - level), 0.5 * float(ctx.atr5[i]))
            trail = np.full(end - i, np.nan)
            for jj in range(j + 1, n_in):
                if half[jj] and np.isfinite(ctx.sigma[s.k][jj]):
                    u, l_ = _bounds(ctx, s, jj)
                    trail[jj - j - 1] = max(u, s.vwap[jj]) if d > 0 else min(l_, s.vwap[jj])
            col.add(s.k, i, d, stop, end, _common(ctx, s, i, d), trail)
    return col.done()


PULLBACK_F = ("htf_trend", "day_dir", "gap_dir", "vol_surge", "rel_vol_open", "high_vol",
              "early", "strong_body", "noise_out", "nr7")


def gen_vwap_pullback(ctx: Ctx, s: Session) -> Iterator[tuple[int, int, float, dict, np.ndarray]]:
    b = ctx.b
    end, last = s.r1 - 1, -99
    for j in range(4, min(s.r1 - s.r0, s.n_end - 6)):
        i = s.r0 + j
        a = float(ctx.atr5[i])
        if not np.isfinite(a) or a <= 0 or i - last < 3:
            continue
        vw, slope = s.vwap[j], s.vwap[j] - s.vwap[j - 4]
        c, lo, hi = float(b.close[i]), float(b.low[i]), float(b.high[i])
        if slope > 0 and lo <= vw < c:
            d, stop = 1, max(c - lo + 0.25 * a, 0.5 * a)
        elif slope < 0 and hi >= vw > c:
            d, stop = -1, max(hi - c + 0.25 * a, 0.5 * a)
        else:
            continue
        last = i
        yield i, d, stop, _common(ctx, s, i, d), s.vwap[j + 1:end - s.r0 + 1]


def fam_vwap_pullback(ctx: Ctx, axis: str) -> CandSet:
    col = _Collector(ctx, "vwap_pullback", axis, PULLBACK_F)
    for s in ctx.sessions[20:]:
        for i, d, stop, feats, trail in gen_vwap_pullback(ctx, s):
            col.add(s.k, i, d, stop, s.r1 - 1, feats, trail)
    return col.done()


LAST_HALF_F = ("high_vol", "rel_vol_open", "day_dir", "vwap_side", "htf_trend", "gap_dir",
               "nr7", "big_first", "prev_day_dir", "vwap_slope")


def fam_last_half(ctx: Ctx, axis: str) -> CandSet:
    col = _Collector(ctx, "last_half", axis, LAST_HALF_F)
    b = ctx.b
    first_moves: list[float] = []
    for s in ctx.sessions[1:]:
        if not np.isfinite(s.prev_close):
            continue
        i30 = int(np.searchsorted(b.time, s.open_ts + 1800 - TF, side="left"))
        ie = int(np.searchsorted(b.time, s.close_ts - 1800 - TF, side="left"))
        if not (s.r0 <= i30 < ie < s.r1) or int(b.time[ie]) != s.close_ts - 1800 - TF:
            continue
        ret = float(b.close[i30]) / s.prev_close - 1.0
        big = len(first_moves) >= 20 and abs(ret) > statistics.median(first_moves[-20:])
        first_moves.append(abs(ret))
        d = 1 if ret > 0 else (-1 if ret < 0 else 0)
        a = float(ctx.atr5[ie])
        if d == 0 or s.k < 20 or not np.isfinite(a):
            continue
        feats = _common(ctx, s, ie, d)
        feats["big_first"] = big
        col.add(s.k, ie, d, 2.0 * a, s.r1 - 1, feats, None)
    return col.done()


ORB_CANDLE_F = ("rel_vol_open", "rel_vol_high", "htf_trend", "gap_dir", "nr7", "high_vol",
                "strong_body", "inside_day", "prev_day_dir", "vol_surge")


def fam_orb_candle(ctx: Ctx, axis: str) -> CandSet:
    mins, stop_mode = int(axis.split("|")[1]), axis.split("|")[2]
    col = _Collector(ctx, "orb_candle", axis, ORB_CANDLE_F)
    b = ctx.b
    m = mins * 60 // TF
    for s in ctx.sessions[20:]:
        i = s.r0 + m - 1
        if i + 1 >= s.r1 or not np.isfinite(ctx.atr_d[s.k]):
            continue
        o, c = s.open_px, float(b.close[i])
        hi, lo = float(b.high[s.r0:i + 1].max()), float(b.low[s.r0:i + 1].min())
        d = 1 if c > o else (-1 if c < o else 0)
        if d == 0:
            continue
        stop = 0.10 * float(ctx.atr_d[s.k]) if stop_mode == "atr10" else (c - lo if d > 0 else hi - c)
        feats = _common(ctx, s, i, d)
        rv = ctx.open_vol[mins][s.k]
        feats["rel_vol_open"] = _f(np.isfinite(rv) and rv >= 1.0)
        feats["rel_vol_high"] = _f(np.isfinite(rv) and rv >= 1.5)
        feats["strong_body"] = _f(hi > lo and abs(c - o) >= 0.6 * (hi - lo))
        col.add(s.k, i, d, stop, s.r1 - 1, feats, None)
    return col.done()


ORB_BREAK_F = ("nr7", "inside_day", "rel_vol_open", "htf_trend", "gap_dir", "vwap_side",
               "vol_surge", "strong_body", "range_narrow", "high_vol")


def gen_orb_break(ctx: Ctx, s: Session, mins: int, window_bars: int = 36,
                  min_stop_atr: float = 0.5) -> Iterator[tuple[int, int, float, dict, None]]:
    """The session's FIRST M5 close beyond the opening range (at most one)."""
    b = ctx.b
    re_ = s.r0 + mins * 60 // TF
    if re_ >= s.r1 or not np.isfinite(ctx.atr_d[s.k]):
        return
    rh, rl = float(b.high[s.r0:re_].max()), float(b.low[s.r0:re_].min())
    w_end = min(s.r1, s.r0 + s.n_end - 6, re_ + window_bars)
    for i in range(re_, w_end):
        c = float(b.close[i])
        d = 1 if c > rh else (-1 if c < rl else 0)
        if d == 0:
            continue
        a = float(ctx.atr5[i]) if np.isfinite(ctx.atr5[i]) else 0.0
        stop = max(c - rl if d > 0 else rh - c, min_stop_atr * a)
        feats = _common(ctx, s, i, d)
        feats["rel_vol_open"] = _f(np.isfinite(ctx.open_vol[mins][s.k]) and ctx.open_vol[mins][s.k] >= 1.0)
        feats["range_narrow"] = _f((rh - rl) < 0.35 * float(ctx.atr_d[s.k]))
        feats["range_high"], feats["range_low"] = rh, rl
        yield i, d, stop, feats, None
        return


def fam_orb_break(ctx: Ctx, axis: str) -> CandSet:
    mins = int(axis.split("|")[1])
    col = _Collector(ctx, "orb_break", axis, ORB_BREAK_F)
    for s in ctx.sessions[20:]:
        for i, d, stop, feats, _ in gen_orb_break(ctx, s, mins):
            col.add(s.k, i, d, stop, s.r1 - 1, feats, None)
    return col.done()


SWEEP_F = ("vwap_side", "htf_trend", "wick_reject", "vol_surge", "early", "gap_dir",
           "high_vol", "day_dir", "nr7", "rel_vol_open")


def fam_pdhl_sweep(ctx: Ctx, axis: str) -> CandSet:
    col = _Collector(ctx, "pdhl_sweep", axis, SWEEP_F)
    b = ctx.b
    for s in ctx.sessions[20:]:
        p = ctx.sessions[s.k - 1]
        pdh, pdl = p.hi, p.lo
        done_hi = done_lo = False
        end = s.r1 - 1
        for j in range(1, s.r1 - s.r0 - 6):
            i = s.r0 + j
            h, lo, c, o = float(b.high[i]), float(b.low[i]), float(b.close[i]), float(b.open[i])
            a = float(ctx.atr5[i]) if np.isfinite(ctx.atr5[i]) else 0.0
            if not done_hi and h > pdh:
                done_hi = True
                if c < pdh:
                    feats = _common(ctx, s, i, -1)
                    feats["wick_reject"] = _f(h > lo and (h - max(o, c)) >= 0.5 * (h - lo))
                    col.add(s.k, i, -1, max(h - c + 0.1 * a, 0.5 * a), end, feats, None)
            if not done_lo and lo < pdl:
                done_lo = True
                if c > pdl:
                    feats = _common(ctx, s, i, 1)
                    feats["wick_reject"] = _f(h > lo and (min(o, c) - lo) >= 0.5 * (h - lo))
                    col.add(s.k, i, 1, max(c - lo + 0.1 * a, 0.5 * a), end, feats, None)
            if done_hi and done_lo:
                break
    return col.done()


# ── the live form ────────────────────────────────────────────────────────────

LIVE_FAMILIES = ("vwap_trend", "vwap_pullback", "orb_break")
# Confluences a live engine can evaluate from its own window. high_vol needs 34+
# prior sessions of history, more than any engine is handed, so it is not offered.
LIVE_GATES = frozenset({"htf_trend", "vwap_side", "vwap_slope", "day_dir", "gap_dir", "prev_day_dir",
                        "vol_surge", "rel_vol_open", "nr7", "inside_day", "early", "strong_body",
                        "noise_out", "range_narrow"})


def live_signal(family: str, ctx: Ctx, gates: tuple[str, ...], *, mins: int = 60,
                window_bars: int = 36, min_stop_atr: float = 0.5) -> dict | None:
    """What the research would trade on ctx's LAST bar, if anything.

    Walks the family's candidates in the session containing the last bar and
    applies the same rules pick() applies to the saved candidates: the stop
    floor/discard, then the gates, then one trade per session (the first
    candidate that passes). Only a pass ON the last bar is a signal.
    """
    b = ctx.b
    last = len(b) - 1
    s = next((x for x in reversed(ctx.sessions) if x.r0 <= last < x.r1), None)
    if s is None:
        return None
    if family == "vwap_trend":
        gen = gen_vwap_trend(ctx, s)
    elif family == "vwap_pullback":
        gen = gen_vwap_pullback(ctx, s)
    elif family == "orb_break":
        gen = gen_orb_break(ctx, s, mins, window_bars, min_stop_atr)
    else:
        raise ValueError(f"no live form for {family}")
    for i, d, stop, feats, _ in gen:
        stop = accept_stop(ctx, s.k, i, stop)
        if stop is None or not all(feats.get(g, False) for g in gates):
            continue
        if i != last:
            return None          # the session's trade was already taken earlier
        return {"direction": d, "stop_dist": stop, "features": feats, "session_close_ts": s.close_ts,
                "vwap": float(s.vwap[i - s.r0])}
    return None


@dataclass(frozen=True)
class Family:
    name: str
    source: str
    features: tuple[str, ...]
    build: Callable[[Ctx, str], CandSet]
    axes: tuple[str, ...]          # beyond the session choice


FAMILIES: tuple[Family, ...] = (
    Family("vwap_trend", "Zarattini & Aziz 2023, SSRN 4631351", VWAP_TREND_F, fam_vwap_trend, ("",)),
    Family("noise_mom", "Zarattini, Aziz & Barbon 2024, SSRN 4824172", NOISE_F, fam_noise_mom, ("",)),
    Family("vwap_pullback", "institutional VWAP pullback (VWAP_v1 thesis)", PULLBACK_F, fam_vwap_pullback, ("",)),
    Family("last_half", "Gao, Han, Li & Zhou 2018, SSRN 2552752", LAST_HALF_F, fam_last_half, ("",)),
    Family("orb_candle", "Zarattini, Barbon & Aziz 2024, SSRN 4729284", ORB_CANDLE_F, fam_orb_candle,
           tuple(f"|{m}|{sm}" for m in (5, 15, 30) for sm in ("atr10", "range"))),
    Family("orb_break", "Crabel 1990 (NR7 / inside day + opening range)", ORB_BREAK_F, fam_orb_break,
           tuple(f"|{m}" for m in (15, 30, 60))),
    Family("pdhl_sweep", "Connors & Raschke 1995 'Turtle Soup' / liquidity sweep", SWEEP_F, fam_pdhl_sweep, ("",)),
)
FAMILY_BY_NAME = {f.name: f for f in FAMILIES}


def build_family(ctxs: dict[str, Ctx], fam: Family) -> dict[str, CandSet]:
    """axis_key -> CandSet for one market; ctxs maps 'native'/'alt' -> Ctx."""
    out = {}
    for which, ctx in ctxs.items():
        for ax in fam.axes:
            key = f"{which}{ax}"
            out[key] = fam.build(ctx, key)
    return out


# ── selection ───────────────────────────────────────────────────────────────

def pick(cs: CandSet, window: tuple[int, int], use: tuple[int, ...], e: int, one_per_session: bool = True) -> np.ndarray:
    lo, hi = window
    m = (cs.t_entry >= lo) & (cs.t_entry < hi)
    for f in use:
        m &= cs.feats[:, f]
    idx = np.flatnonzero(m)
    if not one_per_session or len(idx) == 0:
        return idx
    _, first = np.unique(cs.sess[idx], return_index=True)
    return idx[first]


def rstats(r: np.ndarray) -> dict[str, Any]:
    n = int(len(r))
    if n == 0:
        return {"n": 0, "avg_r": None, "t": None, "pf": None, "win": None, "total_r": 0.0}
    gw, gl = float(r[r > 0].sum()), float(-r[r < 0].sum())
    sd = float(r.std(ddof=1)) if n > 2 else 0.0
    return {"n": n, "avg_r": round(float(r.mean()), 4),
            "t": round(float(r.mean() / (sd / math.sqrt(n))), 2) if sd > 0 else None,
            "pf": round(gw / gl, 3) if gl > 0 else None,
            "win": round(float((r > 0).mean()), 3), "total_r": round(float(r.sum()), 2)}


def combos(F: int) -> list[tuple[int, ...]]:
    return [c for k in range(MAX_COMBO + 1) for c in itertools.combinations(range(F), k)]


@dataclass
class ConfigResult:
    family: str
    axis: str
    use: tuple[str, ...]
    exit: str
    per_market: dict[str, dict[str, dict]] = field(default_factory=dict)   # window -> sym -> stats
    pooled: dict[str, dict] = field(default_factory=dict)


def evaluate(family: Family, data: dict[str, dict[str, CandSet]], axis: str, use: tuple[int, ...],
             e: int, windows: dict[str, tuple[int, int]]) -> ConfigResult:
    res = ConfigResult(family.name, axis, tuple(family.features[u] for u in use), EXITS[e])
    for w, span in windows.items():
        pooled = []
        res.per_market[w] = {}
        for sym, sets in data.items():
            cs = sets[axis]
            r = cs.r[pick(cs, span, use, e), e]
            res.per_market[w][sym] = rstats(r)
            if len(r):
                pooled.append(r)
        res.pooled[w] = rstats(np.concatenate(pooled) if pooled else np.zeros(0))
    return res


HOLDOUT = "holdout_2022_23"


def search(family: Family, data: dict[str, dict[str, CandSet]], *, min_n_market: int = 20,
           min_markets_positive: int | None = None,
           select_windows: tuple[str, ...] = (SELECT,)) -> dict[str, Any]:
    """Every axis x confluence subset (<= MAX_COMBO) x exit, scored on the
    selection window(s) only.

    With select_windows=(SELECT,) the pick is the highest pooled t-stat among
    configurations positive in all markets but one on 2024-25. With
    (HOLDOUT, SELECT) — the ROBUST mode — a configuration must ALSO be positive
    pooled and in all-but-one of the markets that have data on 2022-23, and it
    is ranked by the WORSE of its two pooled t-stats: a setting that only
    worked in one regime cannot win. 2026 is never used either way.

    The top 10 are returned too: if they fall apart out of sample, the pick was luck.
    """
    syms = list(data)
    need = min_markets_positive if min_markets_positive is not None else max(1, len(syms) - 1)
    axes = list(next(iter(data.values())).keys())
    rows = []
    for axis in axes:
        for use in combos(len(family.features)):
            # the pick depends on the exit only through validity, so reuse per exit
            idx = {w: {sym: pick(data[sym][axis], WINDOWS[w], use, 0) for sym in syms} for w in select_windows}
            for e in range(len(EXITS)):
                ok, ts, means, n_sel, pos_sel = True, [], [], 0, 0
                for w in select_windows:
                    parts, positive, have = [], 0, 0
                    for sym in syms:
                        r = data[sym][axis].r[idx[w][sym], e]
                        if w == SELECT and len(r) < min_n_market:
                            ok = False
                            break
                        if len(r) == 0:
                            continue        # no history in this window (e.g. indices before 2024)
                        if len(r) < min_n_market // 2:
                            ok = False
                            break
                        have += 1
                        positive += r.mean() > 0
                        parts.append(r)
                    if not ok:
                        break
                    if not parts:
                        continue
                    pr = np.concatenate(parts)
                    sd = pr.std(ddof=1)
                    ts.append(pr.mean() / (sd / math.sqrt(len(pr))) if sd > 0 else 0.0)
                    means.append(float(pr.mean()))
                    if w == SELECT:
                        n_sel, pos_sel = len(pr), positive
                    elif positive < max(1, have - 1):
                        means[-1] = -abs(means[-1]) - 1e-9    # fails the per-market rule
                if not ok or not ts:
                    continue
                rows.append((min(ts), pos_sel, axis, use, e, min(means), n_sel))
    tried = len(rows)
    eligible = [r for r in rows if r[1] >= need and r[5] > 0]
    eligible.sort(key=lambda r: -r[0])
    ranked_all = sorted(rows, key=lambda r: -r[0])
    top = (eligible or ranked_all)[:10]
    out: dict[str, Any] = {"family": family.name, "source": family.source, "configs_tried": tried,
                           "configs_eligible": len(eligible), "markets": syms}
    out["top10"] = [asdict_cfg(evaluate(family, data, a, u, e, WINDOWS)) for (_, _, a, u, e, _, _) in top]
    out["chosen"] = out["top10"][0] if top else None
    out["chosen_passed_rule"] = bool(eligible)
    return out


def asdict_cfg(c: ConfigResult) -> dict[str, Any]:
    return {"family": c.family, "axis": c.axis, "use": list(c.use), "exit": c.exit,
            "per_market": c.per_market, "pooled": c.pooled}


def ablation(family: Family, data: dict[str, dict[str, CandSet]], axis: str, e: int) -> list[dict]:
    """Each confluence alone vs no confluence, pooled, per window, plus how many
    markets it helped on the last 8 months."""
    rows = []
    for fi, name in enumerate(family.features):
        row = {"confluence": name}
        for w, span in WINDOWS.items():
            base, withf = [], []
            helped = counted = 0
            for sym, sets in data.items():
                cs = sets[axis]
                rb = cs.r[pick(cs, span, (), e), e]
                rw = cs.r[pick(cs, span, (fi,), e), e]
                if len(rb):
                    base.append(rb)
                if len(rw):
                    withf.append(rw)
                if len(rw) >= 10 and len(rb) >= 10:
                    counted += 1
                    helped += rw.mean() > rb.mean()
            sb = rstats(np.concatenate(base) if base else np.zeros(0))
            sw = rstats(np.concatenate(withf) if withf else np.zeros(0))
            row[w] = {"base": sb["avg_r"], "with": sw["avg_r"], "n_with": sw["n"],
                      "edge": round(sw["avg_r"] - sb["avg_r"], 4) if sb["avg_r"] is not None and sw["avg_r"] is not None else None,
                      "markets_helped": f"{helped}/{counted}"}
        rows.append(row)
    return rows


def legs_for(cs: CandSet, window: tuple[int, int], use: tuple[int, ...], e: int):
    from backend.analytics.money_sim import Leg
    idx = pick(cs, window, use, e)
    return [Leg(cs.symbol, int(cs.t_entry[k]), int(cs.tx[k, e]), float(cs.r[k, e]), float(cs.stop_dist[k]),
                group=f"{cs.symbol}|{cs.family}|{int(cs.t_entry[k])}") for k in idx]
