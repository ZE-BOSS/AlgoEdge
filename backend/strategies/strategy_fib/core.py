"""
backend/strategies/strategy_fib/core.py

The FibTrend_v1 rule as pure functions of M15 bars, shared by the engine
(backtest and live) and the research re-measurement (research/fib/).

WHY EVERYTHING IS BUILT FROM "THE LAST N COMPLETED H4 BARS"
-----------------------------------------------------------
The rule lives on H4 (swings, fib, trend SMA, ATR) but executes on M15, so the
engine rebuilds H4 from its M15 window. A window is "the last W M15 bars", and
where it starts moves every bar. If the H4 series began wherever the window
began, the Wilder ATR seed and the swing sequence would shift with it, and the
same bar could compute differently in a backtest (stepped bar by bar) and live
(a fresh engine primed from wherever its fetch started). Taking exactly the last
N COMPLETED buckets - never the first one, which the window may cut - makes every
value a function of the data and the clock alone. That is what lets the engine
cache per H4 bar and still match live exactly.
"""

from __future__ import annotations

import numpy as np

H4 = 14_400
M15 = 900


def h4_bars(t: np.ndarray, o: np.ndarray, h: np.ndarray, l: np.ndarray, c: np.ndarray,
            last_close: int, n: int) -> dict | None:
    """The last `n` H4 buckets that are complete by `last_close` (epoch s), built
    from intraday bars with open times `t`. None if the window holds fewer."""
    if len(t) == 0:
        return None
    b = t // H4 * H4
    starts = np.r_[0, np.flatnonzero(np.diff(b)) + 1]
    ends = np.r_[starts[1:], len(b)]
    bt = b[starts]
    ok = bt + H4 <= last_close
    ok[0] = False                                   # the window may have cut the first bucket
    sel = np.flatnonzero(ok)
    if len(sel) < n:
        return None
    sel = sel[-n:]
    return dict(t=bt[sel], o=o[starts[sel]], c=c[ends[sel] - 1],
                h=np.maximum.reduceat(h, starts)[sel], l=np.minimum.reduceat(l, starts)[sel])


def atr_wilder(h: np.ndarray, l: np.ndarray, c: np.ndarray, n: int = 14) -> np.ndarray:
    """Wilder ATR seeded with the running mean of the first `n` true ranges."""
    tr = np.r_[h[0] - l[0], np.maximum(h[1:], c[:-1]) - np.minimum(l[1:], c[:-1])]
    out = np.empty_like(tr)
    m = min(n, len(tr))
    out[:m] = np.cumsum(tr[:m]) / np.arange(1, m + 1)
    for i in range(m, len(tr)):
        out[i] = out[i - 1] + (tr[i] - out[i - 1]) / n
    return out


def pivots(h: np.ndarray, l: np.ndarray, k: int) -> list[tuple[int, int, float]]:
    """Fractal swings [(index, +1 high / -1 low, price)], each CONFIRMED - it has
    `k` bars on both sides inside the series."""
    out = []
    for i in range(k, len(h) - k):
        if h[i] > h[i - k:i].max() and h[i] >= h[i + 1:i + k + 1].max():
            out.append((i, 1, float(h[i])))
        if l[i] < l[i - k:i].min() and l[i] <= l[i + 1:i + k + 1].min():
            out.append((i, -1, float(l[i])))
    return out


def setups(H: dict, p) -> list[dict]:
    """Every trend-aligned leg whose order could still be live after the last
    H4 bar in `H`: its fib levels, when it was confirmed and when it expires.

    A leg is two consecutive alternating swings; it is known only when its END
    swing is confirmed (`swing_bars` H4 bars later). Same rule as
    research/fib/fib_backtest.legs + admit + the missed-entry check.
    """
    hh, ll, cc, tt = H["h"], H["l"], H["c"], H["t"]
    n = len(tt)
    k = int(p.swing_bars)
    atr = atr_wilder(hh, ll, cc, int(p.atr_bars))
    sma = None
    if p.trend_filter:
        w = int(p.trend_sma_bars)
        cs = np.r_[0.0, np.cumsum(cc)]
        sma = np.full(n, np.nan)
        if n >= w:
            sma[w - 1:] = (cs[w:] - cs[:-w]) / w
    expiry = int(p.order_expiry_bars)
    first_conf = n - 1 - expiry                     # older legs have expired by now
    seq: list[tuple[int, int, float]] = []
    out = []
    for (i, typ, px) in pivots(hh, ll, k):
        if seq and seq[-1][1] == typ:
            if (typ == 1 and px > seq[-1][2]) or (typ == -1 and px < seq[-1][2]):
                seq[-1] = (i, typ, px)              # the same swing extended
            else:
                continue
        else:
            seq.append((i, typ, px))
        if len(seq) < 2:
            continue
        (a, _, pa), (b, tb, pb) = seq[-2], seq[-1]
        conf = b + k
        if conf < first_conf:
            continue
        d = 1 if tb == 1 else -1
        rng = abs(pb - pa)
        if rng < float(p.min_leg_atr) * atr[b]:
            continue
        if p.side == "long" and d < 0 or p.side == "short" and d > 0:
            continue
        if sma is not None:
            sl_ = int(p.trend_slope_bars)
            if conf - sl_ < 0 or np.isnan(sma[conf]) or np.isnan(sma[conf - sl_]):
                continue
            if not ((cc[conf] - sma[conf]) * d > 0 and (sma[conf] - sma[conf - sl_]) * d > 0):
                continue

        def lvl(f, pb=pb, d=d, rng=rng):
            return pb - d * f * rng
        entry, stop, end0 = lvl(float(p.entry_level)), lvl(float(p.stop_level)), lvl(0.0)
        # missed: the pullback already reached the entry before the leg was known
        if p.entry_mode == "fib":
            seg = slice(b + 1, conf + 1)
            if (ll[seg].min() <= entry) if d > 0 else (hh[seg].max() >= entry):
                continue
        conf_time = int(tt[conf]) + H4              # the confirming bar has closed
        out.append(dict(d=d, entry=entry, stop=stop, end0=end0, conf_time=conf_time,
                        expires=conf_time + expiry * H4, atr=float(atr[conf]),
                        leg_start=int(tt[a]), leg_end=int(tt[b]), leg_from=pa, leg_to=pb))
    return out


def triggered(s: dict, t: np.ndarray, h: np.ndarray, l: np.ndarray, mode: str) -> bool:
    """Is the LAST M15 bar of (t, h, l) the bar this setup enters on?

    fib: the first M15 bar after confirmation that trades back to the entry
    level, provided price has not run beyond the leg's end first (the setup is
    void then) and that bar does not also run beyond it.
    immediate: the first M15 bar whose close is at or past the end of the
    confirming H4 bar. Not "the bar that ends exactly on it": markets with a
    daily break or a weekend have no such bar for some buckets, and those legs
    were silently skipped."""
    last_t = int(t[-1])
    if mode == "immediate":
        prev_close = int(t[-2]) + M15 if len(t) > 1 else -1
        return prev_close < s["conf_time"] <= last_t + M15
    if not (s["conf_time"] <= last_t < s["expires"]):
        return False
    i0 = int(np.searchsorted(t, s["conf_time"], side="left"))
    hs, ls = h[i0:], l[i0:]
    if s["d"] > 0:
        touch, beyond = ls <= s["entry"], hs > s["end0"]
    else:
        touch, beyond = hs >= s["entry"], ls < s["end0"]
    ev = np.flatnonzero(touch | beyond)
    return bool(len(ev)) and ev[0] == len(hs) - 1 and bool(touch[-1]) and not bool(beyond[-1])


def trail_level(H: dict, entry_time: int, direction: str, multiple: float, atr_bars: int) -> float | None:
    """Chandelier level: best H4 close among the bars completed since the entry
    (the entry's own bucket included once it closes) minus `multiple` x H4 ATR
    of the last completed bar. None until one such bar has closed."""
    sel = H["t"] + H4 > entry_time
    if not sel.any():
        return None
    atr = atr_wilder(H["h"], H["l"], H["c"], atr_bars)[-1]
    closes = H["c"][sel]
    if str(direction).upper().startswith("B"):
        return float(closes.max() - multiple * atr)
    return float(closes.min() + multiple * atr)
