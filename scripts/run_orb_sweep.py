#!/usr/bin/env python
"""
scripts/run_orb_sweep.py

Why the published 5-minute ORB fails here, and at what settings it stops failing.

Zarattini & Aziz trade US equities, where the opening 5 minutes of a stock in
play is wide and the spread is a cent. On an FX or CFD feed the first 5-minute
bar can be 3 pips, and the spread is 1 — so the trade starts a third of R in the
hole before price moves. The sweep below varies the opening-range length (which
sets R) against the target, and also reports the cost-to-R ratio that explains
the result.

    python scripts/run_orb_sweep.py --feed deriv
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.run_published_strategies import (  # noqa: E402
    DERIV_MARKETS, FEEDS, FN_MARKETS, load, orb_trades, summarise,
)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--feed", default="deriv", choices=list(FEEDS))
    ap.add_argument("--since", default="2026-01-01")
    ap.add_argument("--slip-points", type=float, default=1.0)
    args = ap.parse_args()

    bars_dir, _ = FEEDS[args.feed]
    markets = FN_MARKETS if args.feed == "fundednext" else DERIV_MARKETS
    t0 = int(datetime.fromisoformat(args.since).replace(tzinfo=timezone.utc).timestamp())

    bars = {}
    for sym in markets:
        try:
            b = load(bars_dir, sym)
        except FileNotFoundError:
            continue
        m = b["time"] >= t0
        b = {k: (v[m] if isinstance(v, np.ndarray) else v) for k, v in b.items()}
        if len(b["time"]) >= 2000:
            bars[sym] = b

    ranges = [(1, "5m"), (3, "15m"), (6, "30m"), (12, "60m"), (24, "120m")]
    targets = [1, 2, 3, 5, 10]

    # 1. the diagnosis: how big is the cost relative to R at each range length?
    print(f"\nCOST AS A FRACTION OF R  ({args.feed} feed, "
          f"spread + {args.slip_points} pts over the opening-range stop)")
    print(f"  {'market':14s} " + " ".join(f"{lbl:>8s}" for _, lbl in ranges))
    for sym, b in bars.items():
        cells = []
        for ob, _ in ranges:
            tr = orb_trades(b, sym, 10.0, args.slip_points, ob)
            if len(tr) < 20:
                cells.append(f"{'-':>8s}")
                continue
            spread_px = b["spread_pts"] * b["point"]
            cost = float(np.median(spread_px)) + args.slip_points * b["point"]
            medR = float(np.median([t["stop_distance"] for t in tr]))
            cells.append(f"{cost / medR * 100:>7.1f}%")
        print(f"  {sym:14s} " + " ".join(cells))

    # 2. the sweep, in R per month for the whole portfolio
    print(f"\nPORTFOLIO R PER MONTH  (all {len(bars)} markets, every session)")
    print(f"  {'OR':>6s} " + " ".join(f"{('tgt ' + str(t) + 'R'):>10s}" for t in targets))
    best = None
    for ob, lbl in ranges:
        cells = []
        for tgt in targets:
            allt = []
            for sym, b in bars.items():
                allt += orb_trades(b, sym, tgt, args.slip_points, ob)
            s = summarise(allt, "x")
            if s.get("n", 0) > 20:
                rpm = s["r_per_month"]
                cells.append(f"{rpm:>+10.1f}")
                if best is None or rpm > best[0]:
                    best = (rpm, lbl, tgt, s)
            else:
                cells.append(f"{'-':>10s}")
        print(f"  {lbl:>6s} " + " ".join(cells))

    if best:
        rpm, lbl, tgt, s = best
        print(f"\n  best cell: {lbl} range, {tgt}R target -> {rpm:+.1f} R/month "
              f"({s['n']} trades, expectancy {s['expectancy_r']:+.3f}R, "
              f"win {s['win_rate'] * 100:.1f}%, t {s['t_stat']:+.2f})")
        print(f"  a prop challenge needs about +20 R/month.")

    (ROOT / "data" / "zone_study" / f"orb_sweep_{args.feed}.json").write_text(
        json.dumps({"best": {"r_per_month": best[0], "range": best[1], "target": best[2]}
                    if best else None}, indent=1, default=float))


if __name__ == "__main__":
    main()
