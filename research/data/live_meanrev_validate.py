"""Does the index reversal effect survive honest statistics?

live_meanrev_test.py: all nine indices positive gross and net, pooled +11.6%/yr
after costs, and costs are nowhere near binding (US SP 500 needs |rho| > 0.006 to
break even and measures -0.174). That is the first real candidate this research
has produced, which is exactly why it needs the harshest available test rather
than the most flattering one.

Three things it has to survive:

1. **Correct pooling.** Nine world equity indices over the same 2024-2026 window
   are not nine independent experiments - they are one market, sampled nine ways.
   Averaging their t-statistics would overstate the evidence by roughly sqrt(9).
   The right construction is a single equal-weight portfolio series aligned by
   date, whose own standard error already contains the cross-correlation.
2. **A split it was not discovered on.** The effect was found on the whole 2.6
   years, so the second half is not truly out of sample - but a collapse between
   halves would still be fatal, and consistency is weak positive evidence.
3. **Execution realism.** The rule trades at the daily close. Every real fill
   happens somewhere else, so the question is how much slippage the edge absorbs
   before it disappears.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import numpy as np

BARS = Path(__file__).resolve().parent / "bars_live"
INDICES = ["US_SP_500", "US_Tech_100", "Germany_40", "UK_100", "Japan_225",
           "Hong_Kong_50", "Netherlands_25", "France_40", "Australia_200"]


def series(name: str):
    f = BARS / f"{name}_D1.npz"
    if not f.exists():
        return None
    z = np.load(f, allow_pickle=True)
    c = z["c"].astype(float)
    t = z["t"].astype(np.int64)
    sp = float(np.median(z["spread"].astype(float) * float(z["meta"][2]) / c))
    r = np.diff(np.log(c))
    day = (t[1:] // 86400)
    sig = -np.sign(r[:-1])
    net = sig * r[1:] - sp
    return day[1:], net, sp


def tstat(x: np.ndarray) -> tuple[float, float]:
    se = x.std(ddof=1) / np.sqrt(len(x))
    return x.mean(), (x.mean() / se if se else np.nan)


def main() -> None:
    data = {n: series(n) for n in INDICES}
    data = {k: v for k, v in data.items() if v is not None}

    # ---- 1. correct pooling: one portfolio series, aligned by date
    days = sorted(set.intersection(*[set(v[0].tolist()) for v in data.values()]))
    print(f"common trading days across {len(data)} indices: {len(days)}")
    idx = {d: i for i, d in enumerate(days)}
    M = np.full((len(days), len(data)), np.nan)
    for j, (name, (dd, net, sp)) in enumerate(data.items()):
        for d, x in zip(dd.tolist(), net):
            if d in idx:
                M[idx[d], j] = x
    port = np.nanmean(M, axis=1)
    port = port[np.isfinite(port)]

    m, t = tstat(port)
    print("\n--- 1. equal-weight portfolio (correct pooling) ---")
    print(f"  n = {len(port)} days   mean {m*100:+.4f}%/day   t = {t:+.2f}")
    print(f"  annualised {m*252*100:+.1f}%   Sharpe {m/port.std(ddof=1)*np.sqrt(252):+.2f}")
    naive = np.mean([tstat(v[1])[1] for v in data.values()])
    print(f"  (mean of the nine individual t-stats was {naive:+.2f} - "
          f"pooling properly gives {t:+.2f})")
    C = np.corrcoef(np.nan_to_num(M[np.isfinite(M).all(axis=1)].T))
    off = C[np.triu_indices_from(C, 1)]
    print(f"  mean pairwise correlation between the nine: {off.mean():.2f}"
          f"  -> effective independent bets ~{1/ (1/len(data) + (1-1/len(data))*off.mean()):.1f}")

    # ---- 2. split
    print("\n--- 2. split-sample ---")
    half = len(port) // 2
    for label, seg in (("first half ", port[:half]), ("second half", port[half:])):
        mm, tt = tstat(seg)
        print(f"  {label}: n={len(seg):4d}  mean {mm*100:+.4f}%/day  "
              f"t {tt:+.2f}  ann {mm*252*100:+.1f}%")

    # ---- 3. execution realism
    print("\n--- 3. how much slippage does it absorb? ---")
    print(f"  {'extra cost/day':>16s}{'mean %/day':>13s}{'ann %':>9s}{'t':>8s}")
    for extra_bp in (0, 1, 2, 5, 10, 20):
        adj = port - extra_bp / 10000.0
        mm, tt = tstat(adj)
        print(f"  {extra_bp:>13d}bp{mm*100:13.4f}{mm*252*100:9.1f}{tt:8.2f}")
    # break-even slippage
    be = m * 10000
    print(f"\n  the edge dies at about {be:.1f} bp of round-trip slippage per day")
    print(f"  (median quoted spread across the nine is "
          f"{np.mean([v[2] for v in data.values()])*10000:.1f} bp, already deducted)")


if __name__ == "__main__":
    main()
