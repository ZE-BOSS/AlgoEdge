"""Does the cash-index series reproduce the tradeable CFD series?

This gate decides whether 40 years of cash-index history can be used to judge a
strategy that will be executed on CFDs.

The concern is specific and well known. A cash-index *open* is a computed level:
on many indices it is assembled from staggered constituent opens, and any
constituent that has not yet traded contributes its previous close. The printed
open is therefore partly yesterday's information, which mechanically manufactures
open-to-close drift that nobody could have traded. Deriv's CFD open is a live
quote with no such construction.

If the two disagree over the window where both exist, the long history is
measuring an artifact of index construction and must be discarded for this
purpose. If they agree, it is measuring the same phenomenon and 40 years of it can
settle the question that 2.6 years cannot.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
CASH = HERE / "bars_cash"
CFD = HERE / "bars_live"
PAIRS = ["US_SP_500", "US_Tech_100", "Germany_40", "UK_100", "Japan_225",
         "Hong_Kong_50", "Netherlands_25", "France_40", "Australia_200"]


def legs(f: Path, spread: float = 0.0):
    z = np.load(f, allow_pickle=True)
    t, o, c = z["t"].astype(np.int64), z["o"].astype(float), z["c"].astype(float)
    cc = np.diff(np.log(c))
    oc = np.log(c / o)[1:]
    sig = -np.sign(cc[:-1])
    return (t[2:] // 86400), sig * oc[1:] - spread


def cfd_spread(name: str) -> float:
    z = np.load(CFD / f"{name}_D1.npz", allow_pickle=True)
    c = z["c"].astype(float)
    return float(np.median(z["spread"].astype(float) * float(z["meta"][2]) / c))


def main() -> None:
    print("Strategy: yesterday's close-to-close sign, traded open->close.")
    print("Restricted to the window where BOTH series exist.\n")
    print(f"{'index':18s}{'days':>6s}{'cash %/day':>12s}{'cfd %/day':>12s}"
          f"{'corr':>8s}{'  agreement'}")
    print("-" * 78)
    rows = []
    for name in PAIRS:
        fc, fl = CASH / f"{name}_D1.npz", CFD / f"{name}_D1.npz"
        if not (fc.exists() and fl.exists()):
            print(f"{name:18s}  missing")
            continue
        sp = cfd_spread(name)
        dc, vc = legs(fc, sp)
        dl, vl = legs(fl, sp)
        common = sorted(set(dc.tolist()) & set(dl.tolist()))
        if len(common) < 100:
            print(f"{name:18s}  only {len(common)} shared days")
            continue
        mc = {d: x for d, x in zip(dc.tolist(), vc)}
        ml = {d: x for d, x in zip(dl.tolist(), vl)}
        a = np.array([mc[d] for d in common])
        b = np.array([ml[d] for d in common])
        r = float(np.corrcoef(a, b)[0, 1])
        agree = ("strong" if r > 0.8 else "partial" if r > 0.5 else "POOR")
        rows.append((name, len(common), a.mean(), b.mean(), r))
        print(f"{name:18s}{len(common):6d}{a.mean()*100:12.4f}{b.mean()*100:12.4f}"
              f"{r:8.3f}  {agree}")

    print("-" * 78)
    if rows:
        rr = np.array([x[4] for x in rows])
        ca = np.array([x[2] for x in rows])
        cf = np.array([x[3] for x in rows])
        print(f"\nmean correlation of daily strategy returns: {rr.mean():.3f}")
        print(f"mean expectancy   cash {ca.mean()*100:+.4f}%/day   "
              f"cfd {cf.mean()*100:+.4f}%/day   ratio {ca.mean()/cf.mean():.2f}x")
        print(f"indices where cash and cfd agree in SIGN: "
              f"{int(np.sum(np.sign(ca) == np.sign(cf)))}/{len(rows)}")
        if rr.mean() > 0.8 and abs(ca.mean() / cf.mean() - 1) < 0.5:
            print("\n=> The cash series tracks the tradeable series. Long history is")
            print("   usable for judging this effect.")
        elif ca.mean() / cf.mean() > 1.5:
            print("\n=> Cash OVERSTATES the effect versus the tradeable series. The gap")
            print("   is the stale-open component. Long history can still test whether")
            print("   the phenomenon exists, but not its tradeable magnitude.")
        else:
            print("\n=> The two disagree. The cash history is measuring something the")
            print("   CFD cannot trade; do not use it to size this strategy.")


if __name__ == "__main__":
    main()
