"""
research/fib/fib_backtest.py - the user's H4 Fibonacci-retracement method as fixed,
hindsight-free rules (codified from Fib_Strategy.zip, 2026-10-25).

  leg      : two consecutive confirmed swing points (fractal, K bars each side).
             A swing is only KNOWN K bars after it prints - that is when the Fib
             can be drawn. Leg must be >= MIN_LEG ATRs ("clear trend").
  fib      : 1 at the leg's start, 0 at its end (TradingView convention).
  entry    : limit order at 0.5 (the 0.5-0.618 "fib zone"), live from the bar
             after the leg is confirmed.
  missed   : price already reached the entry level between the swing and its
             confirmation -> no trade (the user's "NO TRADE MISSED ENTRY").
  cancel   : price runs beyond 0 (the leg extends) before the fill, or EXPIRY bars pass.
  stop     : 0.786 (the user: "a bit above 0.7"); target: 0 (end of the fib).
  options  : structure (BOS / CHoCH), order block, repeatedly-tested zone,
             confirmation-candle entry, other SL/TP levels.

Prices are bid bars; one spread per round trip is charged as spread/risk in R.
Ambiguous bars (stop and target both inside one H4 bar) are resolved on M15
where M15 exists, else the stop is assumed first. On the fill bar a target is
only credited when M15 proves it came after the fill.

    PYTHONPATH="venv/Lib/site-packages;." py -3.12 research/fib/fib_backtest.py
"""

from __future__ import annotations

import dataclasses as dc
import json
import math
import sys
from pathlib import Path

import numpy as np

CACHE = Path(__file__).parent / "cache"
RESULTS = Path(__file__).parent / "results"
H4 = 14_400
MAX_HOLD = 120          # bars (20 days) then exit at the close


@dc.dataclass(frozen=True)
class V:
    name: str
    k: int = 3                      # fractal half-width
    min_leg: float = 3.0            # leg size in ATR(14)
    structure: str = "any"          # any | break | bos | choch
    ob: bool = False                # an order block must overlap the 0.5-0.786 band
    zone_touches: int = 0           # earlier swing points inside the 0.5-0.618 zone (+-0.25 ATR)
    entry: float = 0.5
    entry_mode: str = "limit"       # limit | confirm (rejection candle in the zone, enter next open)
    sl: float = 0.786
    tp: float = 0.0
    expiry: int = 30                # bars the order stays live (5 days)
    intrabar: bool = True           # resolve ambiguous bars on M15
    trend: str = "none"             # none | htf: only legs in the direction of the 50-day trend (SMA 300 H4 + its slope)
    exit: str = "target"            # target | trail: no target, chandelier stop TRAIL_ATR x ATR below the best close
    trail_atr: float = 3.0


VARIANTS = [
    V("base: 0.5 entry, SL 0.786, TP 0"),
    V("structure: break of prior swing", structure="break"),
    V("structure: BOS (continuation)", structure="bos"),
    V("structure: CHoCH (reversal)", structure="choch"),
    V("confluence: order block", ob=True),
    V("confluence: zone tested >= 1x", zone_touches=1),
    V("confluence: zone tested >= 2x", zone_touches=2),
    V("entry: 0.618", entry=0.618),
    V("entry: confirmation candle", entry_mode="confirm"),
    V("stop: 0.705", sl=0.705),
    V("stop: 1.0 (beyond the fib)", sl=1.0),
    V("target: -0.272 extension", tp=-0.272),
    V("target: 0.236 (early)", tp=0.236),
    V("swings: K=2 (smaller legs)", k=2),
    V("swings: K=5 (bigger legs)", k=5),
    V("leg >= 5 ATR", min_leg=5.0),
    V("as shown: CHoCH + OB + 0.5/0.786/0", structure="choch", ob=True),
    V("CHoCH + zone>=1 + confirm", structure="choch", zone_touches=1, entry_mode="confirm"),
]


# -- data ---------------------------------------------------------------------------------------
def load(sym: str):
    f = lambda tf: CACHE / f"{sym.replace(' ', '_')}_{tf}.npz"           # noqa: E731
    h4 = dict(np.load(f("H4")))
    m15 = dict(np.load(f("M15"))) if f("M15").exists() else None
    if m15 is not None and m15["t"][-1] > h4["t"][-1] + H4:
        # The terminal's H4 history can stop days short of M15's: rebuild the tail from M15.
        sel = m15["t"] >= h4["t"][-1] + H4
        b = m15["t"][sel] // H4 * H4
        u, start = np.unique(b, return_index=True)
        end = np.r_[start[1:], len(b)]
        tail = {"t": u,
                "o": m15["o"][sel][start], "c": m15["c"][sel][end - 1],
                "h": np.maximum.reduceat(m15["h"][sel], start), "l": np.minimum.reduceat(m15["l"][sel], start),
                "spread": np.maximum.reduceat(m15["spread"][sel], start)}
        h4 = {k: np.r_[h4[k], tail[k]] for k in h4}
    return h4, m15


def atr(h, l, c, n=14):
    tr = np.maximum(h[1:], c[:-1]) - np.minimum(l[1:], c[:-1])
    tr = np.r_[h[0] - l[0], tr]
    out = np.empty_like(tr)
    out[: n] = np.cumsum(tr[: n]) / np.arange(1, n + 1)
    for i in range(n, len(tr)):
        out[i] = out[i - 1] + (tr[i] - out[i - 1]) / n
    return out


def pivots(h, l, k):
    """[(index, +1 high / -1 low, price)] alternating, in confirmation order (index + k)."""
    n = len(h)
    raw = []
    for i in range(k, n - k):
        if h[i] > h[i - k:i].max() and h[i] >= h[i + 1:i + k + 1].max():
            raw.append((i, 1, h[i]))
        if l[i] < l[i - k:i].min() and l[i] <= l[i + 1:i + k + 1].min():
            raw.append((i, -1, l[i]))
    return raw


# -- one market -----------------------------------------------------------------------------------
class Market:
    def __init__(self, sym):
        self.sym = sym
        d, m = load(sym)
        self.t, self.o, self.h, self.l, self.c, self.sp = d["t"], d["o"], d["h"], d["l"], d["c"], d["spread"]
        self.atr = atr(self.h, self.l, self.c)
        self.m15 = m
        self._piv = {}
        cs = np.r_[0, np.cumsum(self.c)]
        self.sma = np.full(len(self.c), np.nan)
        self.sma[299:] = (cs[300:] - cs[:-300]) / 300     # 50 days of H4 closes, known at each bar's close

    def piv(self, k):
        if k not in self._piv:
            self._piv[k] = pivots(self.h, self.l, k)
        return self._piv[k]

    def m15_slice(self, j):
        """M15 bars inside H4 bar j (empty when no M15 covers it)."""
        if self.m15 is None:
            return None
        t0 = self.t[j]
        a, b = np.searchsorted(self.m15["t"], [t0, t0 + H4])
        return None if b - a < 4 else (a, b)


def legs(mk: Market, v: V):
    """Every leg with its fib, structure and confluence, as known at confirmation."""
    seq = []                                            # alternating confirmed pivots
    out = []
    for (i, typ, px) in mk.piv(v.k):
        if seq and seq[-1][1] == typ:
            if (typ == 1 and px > seq[-1][2]) or (typ == -1 and px < seq[-1][2]):
                seq[-1] = (i, typ, px)                  # the same swing extended
            else:
                continue
        else:
            seq.append((i, typ, px))
        if len(seq) < 2:
            continue
        (a, ta, pa), (b, tb, pb) = seq[-2], seq[-1]
        d = 1 if tb == 1 else -1                        # +1: up leg (buy the pullback), -1: down leg (sell it)
        rng = abs(pb - pa)
        if rng < v.min_leg * mk.atr[b]:
            continue
        lvl = lambda f, pb=pb, d=d, rng=rng: pb - d * f * rng   # noqa: E731 - fib level f (0 = leg end, 1 = start); bound now, not at call time
        # structure: the leg breaks the previous swing of its end's type?
        brk = bos = choch = False
        if len(seq) >= 4:
            (_, _, p_same), (_, _, p_start_prev) = seq[-3], seq[-4]
            brk = (pb - p_same) * d > 0                  # new high above the prior high (up leg) / low below prior low
            if brk:
                # start of this leg vs the previous swing of the same type as the start
                bos = (pa - p_start_prev) * d > 0        # higher low in an up leg = continuation
                choch = not bos
        # order block: last opposite-colour candle at/just before the leg start, overlapping 0.5-0.786
        ob = False
        for j in range(a, max(a - 6, 0), -1):
            if (mk.c[j] - mk.o[j]) * d < 0:             # down candle before an up leg / up candle before a down leg
                z_lo, z_hi = sorted((lvl(0.5), lvl(0.786)))
                ob = mk.l[j] <= z_hi and mk.h[j] >= z_lo
                break
        # repeatedly tested zone: earlier swing points inside the 0.5-0.618 band
        z_lo, z_hi = sorted((lvl(0.5), lvl(0.618)))
        tol = 0.25 * mk.atr[b]
        touches = sum(1 for (i2, _, p2) in seq[:-2][-12:] if z_lo - tol <= p2 <= z_hi + tol)
        out.append(dict(a=a, b=b, conf=b + v.k, d=d, lvl=lvl, rng=rng,
                        brk=brk, bos=bos, choch=choch, ob=ob, touches=touches))
    return out


def admit(L, v: V, mk: Market | None = None):
    if v.trend == "htf":
        j = L["conf"]
        if j < 330 or np.isnan(mk.sma[j - 30]):
            return False
        d = L["d"]
        if not ((mk.c[j] - mk.sma[j]) * d > 0 and (mk.sma[j] - mk.sma[j - 30]) * d > 0):
            return False
    if v.structure == "break" and not L["brk"]:
        return False
    if v.structure == "bos" and not L["bos"]:
        return False
    if v.structure == "choch" and not L["choch"]:
        return False
    if v.ob and not L["ob"]:
        return False
    return L["touches"] >= v.zone_touches


def first_touch(mk: Market, j, d, levels, start_px=None):
    """Within H4 bar j, which of `levels` (name -> (price, side)) M15 shows first.
    side +1: touched when high >= price; -1: when low <= price. None if no M15."""
    s = mk.m15_slice(j)
    if s is None:
        return None
    a, b = s
    for x in range(a, b):
        hit = [n for n, (p, side) in levels.items()
               if (side > 0 and mk.m15["h"][x] >= p) or (side < 0 and mk.m15["l"][x] <= p)]
        if len(hit) == 1:
            return hit[0], x
        if len(hit) > 1:
            return "both", x
    return "none", None


def run_trade(mk: Market, j0, d, entry, sl, tp, v: V, fill_m15=None):
    """Manage a filled trade from bar j0 (the fill bar). Returns (exit_px, exit_bar, how, ambiguous)."""
    amb = 0
    trail = v.exit == "trail"
    if trail:
        tp = d * math.inf                               # no target: the trailing stop is the only exit
    best = entry
    hold = MAX_HOLD * 3 if trail else MAX_HOLD
    for j in range(j0, min(j0 + hold, len(mk.t))):
        if trail and j > j0:
            # the stop for bar j comes from closes up to bar j-1 only
            best = max(best, mk.c[j - 1]) if d > 0 else min(best, mk.c[j - 1])
            new = best - d * v.trail_atr * mk.atr[j - 1]
            sl = max(sl, new) if d > 0 else min(sl, new)
        hi, lo = mk.h[j], mk.l[j]
        if j == j0 and fill_m15 is not None:
            # only the M15 bars after the fill decide the rest of the fill bar
            a, b = mk.m15_slice(j)
            x0 = fill_m15
            hs, ls = mk.m15["h"][x0:b], mk.m15["l"][x0:b]
            stop_first = None
            for q in range(len(hs)):
                s_hit = (ls[q] <= sl) if d > 0 else (hs[q] >= sl)
                t_hit = (hs[q] >= tp) if d > 0 else (ls[q] <= tp)
                if q == 0:
                    t_hit = False                       # the fill M15 bar: target not provable after the fill
                if s_hit:
                    return sl, j, "sl", amb
                if t_hit:
                    return tp, j, "tp", amb
            continue
        s_hit = (lo <= sl) if d > 0 else (hi >= sl)
        t_hit = (hi >= tp) if d > 0 else (lo <= tp)
        if j == j0:
            if s_hit:
                return sl, j, "sl", amb
            continue                                    # no M15: the fill bar can't prove a target
        gap_open = mk.o[j]
        if (gap_open - sl) * d <= 0:
            return gap_open, j, "sl_gap", amb
        if (gap_open - tp) * d >= 0:
            return gap_open, j, "tp_gap", amb
        if s_hit and t_hit:
            amb += 1
            ft = first_touch(mk, j, d, {"sl": (sl, -d), "tp": (tp, d)}) if v.intrabar else None
            if ft is not None and ft[0] == "tp":
                return tp, j, "tp", amb
            return sl, j, "sl", amb
        if s_hit:
            return sl, j, "sl", amb
        if t_hit:
            return tp, j, "tp", amb
    j = min(j0 + hold, len(mk.t)) - 1
    return mk.c[j], j, "time", amb


def backtest(mk: Market, v: V):
    trades, missed, ran_away, expired, busy, setups = [], 0, 0, 0, 0, 0
    free_from = 0
    n = len(mk.t)
    for L in legs(mk, v):
        if not admit(L, v, mk):
            continue
        conf, d, lvl = L["conf"], L["d"], L["lvl"]
        if conf + 1 >= n:
            continue
        setups += 1
        if conf < free_from:
            busy += 1
            continue
        e, sl, tp, end0 = lvl(v.entry), lvl(v.sl), lvl(v.tp), lvl(0.0)
        if v.entry_mode == "market":
            # control: no pullback - buy/sell the next open once the leg is confirmed
            px = mk.o[conf + 1]
            if (px - sl) * d <= 0:
                continue
            filled = (conf + 1, px, None)
            j0 = conf + 1
            risk = abs(px - sl)
            if risk < 0.02 * mk.atr[j0]:
                continue
            ex, jx, how, amb = run_trade(mk, j0, d, px, sl, tp, v)
            trades.append(dict(t=int(mk.t[j0]), tx=int(mk.t[jx]), d=d, entry=px, sl=sl, tp=tp, exit=ex,
                               how=how, r=(ex - px) * d / risk - mk.sp[j0] / risk, bars=jx - j0, amb=amb,
                               rr=abs(tp - px) / risk, leg=[int(mk.t[L["a"]]), int(mk.t[L["b"]])]))
            free_from = jx + 1
            continue
        # missed: the pullback reached the entry before the swing was even confirmed
        seg = slice(L["b"] + 1, conf + 1)
        reached = (mk.l[seg].min() <= e) if d > 0 else (mk.h[seg].max() >= e)
        if reached:
            missed += 1
            continue
        filled = None
        for j in range(conf + 1, min(conf + 1 + v.expiry, n)):
            hi, lo = mk.h[j], mk.l[j]
            beyond = (hi > end0) if d > 0 else (lo < end0)          # the leg extended: setup void
            if v.entry_mode == "limit":
                touch = (lo <= e) if d > 0 else (hi >= e)
                if touch and beyond:
                    ft = first_touch(mk, j, d, {"e": (e, -d), "x": (end0, d)}) if v.intrabar else None
                    if ft is None or ft[0] != "e":
                        ran_away += 1; filled = "void"; break
                    filled = (j, min(e, mk.o[j]) if d > 0 else max(e, mk.o[j]), ft[1]); break
                if beyond:
                    ran_away += 1; filled = "void"; break
                if touch:
                    px = min(e, mk.o[j]) if d > 0 else max(e, mk.o[j])
                    fm = None
                    if v.intrabar and mk.m15_slice(j) is not None:
                        ft = first_touch(mk, j, d, {"e": (e, -d)})
                        fm = ft[1] if ft and ft[0] == "e" else None
                    filled = (j, px, fm); break
            else:                                                    # confirm
                if beyond:
                    ran_away += 1; filled = "void"; break
                touch = (lo <= e) if d > 0 else (hi >= e)
                stop_hit = (lo <= sl) if d > 0 else (hi >= sl)
                reject = (mk.c[j] - e) * d > 0 and (mk.c[j] - mk.o[j]) * d > 0
                if touch and not stop_hit and reject and j + 1 < n:
                    px = mk.o[j + 1]
                    if (px - sl) * d > 0 and (tp - px) * d > 0:
                        filled = (j + 1, px, None)
                    else:
                        filled = "void"
                    break
        if filled is None:
            expired += 1
            continue
        if filled == "void":
            continue
        j0, px, fm = filled
        risk = abs(px - sl)
        if risk < 0.02 * mk.atr[j0] or risk <= 0:
            continue
        ex, jx, how, amb = run_trade(mk, j0, d, px, sl, tp, v, fill_m15=fm)
        r = (ex - px) * d / risk - mk.sp[j0] / risk
        trades.append(dict(t=int(mk.t[j0]), tx=int(mk.t[jx]), d=d, entry=px, sl=sl, tp=tp, exit=ex,
                           how=how, r=r, bars=jx - j0, amb=amb, rr=abs(tp - px) / risk,
                           leg=[int(mk.t[L["a"]]), int(mk.t[L["b"]])]))
        free_from = jx + 1
    return dict(trades=trades, missed=missed, ran_away=ran_away, expired=expired, busy=busy, setups=setups)


# -- stats ----------------------------------------------------------------------------------------
def stats(rs, months):
    rs = np.asarray(rs, float)
    n = len(rs)
    if n == 0:
        return dict(n=0)
    wins, losses = rs[rs > 0], rs[rs <= 0]
    eq = np.cumsum(rs)
    dd_r = float((np.maximum.accumulate(np.r_[0, eq]) - np.r_[0, eq]).max())
    acct = np.cumprod(1 + 0.01 * rs)                      # 1% risk per trade, compounded
    peak = np.maximum.accumulate(np.r_[1, acct])
    dd_pct = float(((peak - np.r_[1, acct]) / peak).max() * 100)
    sd = rs.std(ddof=1) if n > 1 else float("nan")
    return dict(n=n, win=float((rs > 0).mean() * 100), exp=float(rs.mean()),
                pf=float(wins.sum() / -losses.sum()) if losses.sum() < 0 else float("inf"),
                t=float(rs.mean() / sd * math.sqrt(n)) if n > 1 and sd > 0 else float("nan"),
                dd_r=dd_r, dd_pct=dd_pct, ret_pct=float((acct[-1] - 1) * 100),
                per_month=n / months if months else float("nan"))


SYMBOLS = ["Step Index 200", "Step Index 500", "Step Index", "Step Index 300", "Step Index 400",
           "Multi Step 2 Index", "Volatility 10 Index", "Volatility 25 Index", "Volatility 50 Index",
           "Volatility 75 Index", "Volatility 100 Index", "Volatility 75 (1s) Index",
           "XAUUSD", "XAGUSD", "US Tech 100", "US SP 500", "Wall Street 30", "GBPJPY", "EURUSD", "BTCUSD"]
SINCE = 1_712_188_800       # 2024-04-04: the start of Step 200/500 history - one common window for every market


def main():
    RESULTS.mkdir(exist_ok=True)
    only = sys.argv[1:]
    out = {}
    markets = {s: Market(s) for s in SYMBOLS}
    for v in VARIANTS if not only else [x for x in VARIANTS if any(o in x.name for o in only)]:
        rows = {}
        for s, mk in markets.items():
            res = backtest(mk, v)
            tr = [x for x in res["trades"] if x["t"] >= SINCE]
            months = (mk.t[-1] - max(SINCE, mk.t[0])) / (86400 * 30.44)
            half = SINCE + (mk.t[-1] - SINCE) / 2
            rows[s] = dict(**stats([x["r"] for x in tr], months),
                           first_half=stats([x["r"] for x in tr if x["t"] < half], months / 2).get("exp"),
                           second_half=stats([x["r"] for x in tr if x["t"] >= half], months / 2).get("exp"),
                           avg_rr=float(np.mean([x["rr"] for x in tr])) if tr else None,
                           missed=res["missed"], ran_away=res["ran_away"],
                           expired=res["expired"], busy=res["busy"], setups=res["setups"],
                           full_history=stats([x["r"] for x in res["trades"]],
                                              (mk.t[-1] - mk.t[0]) / (86400 * 30.44)),
                           trades=tr)
        out[v.name] = rows
        pooled = [x["r"] for r in rows.values() for x in r["trades"]]
        ps = stats(pooled, 0)
        print(f"{v.name:42s} n={ps.get('n',0):5d}  win {ps.get('win',0):5.1f}%  exp {ps.get('exp',0):+.3f}R  "
              f"PF {ps.get('pf',0):.2f}  t {ps.get('t',0):+.2f}", flush=True)
    (RESULTS / ("fib_results.json" if not only else "fib_results_partial.json")).write_text(
        json.dumps(out, default=float))


if __name__ == "__main__":
    main()
