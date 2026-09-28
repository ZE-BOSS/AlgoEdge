#!/usr/bin/env python
"""
scripts/run_prior_zone_study.py

Does price respect a level where it ALREADY reversed once?

This is the break-and-retest question, and it is a different claim from the
round-number one. A round number is a number. A prior zone is a place the market
demonstrably turned — there were real orders there, and the argument is that
some of them are still there, or that the memory of them draws new ones.

ZONE
----
A swing pivot: a bar whose high is the highest of the `pivot` bars either side
(resistance), or whose low is the lowest (support). That is a level price
actually reversed at. The zone is the pivot price with a tolerance band of
`band` x ATR, and it is only armed once the pivot is confirmed (`pivot` bars
after the fact — no lookahead).

RETEST
------
Price returns to the band after having been away from it by at least
`away` x ATR. First return only; a zone is retired after `max_tests` touches.

OUTCOME
-------
The same race as the round-number study: from the zone, does price first move
`T` back the way it came (HOLD — the zone did its job) or `T` through it
(BREAK)? T = `thresh` x ATR.

CONTROL
-------
The part that decides the study, and the easy thing to get wrong. A control
level must be RETESTED the same way the real zone is — not merely evaluated on
the same bar, because a level price has not reached cannot break, which scores
a fake 95% "hold".

So the control is a PLACEBO ZONE: the pivot price shifted by `shift` x ATR, put
through the identical pipeline — wait until price leaves by `away` x ATR, wait
for it to come back, run the same race. Same event definition, same bars, same
market. The only difference is that nothing ever reversed at the placebo price.

Also reported: zone AGE at the retest, and whether the hold rate decays with it.

    python scripts/run_prior_zone_study.py --since 2026-01-01
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

from scripts.run_zone_study import FX, SYNTH, OUT, load, z_two_prop  # noqa: E402


def atr(b: dict, period: int = 288) -> np.ndarray:
    h, lo, c = b["high"], b["low"], b["close"]
    pc = np.r_[c[0], c[:-1]]
    tr = np.maximum(h - lo, np.maximum(np.abs(h - pc), np.abs(lo - pc)))
    out = np.full(len(tr), np.nan)
    if len(tr) > period:
        csum = np.cumsum(tr)
        out[period:] = (csum[period:] - csum[:-period]) / period
    return out


def pivots(b: dict, k: int) -> tuple[np.ndarray, np.ndarray]:
    """Confirmed swing highs/lows. Index is the CONFIRMATION bar (pivot + k)."""
    h, lo = b["high"], b["low"]
    n = len(h)
    hi_idx, lo_idx = [], []
    if n < 2 * k + 2:
        return np.array(hi_idx, int), np.array(lo_idx, int)
    wh = np.lib.stride_tricks.sliding_window_view(h, 2 * k + 1)
    wl = np.lib.stride_tricks.sliding_window_view(lo, 2 * k + 1)
    centre = np.arange(k, n - k)
    is_hi = wh.argmax(axis=1) == k
    is_lo = wl.argmin(axis=1) == k
    return centre[is_hi], centre[is_lo]


def retests(b: dict, a: np.ndarray, levels: list[tuple[float, int]], band: float,
            away: float, thresh: float, horizon: int, max_tests: int) -> dict:
    """Run every level through the same retest-and-race pipeline."""
    h, lo, c, t = b["high"], b["low"], b["close"], b["time"]
    n = len(h)
    res = {"hold": 0, "break": 0, "ages": [], "by_age": defaultdict(lambda: [0, 0]),
           "events": []}

    for price, born in levels:
        if born + 2 >= n or not np.isfinite(a[born]):
            continue
        tol = band * a[born]
        j = born
        tests = 0
        while j < n - horizon - 1 and tests < max_tests:
            gone = np.flatnonzero(np.abs(c[j:n - horizon - 1] - price) > away * a[born])
            if not gone.size:
                break
            j = j + int(gone[0])
            back = np.flatnonzero((h[j:n - horizon - 1] >= price - tol) &
                                  (lo[j:n - horizon - 1] <= price + tol))
            if not back.size:
                break
            i = j + int(back[0])
            if i + horizon >= n or not np.isfinite(a[i]) or i < 1:
                break

            approached_up = c[i - 1] < price
            T = thresh * a[i]
            hh = h[i + 1: i + 1 + horizon]
            ll = lo[i + 1: i + 1 + horizon]
            up = np.flatnonzero(hh >= price + T)
            dn = np.flatnonzero(ll <= price - T)
            u = up[0] if up.size else 10 ** 9
            d = dn[0] if dn.size else 10 ** 9

            if u != 10 ** 9 or d != 10 ** 9:
                if approached_up:
                    out = "break" if u < d else "hold"
                else:
                    out = "break" if d < u else "hold"
                age_days = (t[i] - t[born]) / 86400.0
                res[out] += 1
                res["ages"].append(age_days)
                bucket = ("<1d" if age_days < 1 else "1-5d" if age_days < 5
                          else "5-20d" if age_days < 20 else ">20d")
                res["by_age"][bucket][0 if out == "hold" else 1] += 1
                res["events"].append({
                    "t": int(t[i]), "price": float(price), "age_days": age_days,
                    "outcome": out, "stop_distance": float(T),
                    "approached_up": bool(approached_up),
                })
            tests += 1
            j = i + horizon
    return res


def study(b: dict, pivot: int, band: float, away: float, thresh: float,
          horizon: int, shift: float, max_tests: int) -> dict:
    h, lo = b["high"], b["low"]
    a = atr(b)
    hi_p, lo_p = pivots(b, pivot)

    real: list[tuple[float, int]] = []
    placebo: list[tuple[float, int]] = []
    for i in hi_p:
        born = int(i + pivot)
        real.append((float(h[i]), born))
        if np.isfinite(a[born]):
            placebo.append((float(h[i]) + shift * a[born], born))
    for i in lo_p:
        born = int(i + pivot)
        real.append((float(lo[i]), born))
        if np.isfinite(a[born]):
            placebo.append((float(lo[i]) - shift * a[born], born))

    kw = dict(band=band, away=away, thresh=thresh, horizon=horizon, max_tests=max_tests)
    r = retests(b, a, real, **kw)
    c = retests(b, a, placebo, **kw)
    r["ctrl_hold"], r["ctrl_break"] = c["hold"], c["break"]
    return r


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default=None)
    ap.add_argument("--pivot", type=int, default=12, help="bars either side of a swing")
    ap.add_argument("--band", type=float, default=0.15, help="zone half-width, x ATR")
    ap.add_argument("--away", type=float, default=1.0, help="must leave by this, x ATR")
    ap.add_argument("--thresh", type=float, default=0.5, help="race distance, x ATR")
    ap.add_argument("--horizon", type=int, default=48)
    ap.add_argument("--shift", type=float, default=1.5, help="control offset, x ATR")
    ap.add_argument("--max-tests", type=int, default=3)
    ap.add_argument("--markets", nargs="*", default=None)
    ap.add_argument("--tag", default="prior")
    args = ap.parse_args()

    kw = dict(pivot=args.pivot, band=args.band, away=args.away, thresh=args.thresh,
              horizon=args.horizon, shift=args.shift, max_tests=args.max_tests)

    rows = []
    hdr = (f"{'market':22s} {'tests':>7s} {'hold%':>7s} {'ctrl%':>7s} {'diff':>7s} "
           f"{'z':>6s} {'age d':>7s}")
    print(f"\nPRIOR-ZONE RETEST | window {args.since or 'all'} | pivot {args.pivot} "
          f"| band {args.band}xATR | race {args.thresh}xATR in {args.horizon} bars")
    print(hdr)
    print("-" * len(hdr))

    dump = {}
    for sym in (args.markets or (FX + SYNTH)):
        try:
            b = load(sym)
        except FileNotFoundError as e:
            print(f"skip {e}")
            continue
        if args.since:
            t0 = int(datetime.fromisoformat(args.since).replace(tzinfo=timezone.utc).timestamp())
            m = b["time"] >= t0
            b = {k: (v[m] if isinstance(v, np.ndarray) else v) for k, v in b.items()}

        r = study(b, **kw)
        n = r["hold"] + r["break"]
        cn = r["ctrl_hold"] + r["ctrl_break"]
        if n < 20:
            continue
        hold = r["hold"] / n
        chold = r["ctrl_hold"] / cn if cn else float("nan")
        z = z_two_prop(r["hold"], n, r["ctrl_hold"], cn)
        print(f"{sym:22s} {n:>7d} {hold * 100:>6.1f}% {chold * 100:>6.1f}% "
              f"{(hold - chold) * 100:>+6.1f}% {z:>+6.2f} {np.mean(r['ages']):>7.1f}")
        rows.append({"symbol": sym, "tests": n, "hold": hold, "ctrl_hold": chold, "z": z,
                     "mean_age_days": float(np.mean(r["ages"])),
                     "by_age": {k: v for k, v in r["by_age"].items()}})
        dump[sym] = r["events"]

    print("\nHOLD RATE BY ZONE AGE")
    buckets = ["<1d", "1-5d", "5-20d", ">20d"]
    print(f"{'market':22s} " + " ".join(f"{b:>12s}" for b in buckets))
    for r in rows:
        cells = []
        for bkt in buckets:
            hb = r["by_age"].get(bkt)
            cells.append(f"{hb[0] / (hb[0] + hb[1]) * 100:>7.1f}%/{hb[0] + hb[1]:<4d}"
                         if hb and (hb[0] + hb[1]) > 5 else f"{'-':>12s}")
        print(f"{r['symbol']:22s} " + " ".join(cells))

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"prior_zone_{args.tag}.json").write_text(
        json.dumps({"params": vars(args), "rows": rows}, indent=1, default=float))
    (OUT / f"prior_zone_events_{args.tag}.json").write_text(json.dumps(dump, default=float))
    print(f"\n-> {OUT / f'prior_zone_{args.tag}.json'}")


if __name__ == "__main__":
    main()
