"""Phase 1f: the control that decides whether DJA's entries carry information.

The tick replay (phase1_dja_tick_replay.py) says DJA's 340 Crash 1000 entry
timestamps, traded as a plain long bracket with realistic fills, return +0.485 R.
Optional stopping says that is impossible on a martingale. Two explanations
remain, and this separates them:

  random entries also return ~+0.485 R
      -> the geometry itself pays, my martingale reading is wrong, and DJA's
         entry logic is contributing nothing
  random entries return ~ -spread/stop
      -> the geometry is fair as theory says, and DJA's entry TIMES are
         informative - which on a martingale means look-ahead in signal
         generation, not edge

Same instrument, same stop distance, same 5 R target, same fill rules, same
direction. Only the entry times change. Trades are drawn non-overlapping as well
as overlapping, because overlapping trades share price path and make the naive
standard error far too small.
"""
from __future__ import annotations

import numpy as np
from pathlib import Path

TICKS = Path(__file__).resolve().parent / "ticks"
STOP_PCT = 0.3472 / 100      # DJA's median stop, measured from algoedge.db
RR = 5.0
SPREAD_PCT = 0.00099 / 100
MAX_LOOK = 3_000_000


def bracket(P: np.ndarray, i: int, long: bool) -> float | None:
    """One trade in R, with a market-order stop and a limit-order target."""
    half = SPREAD_PCT / 2
    mid = P[i]
    entry = mid * (1 + half) if long else mid * (1 - half)
    risk = entry * STOP_PCT
    sl = entry - risk if long else entry + risk
    tp = entry + RR * risk if long else entry - RR * risk
    seg = P[i + 1:min(i + 1 + MAX_LOOK, len(P))]
    if seg.size == 0:
        return None
    a = np.flatnonzero(seg <= sl) if long else np.flatnonzero(seg >= sl)
    b = np.flatnonzero(seg >= tp) if long else np.flatnonzero(seg <= tp)
    ja = a[0] if a.size else np.inf
    jb = b[0] if b.size else np.inf
    if ja == np.inf and jb == np.inf:
        return None
    if ja < jb:                                   # stop: fills where it lands
        raw = float(seg[int(ja)])
        fill = raw * (1 - half) if long else raw * (1 + half)
    else:                                         # target: limit, no improvement
        fill = tp * (1 - half) if long else tp * (1 + half)
    return (fill - entry) / risk if long else (entry - fill) / risk


def run(code: str) -> None:
    f = sorted(TICKS.glob(f"{code}_*d.npz"), key=lambda x: x.stat().st_size)[-1]
    z = np.load(f, allow_pickle=True)
    P = z["p"]
    print(f"\n{'='*84}\n{code}: {len(P):,} ticks | stop {STOP_PCT*100:.4f}% | "
          f"target {RR}R | spread {SPREAD_PCT*100:.5f}%\n{'='*84}")
    fair = -SPREAD_PCT / STOP_PCT
    print(f"  martingale prediction (clean fills): {fair:+.4f} R\n")

    rng = np.random.default_rng(7)
    for side, long in (("long", True), ("short", False)):
        # ---- overlapping: many entries, correlated, SE understated
        idx = np.sort(rng.choice(len(P) - 2, size=4000, replace=False))
        v = [bracket(P, int(i), long) for i in idx]
        v = np.array([x for x in v if x is not None])

        # ---- non-overlapping: next entry only after the previous trade closed
        seq, i = [], 0
        while i < len(P) - 2 and len(seq) < 4000:
            r = bracket(P, i, long)
            if r is None:
                break
            seq.append(r)
            # advance past this trade: find where it actually closed
            half = SPREAD_PCT / 2
            mid = P[i]
            entry = mid * (1 + half) if long else mid * (1 - half)
            risk = entry * STOP_PCT
            sl = entry - risk if long else entry + risk
            tp = entry + RR * risk if long else entry - RR * risk
            seg = P[i + 1:min(i + 1 + MAX_LOOK, len(P))]
            a = np.flatnonzero(seg <= sl) if long else np.flatnonzero(seg >= sl)
            b = np.flatnonzero(seg >= tp) if long else np.flatnonzero(seg <= tp)
            ja = a[0] if a.size else len(seg)
            jb = b[0] if b.size else len(seg)
            i += int(min(ja, jb)) + 2
        s = np.asarray(seq)

        for label, arr in (("overlapping", v), ("NON-overlapping", s)):
            if len(arr) < 30:
                continue
            se = arr.std() / np.sqrt(len(arr))
            print(f"  {side:5s} {label:16s} n={len(arr):5d}  win {(arr>0).mean():6.2%}  "
                  f"exp {arr.mean():+.4f} R  SE {se:.4f}  t vs fair "
                  f"{(arr.mean()-fair)/se:+6.2f}")


if __name__ == "__main__":
    import sys
    for c in (sys.argv[1:] or ["CRASH1000"]):
        run(c)
