#!/usr/bin/env python
"""
scripts/run_zone_study.py

Does price react at ROUND NUMBERS more than at ordinary prices?

THE ONLY QUESTION THAT MATTERS
------------------------------
Price reverses at some level all the time — that is what price does. So
"price reversed at 1.1000" is not evidence of anything until you can show it
reverses there MORE than at 1.10237. This study therefore measures every round
level against controls at the SAME SPACING, differing only in roundness.

  round grid      L = k * S                     (S = 1.00, 0.50, 0.0100, ...)
  control grids   L = (k + f) * S   for f in CONTROL_OFFSETS

The offsets avoid .25 / .50 / .75, which are themselves psychological.

EVENT
-----
A clean first touch, with a direction:
  from below   the previous `approach` bars are ALL under L, and this bar's
               high reaches L
  from above   mirror image
One candidate level per bar per direction (the nearest unbroken one), so an
event cannot be double counted, and chop around a level cannot manufacture
dozens of events.

OUTCOME (a race, which is how a trade actually resolves)
-------
From L, over the next `horizon` bars, which happens first:
  REJECT       price retreats T back the way it came
  THROUGH      price continues T past the level
  unresolved   neither within the horizon
T is a fraction of the grid spacing (0.25 * S), so the test is scale free and
identical for round and control grids.

    reversal rate = REJECT / (REJECT + THROUGH)

50% is the null. The statistic reported is round minus control, with a
two-proportion z-test.

    python scripts/run_zone_study.py --since 2026-01-01
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

BARS_DIRS = [ROOT / "data" / "zone_study" / "bars", ROOT / "data" / "edge_lab" / "bars"]
OUT = ROOT / "data" / "zone_study"

# Non-round on purpose: .25/.50/.75 of a round step are themselves round.
CONTROL_OFFSETS = (0.17, 0.31, 0.43, 0.57, 0.69, 0.83)

# The "human" ladder of round numbers. A market's grids are the members of this
# ladder that sit in a sensible band around its own daily range (see pick_steps).
LADDER = [10 ** e * m for e in range(-5, 6) for m in (1, 2.5, 5)]


def slug(sym: str) -> str:
    return sym.replace(" ", "_")


def load(sym: str) -> dict:
    for d in BARS_DIRS:
        p = d / f"{slug(sym)}.npz"
        if p.exists():
            z = np.load(p)
            return {
                "time": z["time"].astype(np.int64),
                "open": z["open"].astype(float),
                "high": z["high"].astype(float),
                "low": z["low"].astype(float),
                "close": z["close"].astype(float),
                "spread_pts": z["spread"].astype(float),
                "point": float(z["point"]),
            }
    raise FileNotFoundError(f"no cached bars for {sym}")


def rolling_max(a: np.ndarray, w: int) -> np.ndarray:
    """max of the w values ENDING at i-1 (i.e. strictly before bar i)."""
    out = np.full(a.shape, -np.inf)
    if len(a) > w:
        sw = np.lib.stride_tricks.sliding_window_view(a, w)
        out[w:] = sw.max(axis=1)[:-1]
    return out


def rolling_min(a: np.ndarray, w: int) -> np.ndarray:
    out = np.full(a.shape, np.inf)
    if len(a) > w:
        sw = np.lib.stride_tricks.sliding_window_view(a, w)
        out[w:] = sw.min(axis=1)[:-1]
    return out


def daily_range(b: dict) -> float:
    """Median day's high-low, the scale a 'level' has to be meaningful against."""
    day = b["time"] // 86400
    edges = np.flatnonzero(np.diff(day)) + 1
    highs = np.maximum.reduceat(b["high"], np.r_[0, edges])
    lows = np.minimum.reduceat(b["low"], np.r_[0, edges])
    return float(np.median(highs - lows))


def pick_steps(b: dict, n: int = 3) -> list[float]:
    """Round steps worth testing: from ~0.5x the daily range up to ~5x it.

    Below that a 'level' is hit constantly and means nothing; above it there are
    too few touches in a year to say anything.
    """
    dr = daily_range(b)
    lo, hi = 0.4 * dr, 6.0 * dr
    steps = [s for s in LADDER if lo <= s <= hi]
    if not steps:
        steps = [min(LADDER, key=lambda s: abs(math.log(s / max(dr, 1e-12))))]
    return sorted(steps)[:n]


def events(b: dict, step: float, offset: float, approach: int, horizon: int,
           thresh_frac: float) -> tuple[int, int, int, float]:
    """(rejects, throughs, unresolved, mean |excursion| in units of step)."""
    high, low = b["high"], b["low"]
    n = len(high)
    if n < approach + horizon + 10:
        return 0, 0, 0, 0.0

    prev_max = rolling_max(high, approach)
    prev_min = rolling_min(low, approach)
    T = thresh_frac * step

    rej = thr = uns = 0
    exc: list[float] = []

    # The nearest grid level strictly above everything the last `approach` bars
    # printed — the one an up-move meets first.
    with np.errstate(invalid="ignore"):
        lvl_up = (np.floor(prev_max / step - offset) + 1 + offset) * step
        lvl_dn = (np.ceil(prev_min / step - offset) - 1 + offset) * step

    hit_up = np.flatnonzero((high >= lvl_up) & np.isfinite(lvl_up))
    hit_dn = np.flatnonzero((low <= lvl_dn) & np.isfinite(lvl_dn))

    for idx, lvl, up in ((hit_up, lvl_up, True), (hit_dn, lvl_dn, False)):
        last_lvl = None
        last_i = -10 ** 9
        for i in idx:
            if i + horizon >= n:
                break
            L = lvl[i]
            # one event per level per approach: skip a re-touch of the same
            # level while it is still the active one
            if last_lvl is not None and abs(L - last_lvl) < 1e-12 and i - last_i < horizon:
                continue
            last_lvl, last_i = L, i

            h = high[i + 1: i + 1 + horizon]
            lo_ = low[i + 1: i + 1 + horizon]
            if up:
                through = np.flatnonzero(h >= L + T)
                reject = np.flatnonzero(lo_ <= L - T)
                far = float(np.max(np.abs(np.r_[h, lo_] - L))) / step
            else:
                through = np.flatnonzero(lo_ <= L - T)
                reject = np.flatnonzero(h >= L + T)
                far = float(np.max(np.abs(np.r_[h, lo_] - L))) / step

            t0 = through[0] if through.size else 10 ** 9
            r0 = reject[0] if reject.size else 10 ** 9
            if t0 == r0 == 10 ** 9:
                uns += 1
            elif r0 < t0:
                rej += 1
            else:
                thr += 1
            exc.append(far)

    return rej, thr, uns, (float(np.mean(exc)) if exc else 0.0)


def z_two_prop(x1: int, n1: int, x2: int, n2: int) -> float:
    if n1 == 0 or n2 == 0:
        return 0.0
    p1, p2 = x1 / n1, x2 / n2
    p = (x1 + x2) / (n1 + n2)
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    return (p1 - p2) / se if se > 0 else 0.0


def study(sym: str, since: str | None, approach: int, horizon: int,
          thresh_frac: float) -> list[dict]:
    b = load(sym)
    if since:
        t0 = int(datetime.fromisoformat(since).replace(tzinfo=timezone.utc).timestamp())
        m = b["time"] >= t0
        b = {k: (v[m] if isinstance(v, np.ndarray) else v) for k, v in b.items()}
    rows = []
    for step in pick_steps(b):
        r_rej, r_thr, r_uns, r_exc = events(b, step, 0.0, approach, horizon, thresh_frac)
        c_rej = c_thr = c_uns = 0
        c_exc = []
        for f in CONTROL_OFFSETS:
            a, c, u, e = events(b, step, f, approach, horizon, thresh_frac)
            c_rej += a
            c_thr += c
            c_uns += u
            if e:
                c_exc.append(e)
        rows.append({
            "symbol": sym,
            "step": step,
            "bars": int(len(b["high"])),
            "round_events": r_rej + r_thr,
            "round_reversal": (r_rej / (r_rej + r_thr)) if (r_rej + r_thr) else float("nan"),
            "round_excursion": r_exc,
            "ctrl_events": c_rej + c_thr,
            "ctrl_reversal": (c_rej / (c_rej + c_thr)) if (c_rej + c_thr) else float("nan"),
            "ctrl_excursion": float(np.mean(c_exc)) if c_exc else float("nan"),
            "z": z_two_prop(r_rej, r_rej + r_thr, c_rej, c_rej + c_thr),
        })
    return rows


FX = ["EURUSD", "GBPUSD", "USDJPY", "GBPJPY", "XAUUSD", "XAGUSD",
      "BTCUSD", "US Tech 100", "US SP 500", "Germany 40"]
SYNTH = ["Step Index", "Jump 25 Index", "Jump 75 Index", "Jump 100 Index",
         "Crash 900 Index", "Crash 1000 Index", "Boom 900 Index", "Boom 1000 Index",
         "Volatility 75 Index", "Volatility 100 Index"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default=None, help="ISO date, e.g. 2026-01-01")
    ap.add_argument("--approach", type=int, default=12, help="bars that must be clear of the level")
    ap.add_argument("--horizon", type=int, default=48, help="bars to resolve the race in")
    ap.add_argument("--thresh", type=float, default=0.25, help="race distance, as a fraction of the step")
    ap.add_argument("--markets", nargs="*", default=None)
    ap.add_argument("--tag", default="main")
    args = ap.parse_args()

    markets = args.markets or (FX + SYNTH)
    rows: list[dict] = []
    for sym in markets:
        try:
            rows += study(sym, args.since, args.approach, args.horizon, args.thresh)
        except FileNotFoundError as e:
            print(f"skip {e}")

    hdr = f"{'market':22s} {'step':>10s} {'N':>7s} {'round%':>7s} {'ctrl%':>7s} {'diff':>7s} {'z':>7s}"
    print(f"\nwindow: {args.since or 'all'} | approach {args.approach} bars | "
          f"horizon {args.horizon} bars | race {args.thresh}xstep")
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        d = (r["round_reversal"] - r["ctrl_reversal"]) * 100
        print(f"{r['symbol']:22s} {r['step']:>10g} {r['round_events']:>7d} "
              f"{r['round_reversal'] * 100:>6.1f}% {r['ctrl_reversal'] * 100:>6.1f}% "
              f"{d:>+6.1f}% {r['z']:>+7.2f}")

    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"zone_stats_{args.tag}.json"
    path.write_text(json.dumps({"params": vars(args), "rows": rows}, indent=1))
    print(f"\n-> {path}")


if __name__ == "__main__":
    main()
