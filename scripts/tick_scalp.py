"""
scripts/tick_scalp.py

High-frequency scalping on real ticks: does any simple tick rule make money
after the spread?

    python scripts/tick_scalp.py fetch --symbol XAUUSD --days 10 --out /tmp/ticks_XAUUSD.npz   # on the server
    python scripts/tick_scalp.py test /tmp/ticks_XAUUSD.npz

Every fill uses the real quote of the tick it happens on: a buy opens at the
ask and closes at the bid, a sell the other way, so the spread is always paid.
Orders fill on the first tick at least `latency` seconds after the signal
(default 0.25 s - a VPS next to the broker; a phone is slower).

Rules tested (m = mid price, n ticks back):
  mom   m[i] - m[i-n] >= x: buy (<= -x: sell) - ride the burst
  fade  the opposite - bet the burst snaps back
  hedge always one buy AND one sell open (the video's look); each closes at
        +tp or -sl and is replaced at once
Exits: take profit tp, stop sl (both in $ of gold price), or `hold` seconds.
One position at a time per rule (per side for hedge).

The data is split in time: rules are ranked on the first 60 %, then the best
are shown on the last 40 % they never saw. Only that second number is evidence.
"""

from __future__ import annotations

import argparse
import itertools
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def fetch(symbol: str, days: int, out: str) -> None:
    import backend  # noqa: F401 - installs the MT5 bridge on Linux
    import MetaTrader5 as mt5
    if not mt5.initialize():
        raise SystemExit(f"MT5 initialize failed: {mt5.last_error()}")
    mt5.symbol_select(symbol, True)
    end = datetime.now(timezone.utc)
    t = end - timedelta(days=days)
    parts = []
    while t < end:
        b = min(t + timedelta(hours=6), end)
        r = mt5.copy_ticks_range(symbol, int(t.timestamp()), int(b.timestamp()), mt5.COPY_TICKS_INFO)
        if r is not None and len(r):
            parts.append(np.asarray(r))
        t = b
    if not parts:
        raise SystemExit(f"no ticks: {mt5.last_error()}")
    r = np.concatenate(parts)
    tm = r["time_msc"].astype(np.int64) if "time_msc" in r.dtype.names else r["time"].astype(np.int64) * 1000
    keep = (r["bid"] > 0) & (r["ask"] >= r["bid"])
    tm, bid, ask = tm[keep], r["bid"][keep].astype(float), r["ask"][keep].astype(float)
    o = np.argsort(tm, kind="stable")
    np.savez_compressed(out, t=tm[o], bid=bid[o], ask=ask[o])
    sp = ask - bid
    print(f"{len(tm)} ticks {datetime.fromtimestamp(tm[o][0] / 1000, timezone.utc)} .. "
          f"{datetime.fromtimestamp(tm[o][-1] / 1000, timezone.utc)}; spread median {np.median(sp):.2f} "
          f"p90 {np.percentile(sp, 90):.2f} -> {out}")


def first_cross(arr: np.ndarray, start: int, end: int, level: float, up: bool) -> int:
    """First index in [start, end) where arr >= level (up) or <= level; -1 if none."""
    seg = arr[start:end]
    hit = seg >= level if up else seg <= level
    k = int(np.argmax(hit)) if len(seg) else 0
    return start + k if len(seg) and hit[k] else -1


def run(d, mode, n, x, tp, sl, hold, latency_ms, lo, hi, side_only=None):
    """Trades of one rule on ticks [lo, hi). Returns array of P/L in $ per 1 unit of gold (x100 = per lot)."""
    t, bid, ask = d["t"], d["bid"], d["ask"]
    mid = (bid + ask) / 2
    pnl = []
    if mode == "hedge":
        for s in ((1,) if side_only == 1 else (-1,) if side_only == -1 else (1, -1)):
            i = lo
            while i < hi - 2:
                e_px = ask[i] if s == 1 else bid[i]
                end = min(hi, int(np.searchsorted(t, t[i] + hold * 1000)))
                close = bid if s == 1 else ask
                a = first_cross(close, i + 1, end, e_px + s * tp, s == 1)
                b = first_cross(close, i + 1, end, e_px - s * sl, s == -1)
                cands = [j for j in (a, b) if j >= 0]
                j = min(cands) if cands else end - 1
                if j <= i:
                    break
                pnl.append((close[j] - e_px) * s)
                i = j + 1
        return np.array(pnl)
    sig_up = np.zeros(len(mid), bool)
    sig_dn = np.zeros(len(mid), bool)
    sig_up[n:] = mid[n:] - mid[:-n] >= x
    sig_dn[n:] = mid[n:] - mid[:-n] <= -x
    if mode == "fade":
        sig_up, sig_dn = sig_dn, sig_up
    sig = np.flatnonzero((sig_up | sig_dn)[lo:hi]) + lo
    k = 0
    while k < len(sig):
        i = sig[k]
        s = 1 if sig_up[i] else -1
        j0 = int(np.searchsorted(t, t[i] + latency_ms))            # the fill tick
        if j0 >= hi - 1:
            break
        e_px = ask[j0] if s == 1 else bid[j0]
        close = bid if s == 1 else ask
        end = min(hi, int(np.searchsorted(t, t[j0] + hold * 1000)))
        a = first_cross(close, j0 + 1, end, e_px + s * tp, s == 1)
        b = first_cross(close, j0 + 1, end, e_px - s * sl, s == -1)
        cands = [j for j in (a, b) if j >= 0]
        j = min(cands) if cands else max(end - 1, j0 + 1)
        pnl.append((close[j] - e_px) * s)
        k = int(np.searchsorted(sig, j + 1))
    return np.array(pnl)


def stats(p: np.ndarray) -> str:
    if len(p) == 0:
        return "no trades"
    eq = np.cumsum(p)
    dd = float(np.max(np.maximum.accumulate(eq) - eq))
    w = p[p > 0]
    return (f"{len(p):6d} trades  win {100 * len(w) / len(p):4.0f}%  net {p.sum():+8.2f} $/oz  "
            f"avg {p.mean():+.3f}  maxDD {dd:6.2f}")


def test(path: str, latency_ms: int, top: int) -> None:
    d = dict(np.load(path))
    N = len(d["t"])
    cut = int(N * 0.6)
    sp = d["ask"] - d["bid"]
    span = (d["t"][-1] - d["t"][0]) / 86400000
    print(f"{N} ticks over {span:.1f} days, spread median ${np.median(sp):.2f} (p90 ${np.percentile(sp, 90):.2f}); "
          f"latency {latency_ms} ms")
    print(f"in-sample: first 60 % ({cut} ticks) - out-of-sample: last 40 %\n")
    grid = []
    for mode in ("mom", "fade"):
        for n, x, tp, sl in itertools.product((5, 10, 20, 50), (0.3, 0.6, 1.0, 1.5), (0.3, 0.6, 1.0, 2.0),
                                              (0.6, 1.0, 2.0, 4.0)):
            grid.append((mode, n, x, tp, sl, 300))
    for tp, sl in itertools.product((0.2, 0.3, 0.5, 1.0, 2.0), (1.0, 2.0, 5.0, 10.0, 1000.0)):
        grid.append(("hedge", 0, 0, tp, sl, 3600))
    res = []
    for g in grid:
        p_in = run(d, *g, latency_ms, 0, cut)
        res.append((p_in.sum(), g, p_in))
    res.sort(key=lambda r: -r[0])
    print(f"{len(grid)} rules tested. Best {top} in-sample, and the same rules out-of-sample:")
    for s_in, g, p_in in res[:top]:
        p_out = run(d, *g, latency_ms, cut, N)
        name = f"{g[0]} n={g[1]} x={g[2]} tp={g[3]} sl={g[4]}" if g[0] != "hedge" else f"hedge tp={g[3]} sl={g[4]}"
        print(f"  {name:36} IN  {stats(p_in)}")
        print(f"  {'':36} OUT {stats(p_out)}")
    pos = sum(1 for r in res if r[0] > 0)
    print(f"\n{pos}/{len(res)} rules were profitable in-sample (before looking at the out-of-sample part).")
    hedges = [r for r in res if r[1][0] == "hedge"]
    print("video-style hedge (always a buy and a sell, replaced at +tp / -sl), whole period:")
    for _, g, _ in sorted(hedges, key=lambda r: (r[1][3], r[1][4])):
        print(f"  tp={g[3]:<4} sl={g[4]:<6} {stats(run(d, *g, latency_ms, 0, N))}")


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("--symbol", default="XAUUSD")
    f.add_argument("--days", type=int, default=10)
    f.add_argument("--out", required=True)
    t = sub.add_parser("test")
    t.add_argument("path")
    t.add_argument("--latency", type=int, default=250, help="ms from signal to fill")
    t.add_argument("--top", type=int, default=8)
    a = ap.parse_args()
    if a.cmd == "fetch":
        fetch(a.symbol, a.days, a.out)
    else:
        test(a.path, a.latency, a.top)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
