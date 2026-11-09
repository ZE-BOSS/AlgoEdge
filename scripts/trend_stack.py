"""
scripts/trend_stack.py

Trend following that stacks positions while the move continues, closes all of
them when it turns, and opens again when it continues. Two families:

  breakout   higher-timeframe trend (EMA 20 > EMA 50 on `htf`) + the entry bar
             closes above the highest high of the last `n` bars -> open one
             position; every further `add` x ATR in favour adds one more (up
             to `max_pos`). All share one trailing stop: highest high since the
             first entry - `trail` x ATR (chandelier). Reversal: a close below
             the lowest low of the last n/2 bars, or the trend flips -> close
             all. A new breakout re-enters.
  supertrend Supertrend(`n`, `trail`) on the entry chart, in the htf trend:
             open when it turns in the trend's favour, add every `add` x ATR in
             favour, close all when it turns back (its line is the stop).

Each position risks `risk` % of the balance on its stop at entry, 0.01-lot steps.
Orders go at the next bar's open, buys at the ask; a bar that touches the stop
closes there before anything else.

    python scripts/trend_stack.py BARS.npz [--risk 1] [--start 1000]
"""

from __future__ import annotations

import argparse
import itertools
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from trend_scalp import PER_LOT, atr, fmt, htf_trend, load  # noqa: E402


def rolling_max(x, n):
    from numpy.lib.stride_tricks import sliding_window_view
    out = np.full(len(x), np.nan)
    out[n - 1:] = sliding_window_view(x, n).max(axis=1)
    return out


def rolling_min(x, n):
    from numpy.lib.stride_tricks import sliding_window_view
    out = np.full(len(x), np.nan)
    out[n - 1:] = sliding_window_view(x, n).min(axis=1)
    return out


def supertrend(b, n, mult, at):
    h, l_, c = b["high"], b["low"], b["close"]
    mid = (h + l_) / 2
    up = mid - mult * at
    dn = mid + mult * at
    st = np.zeros(len(c))
    dirn = np.ones(len(c), dtype=np.int8)
    fu, fd = up[0], dn[0]
    for i in range(1, len(c)):
        fu = max(up[i], fu) if c[i - 1] > fu else up[i]
        fd = min(dn[i], fd) if c[i - 1] < fd else dn[i]
        if dirn[i - 1] == 1:
            dirn[i] = -1 if c[i] < fu else 1
        else:
            dirn[i] = 1 if c[i] > fd else -1
        st[i] = fu if dirn[i] == 1 else fd
    return st, dirn


class Prep:
    def __init__(self, b, htf):
        self.b = b
        self.tr = htf_trend(b, htf)
        self.atr = atr(b)
        self.hour = (b["time"] // 3600) % 24
        self._cache = {}

    def hl(self, n):
        k = ("hl", n)
        if k not in self._cache:
            b = self.b
            hh = rolling_max(b["high"], n)
            ll = rolling_min(b["low"], n)
            hh2 = rolling_max(b["high"], max(2, n // 2))
            ll2 = rolling_min(b["low"], max(2, n // 2))
            self._cache[k] = (hh, ll, hh2, ll2)
        return self._cache[k]

    def st(self, n, mult):
        k = ("st", n, mult)
        if k not in self._cache:
            from trend_scalp import ema
            b = self.b
            pc = np.concatenate([[b["close"][0]], b["close"][:-1]])
            tr = np.maximum(b["high"] - b["low"], np.maximum(abs(b["high"] - pc), abs(b["low"] - pc)))
            self._cache[k] = supertrend(b, n, mult, ema(tr, n))
        return self._cache[k]


def simulate(P, lo, hi, fam, n, trail, add, max_pos, session, risk, start=1000.0, slip=0.05, fixed=None):
    b = P.b
    o, h, l_, c, sp, at, tr = b["open"], b["high"], b["low"], b["close"], b["spread"], P.atr, P.tr
    if fam == "breakout":
        hh, ll, hh2, ll2 = P.hl(n)
    else:
        stl, sdir = P.st(n, trail)
    bal, peak, maxdd = start, start, 0.0
    side = 0                 # direction of the open stack
    pos = []                 # [entry, lots]
    stop = None
    best = None              # best price since the first entry (for the chandelier)
    last_add = None
    trades = []
    pend = None              # "open" / "add" / "close"
    for i in range(max(lo, 120), hi - 1):
        if pend:
            if pend == "close" and pos:
                px = o[i] if side == 1 else o[i] + sp[i]
                for e, lots in pos:
                    trades.append((px - e) * side * lots * PER_LOT)
                    bal += trades[-1]
                pos, side, stop = [], 0, None
            elif pend[0] in ("open", "add") and len(pos) < max_pos:
                s = pend[1]
                e = o[i] + sp[i] + slip if s == 1 else o[i] - slip
                if pend[0] == "open":
                    stp = e - s * trail * at[i - 1] if fam == "breakout" else stl[i - 1]
                else:
                    stp = stop
                dist = abs(e - stp)
                lots = (fixed if fixed else np.floor(bal * risk / 100.0 / (dist * PER_LOT) / 0.01 + 1e-9) * 0.01) if dist > 0 else 0
                if lots >= 0.01 and (e - stp) * s > 0:
                    pos.append((e, lots))
                    side = s
                    if pend[0] == "open":
                        stop, best = stp, e
                    last_add = e
            pend = None
        # the shared stop inside this bar
        if pos:
            hit = (l_[i] <= stop) if side == 1 else (h[i] + sp[i] >= stop)
            if hit:
                for e, lots in pos:
                    trades.append((stop - e) * side * lots * PER_LOT)
                    bal += trades[-1]
                pos, side, stop = [], 0, None
        eq = bal + sum(((c[i] - e) if side == 1 else (e - c[i] - sp[i])) * lots * PER_LOT for e, lots in pos)
        peak = max(peak, eq)
        maxdd = max(maxdd, (peak - eq) / peak if peak > 0 else 0)
        if bal <= start * 0.05:
            break
        t = int(tr[i])
        # manage the open stack at this close
        if pos:
            if fam == "breakout":
                best = max(best, h[i]) if side == 1 else min(best, l_[i])
                ns = best - side * trail * at[i]
                if (ns - stop) * side > 0:
                    stop = ns
                rev = (c[i] < ll2[i - 1]) if side == 1 else (c[i] > hh2[i - 1])
            else:
                ns = stl[i]
                if (ns - stop) * side > 0:
                    stop = ns
                rev = sdir[i] != side
            if rev or t != side:
                pend = "close"
                continue
            if len(pos) < max_pos and (c[i] - last_add) * side >= add * at[i]:
                pend = ("add", side)
            continue
        if t == 0 or (session and not (7 <= P.hour[i] < 20)):
            continue
        if fam == "breakout":
            if (t == 1 and c[i] > hh[i - 1]) or (t == -1 and c[i] < ll[i - 1]):
                pend = ("open", t)
        else:
            if sdir[i] == t and sdir[i - 1] != t:
                pend = ("open", t)
    for e, lots in pos:
        px = c[hi - 1] if side == 1 else c[hi - 1] + sp[hi - 1]
        trades.append((px - e) * side * lots * PER_LOT)
        bal += trades[-1]
    return bal, maxdd, np.array(trades)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("bars")
    ap.add_argument("--start", type=float, default=1000.0)
    ap.add_argument("--risk", type=float, default=1.0)
    ap.add_argument("--top", type=int, default=8)
    a = ap.parse_args()
    b = load(a.bars)
    N = len(b["time"])
    cut = int(N * 0.6)
    d = lambda i: datetime.fromtimestamp(int(b["time"][i]), timezone.utc).strftime("%Y-%m-%d")  # noqa: E731
    tf = int(np.median(np.diff(b["time"][:1000])) // 60)
    print(f"{N} {tf}m bars {d(0)} .. {d(N - 1)}; in-sample to {d(cut)}; spread median ${np.median(b['spread']):.2f}; "
          f"{a.risk:g}% risk per position, ${a.start:,.0f} start")
    res = []
    for htf in (15, 60, 240):
        P = Prep(b, htf)
        for n, trail, add, mp, sess in itertools.product((10, 20, 40), (1.5, 2.5, 4.0), (0.5, 1.0), (1, 3, 5),
                                                         (False, True)):
            g = ("breakout", n, trail, add, mp, sess)
            bal, dd, trd = simulate(P, 0, cut, *g, a.risk, a.start)
            res.append((bal, P, htf, g, dd, trd))
        for n, mult, add, mp, sess in itertools.product((10,), (2.0, 3.0, 4.0), (0.5, 1.0), (1, 3, 5), (False, True)):
            g = ("supertrend", n, mult, add, mp, sess)
            bal, dd, trd = simulate(P, 0, cut, *g, a.risk, a.start)
            res.append((bal, P, htf, g, dd, trd))
    res.sort(key=lambda r: -r[0])
    print(f"{len(res)} rule sets; {sum(1 for r in res if r[0] > a.start)} grew the account in-sample.\n")
    out_pos = 0
    outs = []
    for k, (bal, P, htf, g, dd, trd) in enumerate(res):
        b2, dd2, tr2 = simulate(P, cut, N, *g, a.risk, a.start)
        out_pos += b2 > a.start
        outs.append(b2)
        if k < a.top:
            fam, n, trail, add, mp, sess = g
            print(f"{fam} htf {htf}m n {n} {'trail' if fam == 'breakout' else 'mult'} {trail} add {add}xATR "
                  f"max {mp} {'London+NY' if sess else '24h'}")
            print(f"   IN  {fmt(a.start, bal, dd, trd)}")
            print(f"   OUT {fmt(a.start, b2, dd2, tr2)}")
    print(f"\nout-of-sample: {out_pos}/{len(res)} rule sets grew the account; "
          f"median ending balance ${np.median(outs):,.0f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
