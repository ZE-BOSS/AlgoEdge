#!/usr/bin/env python
"""
scripts/run_zone_robust.py

The two checks that decide whether the round-number continuation edge is real.

1. OVERLAP.  run_zone_money.py lets trades overlap: several levels can be live
   at once, so the same market move is counted more than once and the t-stat is
   flattered. Here a market holds ONE position at a time — which is also the
   only way you could trade it — so every trade is an independent draw.

2. STABILITY.  A +0.04R edge measured once over five years can be one good year
   and four flat ones. The per-year table shows which it is.

    python scripts/run_zone_robust.py
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.run_zone_study import (  # noqa: E402
    CONTROL_OFFSETS, OUT, load, rolling_max, rolling_min,
)

# The cells that looked positive AND beat their control on full history.
CANDIDATES = [
    ("EURUSD", 0.005), ("EURUSD", 0.01),
    ("XAUUSD", 50.0), ("XAUUSD", 100.0),
    ("BTCUSD", 1000.0),
    ("USDJPY", 2.5),
    ("GBPJPY", 1.0),          # carried as the counter-example
]


def sequential_trades(b: dict, step: float, offset: float, approach: int, horizon: int,
                      stop_frac: float, slip_points: float) -> list[dict]:
    """Continuation trades, ONE AT A TIME (no overlap)."""
    high, low, close, t = b["high"], b["low"], b["close"], b["time"]
    spread_px = b["spread_pts"] * b["point"]
    n = len(high)
    R = stop_frac * step
    slip = slip_points * b["point"]

    prev_max = rolling_max(high, approach)
    prev_min = rolling_min(low, approach)
    with np.errstate(invalid="ignore"):
        lvl_up = (np.floor(prev_max / step - offset) + 1 + offset) * step
        lvl_dn = (np.ceil(prev_min / step - offset) - 1 + offset) * step

    touch_up = np.isfinite(lvl_up) & (high >= lvl_up)
    touch_dn = np.isfinite(lvl_dn) & (low <= lvl_dn)

    out: list[dict] = []
    i = approach + 1
    while i < n - horizon - 1:
        if touch_up[i]:
            L, long_ = lvl_up[i], True
        elif touch_dn[i]:
            L, long_ = lvl_dn[i], False
        else:
            i += 1
            continue

        cost = spread_px[i] + slip
        h = high[i + 1: i + 1 + horizon]
        lo_ = low[i + 1: i + 1 + horizon]
        if long_:
            tgt = np.flatnonzero(h >= L + R)
            stp = np.flatnonzero(lo_ <= L - R)
        else:
            tgt = np.flatnonzero(lo_ <= L - R)
            stp = np.flatnonzero(h >= L + R)

        t0 = tgt[0] if tgt.size else 10 ** 9
        s0 = stp[0] if stp.size else 10 ** 9
        if t0 == s0 == 10 ** 9:
            held, gross = horizon, ((close[i + horizon] - L) if long_ else (L - close[i + horizon]))
        elif t0 < s0:
            held, gross = int(t0) + 1, R
        else:
            held, gross = int(s0) + 1, -R

        out.append({"t": int(t[i]), "r": (gross - cost) / R, "held": held})
        i += held + 1          # flat before the next one — no overlap
    return out


def stats(tr: list[dict]) -> dict:
    if len(tr) < 2:
        return {"n": len(tr)}
    r = np.array([x["r"] for x in tr])
    eq = np.cumsum(r)
    peak = np.maximum.accumulate(eq)
    return {
        "n": int(len(r)),
        "expectancy_r": float(r.mean()),
        "total_r": float(r.sum()),
        "win_rate": float((r > 0).mean()),
        "t_stat": float(r.mean() / (r.std(ddof=1) / math.sqrt(len(r)))) if r.std() > 0 else 0.0,
        "max_dd_r": float(np.max(peak - eq)) if len(eq) else 0.0,
    }


def by_year(tr: list[dict]) -> dict:
    g = defaultdict(list)
    for x in tr:
        g[datetime.fromtimestamp(x["t"], timezone.utc).year].append(x)
    return {y: stats(v) for y, v in sorted(g.items())}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--approach", type=int, default=12)
    ap.add_argument("--horizon", type=int, default=48)
    ap.add_argument("--stop-frac", type=float, default=0.25)
    ap.add_argument("--slip-points", type=float, default=1.0)
    ap.add_argument("--tag", default="robust")
    args = ap.parse_args()

    kw = dict(approach=args.approach, horizon=args.horizon,
              stop_frac=args.stop_frac, slip_points=args.slip_points)

    results = []
    print(f"\nNON-OVERLAPPING continuation trades, 1:1 at {args.stop_frac}xstep, "
          f"costs = bar spread + {args.slip_points} points\n")
    hdr = f"{'market':10s} {'step':>8s} {'N':>5s} {'expR':>7s} {'win%':>6s} {'totR':>8s} {'t':>6s} {'ddR':>7s} | {'ctrl expR':>10s}"
    print(hdr)
    print("-" * len(hdr))

    for sym, step in CANDIDATES:
        b = load(sym)
        rnd = sequential_trades(b, step, 0.0, **kw)
        s = stats(rnd)
        ctl: list[dict] = []
        for f in CONTROL_OFFSETS:
            ctl += sequential_trades(b, step, f, **kw)
        cs = stats(ctl)
        print(f"{sym:10s} {step:>8g} {s['n']:>5d} {s['expectancy_r']:>+7.3f} "
              f"{s['win_rate'] * 100:>5.1f}% {s['total_r']:>+8.1f} {s['t_stat']:>+6.2f} "
              f"{s['max_dd_r']:>7.1f} | {cs['expectancy_r']:>+10.3f}")
        results.append({"symbol": sym, "step": step, "all": s, "control": cs,
                        "by_year": by_year(rnd), "trades": len(rnd)})

    print("\nPER YEAR (expectancy R / trades)")
    years = sorted({y for r in results for y in r["by_year"]})
    print(f"{'market':10s} {'step':>8s} " + " ".join(f"{y:>14d}" for y in years))
    for r in results:
        cells = []
        for y in years:
            s = r["by_year"].get(y)
            cells.append(f"{s['expectancy_r']:>+8.3f}/{s['n']:<5d}" if s and s.get("n", 0) > 1
                         else f"{'-':>14s}")
        print(f"{r['symbol']:10s} {r['step']:>8g} " + " ".join(cells))

    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / f"zone_robust_{args.tag}.json"
    p.write_text(json.dumps({"params": vars(args), "results": results}, indent=1, default=float))
    print(f"\n-> {p}")


if __name__ == "__main__":
    main()
