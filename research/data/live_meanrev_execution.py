"""Is the index reversal a close-print artifact, or a real move you can trade?

Report 21 section 3.3: the effect dies at ~5.9 bp of slippage and the rule trades
at the daily close, which is exactly where a real fill is hardest to get. Negative
daily autocorrelation is also the classic signature of a stale or non-synchronous
price rather than an opportunity.

The decisive, cheap test is to move the entry off the close and see what survives.
A signal computed from yesterday's close is known before today's open, so entering
at the open is entirely legitimate - and if the effect only exists between one
close and the next, it lives inside the close print and cannot be traded.

Three variants on the same signal (yesterday down -> long today):

  close -> close   the original. Contaminated by whatever the close print does.
  open  -> close   pure intraday. Signal known in advance, both fills away from
                   the close. If the effect is real, it should show up here.
  close -> open    pure overnight. This is where a close-print artifact would
                   concentrate, because it reverses at the very next quote.

Short-term reversal is known to live mostly in one leg or the other, so the split
also decides whether a CFD with limited hours can capture it at all.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

BARS = Path(__file__).resolve().parent / "bars_live"
INDICES = ["US_SP_500", "US_Tech_100", "Germany_40", "UK_100", "Japan_225",
           "Hong_Kong_50", "Netherlands_25", "France_40", "Australia_200"]


def load(name: str):
    f = BARS / f"{name}_D1.npz"
    if not f.exists():
        return None
    z = np.load(f, allow_pickle=True)
    return (z["t"].astype(np.int64), z["o"].astype(float), z["c"].astype(float),
            float(np.median(z["spread"].astype(float) * float(z["meta"][2])
                            / z["c"].astype(float))))


def build(name):
    d = load(name)
    if d is None:
        return None
    t, o, c, sp = d
    prev_ret = np.diff(np.log(c))            # close[i] -> close[i+1]
    sig = -np.sign(prev_ret[:-1])            # known before day i+2 opens
    day = t[2:] // 86400
    cc = np.log(c[2:] / c[1:-1])             # close -> next close
    oc = np.log(c[2:] / o[2:])               # open  -> close (intraday)
    co = np.log(o[2:] / c[1:-1])             # prev close -> open (overnight)
    return day, sig, cc, oc, co, sp


def portfolio(rows, key: int, sp_mult: float = 1.0):
    """Equal-weight, date-aligned portfolio of one leg across all indices."""
    days = sorted(set.intersection(*[set(r[0].tolist()) for r in rows]))
    idx = {d: i for i, d in enumerate(days)}
    M = np.full((len(days), len(rows)), np.nan)
    for j, r in enumerate(rows):
        leg = r[key]
        for d, s, x in zip(r[0].tolist(), r[1], leg):
            if d in idx:
                M[idx[d], j] = s * x - r[5] * sp_mult
    p = np.nanmean(M, axis=1)
    return p[np.isfinite(p)]


def report(label, p):
    se = p.std(ddof=1) / np.sqrt(len(p))
    t = p.mean() / se if se else np.nan
    print(f"  {label:26s} n={len(p):4d}  {p.mean()*100:+8.4f}%/day  "
          f"t {t:+6.2f}  ann {p.mean()*252*100:+7.1f}%  "
          f"Sharpe {p.mean()/p.std(ddof=1)*np.sqrt(252):+5.2f}")
    return t


def main() -> None:
    rows = [r for r in (build(n) for n in INDICES) if r is not None]
    print(f"{len(rows)} indices\n")
    print("--- the same signal, executed three different ways ---")
    t_cc = report("close -> close (original)", portfolio(rows, 2))
    t_oc = report("open  -> close (intraday)", portfolio(rows, 3))
    t_co = report("close -> open  (overnight)", portfolio(rows, 4))

    print("\n--- reading ---")
    if t_oc > 2:
        print("  The intraday leg carries the effect on its own. The signal is known")
        print("  before the open and both fills sit away from the close print, so this")
        print("  is a tradeable move rather than a measurement artifact.")
    elif t_co > 2 and t_oc < 1:
        print("  The effect is ENTIRELY overnight - it reverses at the very next quote")
        print("  after the close that generated the signal. That is what a stale or")
        print("  non-synchronous close print looks like, and it is not tradeable:")
        print("  capturing it requires transacting at the close price itself.")
    else:
        print("  Neither leg is individually significant. The close-to-close result")
        print("  rests on the two legs combined, which is weak evidence for an effect")
        print("  that has to be executed one leg at a time.")

    print("\n--- per-index intraday leg (the tradeable one) ---")
    print(f"  {'symbol':17s}{'n':>6s}{'mean%/day':>12s}{'t':>8s}{'ann%':>9s}")
    for name, r in zip(INDICES, rows):
        v = r[1] * r[3] - r[5]
        se = v.std(ddof=1) / np.sqrt(len(v))
        print(f"  {name[:16]:17s}{len(v):6d}{v.mean()*100:12.4f}"
              f"{v.mean()/se if se else np.nan:8.2f}{v.mean()*252*100:9.1f}")


if __name__ == "__main__":
    main()
