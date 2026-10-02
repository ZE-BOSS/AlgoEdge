#!/usr/bin/env python
"""
scripts/run_scalp_screen.py

Which intraday rule, if any, survives costs on the four markets that can carry a
scalper?

THE CONSTRAINT THAT DECIDES THIS
--------------------------------
Scalping is a cost problem before it is a strategy problem. At a stop of one M5
bar range the round trip costs, as a share of R:

    US Tech 100 5.8% | Step Index 7.6% | XAUUSD 8.1% | Vol over Boom 400 9.3%
    ... and Crash 1000 35%, GBPJPY 40%, EURUSD 60%

So only the first four are screened. On the rest no entry signal can recover the
spread, and a "profitable" result there would be a measurement error.

WHAT IS SCREENED
----------------
Four one-line rules, each long and short, each with the SAME stop and target
geometry so the comparison is about the signal and nothing else:

    MOM    close beyond the last N bars' extreme      (continuation)
    FADE   the same break, traded the other way       (reversion)
    ORB    first break of the first hour's range      (opening range)
    VWAPR  a stretch from the session VWAP, faded     (reversion to value)

Every trade pays the real spread twice. Every rule gets a RANDOM-ENTRY control
on the same bars with the same geometry: if the rule cannot beat a coin flip
that pays the same costs, it has no signal in it.

    py -3.12 scripts/run_scalp_screen.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# only the markets whose cost floor leaves room for a scalp
MARKETS = ["US Tech 100", "Step Index", "XAUUSD", "Vol over Boom 400"]


def load(symbol: str, count: int = 60_000):
    import MetaTrader5 as mt5
    mt5.symbol_select(symbol, True)
    r = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, count)
    if r is None or len(r) < 5_000:
        return None, None
    s = mt5.symbol_info(symbol)
    return r, (s.spread * s.point if s else 0.0)


def session_vwap(t: np.ndarray, h, l, c, v):
    """VWAP reset each UTC day."""
    day = t // 86_400
    tp = (h + l + c) / 3.0
    out = np.empty(len(c))
    cum_pv = cum_v = 0.0
    last = None
    for i in range(len(c)):
        if day[i] != last:
            cum_pv = cum_v = 0.0
            last = day[i]
        cum_pv += tp[i] * max(v[i], 1.0)
        cum_v += max(v[i], 1.0)
        out[i] = cum_pv / cum_v
    return out


def signals(kind: str, r, n: int, rng):
    """+1 long, -1 short, 0 nothing, per bar."""
    h, l, c = r["high"].astype(float), r["low"].astype(float), r["close"].astype(float)
    t = r["time"].astype("int64")
    sig = np.zeros(len(c), np.int8)
    if kind in ("MOM", "FADE"):
        for i in range(n, len(c)):
            hi, lo = h[i - n:i].max(), l[i - n:i].min()
            if c[i] > hi: sig[i] = 1
            elif c[i] < lo: sig[i] = -1
        if kind == "FADE": sig = -sig
    elif kind == "ORB":
        day = t // 86_400
        for d in np.unique(day):
            m = np.flatnonzero(day == d)
            if len(m) < 24: continue
            first = m[:12]                       # the first hour of M5 bars
            hi, lo = h[first].max(), l[first].min()
            for i in m[12:]:
                if c[i] > hi: sig[i] = 1; break
                if c[i] < lo: sig[i] = -1; break
    elif kind == "VWAPR":
        v = r["tick_volume"].astype(float)
        vw = session_vwap(t, h, l, c, v)
        rngbar = np.mean(h - l)
        for i in range(n, len(c)):
            if c[i] > vw[i] + 2 * rngbar: sig[i] = -1
            elif c[i] < vw[i] - 2 * rngbar: sig[i] = 1
    elif kind == "RANDOM":
        hits = rng.random(len(c)) < 0.02
        sig = np.where(hits, rng.choice([-1, 1], size=len(c)), 0).astype(np.int8)
    return sig


def simulate(r, sig, spread: float, *, stop_mult: float, rr: float):
    """Next-bar-open entry, fixed geometry, spread charged on entry and exit."""
    o, h, l = r["open"].astype(float), r["high"].astype(float), r["low"].astype(float)
    rngbar = float(np.mean(h - l))
    stop_d = stop_mult * rngbar
    if stop_d <= 0:
        return np.array([])
    cost_r = (2 * spread) / stop_d          # round trip as a fraction of R
    out = []
    i = 1
    while i < len(o) - 1:
        s = sig[i]
        if s == 0:
            i += 1; continue
        entry = o[i + 1]
        stop = entry - s * stop_d
        target = entry + s * rr * stop_d
        res = None
        for j in range(i + 1, min(i + 200, len(o))):
            if s > 0:
                if l[j] <= stop: res = -1.0; break
                if h[j] >= target: res = rr; break
            else:
                if h[j] >= stop: res = -1.0; break
                if l[j] <= target: res = rr; break
        if res is not None:
            out.append(res - cost_r)        # costs, every trade
            i = j + 1
        else:
            i += 1
    return np.array(out)


def row(label: str, rs: np.ndarray, risk_pct: float, balance: float) -> str:
    if len(rs) < 50:
        return f"    {label:<26}{len(rs):>6}   too few"
    exp = rs.mean()
    t = exp / (rs.std(ddof=1) / np.sqrt(len(rs))) if rs.std() > 0 else 0.0
    pnl = exp * len(rs) * balance * risk_pct / 100.0
    return (f"    {label:<26}{len(rs):>6}{(rs > 0).mean() * 100:>6.0f}%"
            f"{exp:>+9.3f}{t:>+7.2f}{pnl:>12,.0f}{pnl / balance * 100:>9.1f}%")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lookback", type=int, default=12)
    ap.add_argument("--stop-mult", type=float, default=1.0, help="stop, in mean M5 bar ranges")
    ap.add_argument("--rr", type=float, default=1.5)
    ap.add_argument("--risk", type=float, default=1.0)
    ap.add_argument("--balance", type=float, default=10_000.0)
    ap.add_argument("--markets", nargs="*", default=MARKETS)
    args = ap.parse_args()

    import MetaTrader5 as mt5
    if not mt5.initialize():
        print("MT5 not available"); return
    rng = np.random.default_rng(3)

    print(f"\nSCALP SCREEN — M5, stop {args.stop_mult:g}x bar range, {args.rr:g}R target, "
          f"flat {args.risk:g}% of ${args.balance:,.0f}, SPREAD CHARGED\n")
    hdr = (f"    {'rule':<26}{'n':>6}{'win':>7}{'exp R':>9}{'t':>7}"
           f"{'P&L $':>12}{'return':>9}")
    best = []
    for m in args.markets:
        r, spread = load(m)
        if r is None:
            print(f"  {m}: no data"); continue
        rngbar = float(np.mean(r["high"].astype(float) - r["low"].astype(float)))
        print(f"  {m}   spread {spread:.4f} = {(2*spread)/(args.stop_mult*rngbar)*100:.1f}% of R")
        print(hdr)
        for kind in ("MOM", "FADE", "ORB", "VWAPR", "RANDOM"):
            sig = signals(kind, r, args.lookback, rng)
            rs = simulate(r, sig, spread, stop_mult=args.stop_mult, rr=args.rr)
            print(row(kind, rs, args.risk, args.balance))
            if kind != "RANDOM" and len(rs) >= 50:
                e = rs.mean()
                t = e / (rs.std(ddof=1) / np.sqrt(len(rs))) if rs.std() > 0 else 0
                best.append((t, e, m, kind, len(rs)))
        print()

    print("SURVIVORS (t >= 2 after costs, which is the bar worth building on)")
    hits = [b for b in sorted(best, reverse=True) if b[0] >= 2.0]
    if not hits:
        print("  none. No rule screened beats its own costs on any of these markets,")
        print("  which is the answer: a scalper here would be paying the spread for")
        print("  the privilege of a coin flip.")
    for t, e, m, kind, n in hits:
        print(f"  {m:<20} {kind:<8} n {n:>6}  {e:+.3f}R  t {t:+.2f}")
    mt5.shutdown()


if __name__ == "__main__":
    main()
