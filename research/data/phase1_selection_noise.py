"""Phase 1d: is DriftJumpAlpha's Crash 1000 result an edge, or the best of 285 coin flips?

Report 16 scored 285 cells (broker x symbol x strategy x R:R) and reported the
winner. Report 18 then walk-forward-tested the winners. That sequence has a known
failure mode: the maximum of many noisy estimates is biased upward, and a
walk-forward on a cell *selected using the full window* inherits the bias.

Phase 1a established there is no drift edge in this universe (every symbol passes
an Ito-corrected martingale test on 5.5-7.7 years), and optional stopping says no
stop/target geometry can manufacture one. So the null here is not a strawman - it
is what the physics of these instruments predicts.

This computes a t-statistic for every cell and asks where DriftJumpAlpha's actually
sits in that distribution. For a fixed-RR bracket the per-trade variance follows
from the win rate, so sd is derived rather than assumed:

    outcomes {+rr, -1} with P(win)=w  ->  var = w*rr^2 + (1-w) - mean^2
"""
from __future__ import annotations

import json
import math
from pathlib import Path

RESDIR = Path(__file__).resolve().parent / "results_full"   # report 16, 285 cells


def main() -> None:
    rows = []
    cells = {}
    for f in sorted(RESDIR.glob("*.json")):
        c = json.loads(f.read_text())
        cells[f"{c['broker']}|{c['symbol']}|{c['strategy']}"] = c
    print(f"loaded {len(cells)} cells from {RESDIR.name}")
    for key, cell in cells.items():
        for rr_s, m in (cell.get("rr") or {}).items():
            n = m.get("trades") or 0
            if n < 30:
                continue
            rr = float(rr_s)
            w = m.get("wr") or 0.0
            exp_r = m.get("exp_r")
            if exp_r is None:
                continue
            mean = w * rr - (1 - w)
            var = w * rr * rr + (1 - w) - mean * mean
            sd = math.sqrt(max(var, 1e-9))
            t = exp_r / (sd / math.sqrt(n))
            rows.append((t, exp_r, n, rr, key))

    rows.sort(reverse=True)
    tot = len(rows)
    print(f"cells with n>=30: {tot}")
    print(f"  t > 2 : {sum(1 for r in rows if r[0] > 2):4d}  "
          f"(expected under null ~{tot*0.0228:.1f})")
    print(f"  t > 3 : {sum(1 for r in rows if r[0] > 3):4d}  "
          f"(expected under null ~{tot*0.00135:.1f})")
    print(f"  t < -2: {sum(1 for r in rows if r[0] < -2):4d}")
    print(f"\nmean t across all cells: {sum(r[0] for r in rows)/tot:+.3f}"
          f"   (a fair book with costs sits BELOW zero)")

    print("\n--- top 12 cells by t ---")
    print(f"{'t':>7s}{'exp_r':>9s}{'n':>6s}{'rr':>5s}  cell")
    for t, e, n, rr, k in rows[:12]:
        print(f"{t:7.2f}{e:9.4f}{n:6d}{rr:5.1f}  {k}")

    print("\n--- every DriftJumpAlpha cell ---")
    print(f"{'t':>7s}{'exp_r':>9s}{'n':>6s}{'rr':>5s}  cell")
    dja = [r for r in rows if "riftJump" in r[4]]
    for t, e, n, rr, k in dja:
        rank = rows.index((t, e, n, rr, k)) + 1
        print(f"{t:7.2f}{e:9.4f}{n:6d}{rr:5.1f}  {k}   (rank {rank}/{tot})")

    # the selection benchmark
    import random
    random.seed(0)
    trials = 20000
    mx = sorted(max(random.gauss(0, 1) for _ in range(tot)) for _ in range(trials // 40))
    print(f"\nUnder the null, the LARGEST t among {tot} cells is typically:")
    print(f"  median {mx[len(mx)//2]:.2f}   95th pct {mx[int(len(mx)*0.95)]:.2f}   "
          f"max seen {mx[-1]:.2f}")
    if dja:
        best = max(r[0] for r in dja)
        frac = sum(1 for m in mx if m >= best) / len(mx)
        print(f"  best DriftJumpAlpha t = {best:.2f}")
        print(f"  P(some cell reaches this t by chance alone) = {frac:.1%}")


if __name__ == "__main__":
    main()
