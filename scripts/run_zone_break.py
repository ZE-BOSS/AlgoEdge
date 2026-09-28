#!/usr/bin/env python
"""
scripts/run_zone_break.py

The OTHER half of the round-number literature.

Osler's order-flow work makes two predictions, not one:
  (1) price reverses AT round numbers  (take-profit orders cluster just before)
  (2) price trends RAPIDLY once it crosses one (stop-loss cascades)

run_zone_study.py tests (1) and finds nothing — round levels are broken slightly
MORE often than controls, which is what (2) would look like from the bounce
side. This script tests (2) on its own terms:

  event     a clean break of a level (the previous `approach` bars are all on
            one side, this bar closes past it by `break_frac` * step)
  measure   how far price runs IN THE BREAK DIRECTION over the next `horizon`
            bars, and whether it reaches `target_frac` * step before giving
            back `stop_frac` * step

and compares round levels against the same non-round control grid.

If (2) held, round breaks would run further than control breaks. Same control,
same spacing, same market, same bars — only roundness differs.

    python scripts/run_zone_break.py --since 2026-01-01
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

from scripts.run_zone_study import (  # noqa: E402
    CONTROL_OFFSETS, FX, SYNTH, OUT, load, pick_steps, rolling_max, rolling_min, z_two_prop,
)


def breaks(b: dict, step: float, offset: float, approach: int, horizon: int,
           break_frac: float, target_frac: float, stop_frac: float) -> dict:
    high, low, close = b["high"], b["low"], b["close"]
    n = len(high)
    if n < approach + horizon + 10:
        return {"n": 0, "wins": 0, "losses": 0, "runs": []}

    prev_max = rolling_max(high, approach)
    prev_min = rolling_min(low, approach)

    wins = losses = 0
    runs: list[float] = []

    with np.errstate(invalid="ignore"):
        lvl_up = (np.floor(prev_max / step - offset) + 1 + offset) * step
        lvl_dn = (np.ceil(prev_min / step - offset) - 1 + offset) * step

    up_break = np.flatnonzero(np.isfinite(lvl_up) & (close >= lvl_up + break_frac * step))
    dn_break = np.flatnonzero(np.isfinite(lvl_dn) & (close <= lvl_dn - break_frac * step))

    for idx, lvl, up in ((up_break, lvl_up, True), (dn_break, lvl_dn, False)):
        last = None
        last_i = -10 ** 9
        for i in idx:
            if i + horizon >= n:
                break
            L = lvl[i]
            if last is not None and abs(L - last) < 1e-12 and i - last_i < horizon:
                continue
            last, last_i = L, i

            entry = close[i]                      # you can only trade the close of the break bar
            h = high[i + 1: i + 1 + horizon]
            lo_ = low[i + 1: i + 1 + horizon]
            if up:
                tgt = np.flatnonzero(h >= entry + target_frac * step)
                stp = np.flatnonzero(lo_ <= entry - stop_frac * step)
                runs.append(float(np.max(h) - entry) / step)
            else:
                tgt = np.flatnonzero(lo_ <= entry - target_frac * step)
                stp = np.flatnonzero(h >= entry + stop_frac * step)
                runs.append(float(entry - np.min(lo_)) / step)

            t0 = tgt[0] if tgt.size else 10 ** 9
            s0 = stp[0] if stp.size else 10 ** 9
            if t0 == s0 == 10 ** 9:
                continue
            if t0 < s0:
                wins += 1
            else:
                losses += 1

    return {"n": wins + losses, "wins": wins, "losses": losses, "runs": runs}


def study(sym: str, since: str | None, **kw) -> list[dict]:
    b = load(sym)
    if since:
        t0 = int(datetime.fromisoformat(since).replace(tzinfo=timezone.utc).timestamp())
        m = b["time"] >= t0
        b = {k: (v[m] if isinstance(v, np.ndarray) else v) for k, v in b.items()}

    rows = []
    for step in pick_steps(b):
        r = breaks(b, step, 0.0, **kw)
        c = {"n": 0, "wins": 0, "losses": 0, "runs": []}
        for f in CONTROL_OFFSETS:
            x = breaks(b, step, f, **kw)
            c["n"] += x["n"]
            c["wins"] += x["wins"]
            c["losses"] += x["losses"]
            c["runs"] += x["runs"]
        rows.append({
            "symbol": sym, "step": step,
            "round_n": r["n"],
            "round_hit": (r["wins"] / r["n"]) if r["n"] else float("nan"),
            "round_run": float(np.mean(r["runs"])) if r["runs"] else float("nan"),
            "ctrl_n": c["n"],
            "ctrl_hit": (c["wins"] / c["n"]) if c["n"] else float("nan"),
            "ctrl_run": float(np.mean(c["runs"])) if c["runs"] else float("nan"),
            "z": z_two_prop(r["wins"], r["n"], c["wins"], c["n"]),
        })
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default=None)
    ap.add_argument("--approach", type=int, default=12)
    ap.add_argument("--horizon", type=int, default=48)
    ap.add_argument("--break-frac", type=float, default=0.05, help="close past the level by this x step")
    ap.add_argument("--target-frac", type=float, default=0.50)
    ap.add_argument("--stop-frac", type=float, default=0.25)
    ap.add_argument("--markets", nargs="*", default=None)
    ap.add_argument("--tag", default="break")
    args = ap.parse_args()

    kw = dict(approach=args.approach, horizon=args.horizon, break_frac=args.break_frac,
              target_frac=args.target_frac, stop_frac=args.stop_frac)

    rows: list[dict] = []
    for sym in (args.markets or (FX + SYNTH)):
        try:
            rows += study(sym, args.since, **kw)
        except FileNotFoundError as e:
            print(f"skip {e}")

    hdr = (f"{'market':22s} {'step':>9s} {'N':>6s} {'round hit':>10s} {'ctrl hit':>9s} "
           f"{'diff':>7s} {'z':>6s} {'run R':>7s} {'run C':>7s}")
    print(f"\nbreak continuation | window {args.since or 'all'} | target {args.target_frac}xstep "
          f"vs stop {args.stop_frac}xstep in {args.horizon} bars")
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        d = (r["round_hit"] - r["ctrl_hit"]) * 100
        print(f"{r['symbol']:22s} {r['step']:>9g} {r['round_n']:>6d} {r['round_hit'] * 100:>9.1f}% "
              f"{r['ctrl_hit'] * 100:>8.1f}% {d:>+6.1f}% {r['z']:>+6.2f} "
              f"{r['round_run']:>7.3f} {r['ctrl_run']:>7.3f}")

    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / f"zone_break_{args.tag}.json"
    p.write_text(json.dumps({"params": vars(args), "rows": rows}, indent=1))
    print(f"\n-> {p}")


if __name__ == "__main__":
    main()
