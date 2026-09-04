"""Phase 1.3: does Step Index (or Range Break) have memory?

Step Index is the only symbol in the universe with any memory signature - its
ACF(r^2) is small but systematically positive at every lag tested (+0.011 to
+0.018 at lags 1, 6, 24, 168 on H1), where every other symbol sits within +-0.009
of zero. It is also the only genuinely discrete process: one step size, 0.1, up
or down, kurtosis 1.0. That makes it a clean Bernoulli sequence, and a Bernoulli
sequence either has memory or it does not - there is no ambiguity to argue about.

With ~31.5 M ticks the standard error on P(up) is 0.009%, so this test can resolve
a bias of a few hundredths of a percent. If the process is a fair coin we will say
so with real precision rather than "no evidence found".

The economics matter as much as the statistics, so they are reported together.
Step's spread is 0.00256% of price against a step of 0.00131% - the round trip
costs about **two steps**. A single-step bet can never pay: you would need
P(correct) > 1.5. An edge only becomes tradeable if it persists over N steps,
where (2p-1) * N > 2. That is the bar any finding here has to clear.
"""
from __future__ import annotations

import sys
from math import log, sqrt
from pathlib import Path

import numpy as np

TICKS = Path(__file__).resolve().parent / "ticks"
SPREAD_PCT = {"stpRNG": 0.00256, "RB100": 0.00185, "RB200": 0.00150}


def main(code: str) -> None:
    f = sorted(TICKS.glob(f"{code}_*d.npz"), key=lambda x: x.stat().st_size)
    if not f:
        print(f"{code}: no ticks"); return
    z = np.load(f[-1], allow_pickle=True)
    p = z["p"]
    d = np.diff(p)
    px = float(np.median(p))

    print(f"\n{'='*92}\n{code}: {len(p):,} ticks   median price {px:,.2f}\n{'='*92}")
    steps = np.unique(np.round(np.abs(d[d != 0]), 8))
    print(f"  distinct |step| sizes: {len(steps)}"
          + (f"  -> {steps[:6]}" if len(steps) <= 8 else f"  (min {steps[0]:g} max {steps[-1]:g})"))
    nz = d != 0
    print(f"  zero-change ticks: {(~nz).sum():,} ({(~nz).mean():.3%})")

    s = (d[nz] > 0).astype(np.int8)          # 1 = up, 0 = down
    n = len(s)
    pu = s.mean()
    se = sqrt(pu * (1 - pu) / n)
    print(f"\n  --- 1. is it a fair coin? ---")
    print(f"    n = {n:,}   P(up) = {pu:.6f}   SE = {se:.6f}")
    print(f"    95% CI [{pu-1.96*se:.6f}, {pu+1.96*se:.6f}]   z vs 0.5 = {(pu-0.5)/se:+.2f}")
    step_pct = float(steps[0]) / px * 100 if len(steps) else float("nan")
    print(f"    step = {step_pct:.6f}% of price, spread = {SPREAD_PCT.get(code, float('nan')):.5f}%"
          f"  -> round trip is {SPREAD_PCT.get(code, 0)/step_pct:.2f} steps")

    print(f"\n  --- 2. conditional on the previous k moves ---")
    print(f"    {'k':>3s}{'states':>8s}{'LR chi2':>12s}{'df':>5s}{'p-value':>11s}"
          f"{'max |P(up)-0.5|':>17s}{'  verdict'}")
    from math import erfc
    for k in (1, 2, 3, 4, 6, 8, 10):
        if n < (1 << k) * 500:
            continue
        # encode the previous k moves as an integer state
        state = np.zeros(n - k, dtype=np.int64)
        for j in range(k):
            state = (state << 1) | s[j:n - k + j]
        nxt = s[k:]
        m = 1 << k
        c1 = np.bincount(state[nxt == 1], minlength=m).astype(float)
        c0 = np.bincount(state[nxt == 0], minlength=m).astype(float)
        tot = c1 + c0
        ok = tot > 200
        # likelihood-ratio test against a single global p
        ll_full = 0.0
        for a, b in zip(c1[ok], c0[ok]):
            q = a / (a + b)
            if 0 < q < 1:
                ll_full += a * log(q) + b * log(1 - q)
        A, B = c1[ok].sum(), c0[ok].sum()
        q0 = A / (A + B)
        ll_null = A * log(q0) + B * log(1 - q0)
        chi2 = 2 * (ll_full - ll_null)
        df = int(ok.sum()) - 1
        # survival of chi2 via normal approx (Wilson-Hilferty), fine at large df
        zc = ((chi2 / df) ** (1 / 3) - (1 - 2 / (9 * df))) / sqrt(2 / (9 * df))
        pval = 0.5 * erfc(zc / sqrt(2))
        dev = float(np.max(np.abs(c1[ok] / tot[ok] - 0.5))) if ok.any() else float("nan")
        verdict = "MEMORY" if pval < 1e-4 else "no memory"
        print(f"    {k:3d}{int(ok.sum()):8d}{chi2:12.1f}{df:5d}{pval:11.3g}"
              f"{dev:17.5f}  {verdict}")

    print(f"\n  --- 3. runs ---")
    ch = np.flatnonzero(np.diff(s) != 0)
    runs = np.diff(np.concatenate(([-1], ch, [n - 1])))
    print(f"    runs: {len(runs):,}   mean length {runs.mean():.4f}   "
          f"(fair coin predicts {1/(1-pu*pu-(1-pu)**2)*1:.4f} -> 2.0000 at p=0.5)")
    obs = np.bincount(runs[runs <= 12], minlength=13)[1:13].astype(float)
    exp = np.array([len(runs) * (0.5 ** L) for L in range(1, 13)])
    print(f"    {'len':>4s}{'observed':>12s}{'geometric':>12s}{'ratio':>8s}")
    for L in range(1, 9):
        print(f"    {L:4d}{obs[L-1]:12,.0f}{exp[L-1]:12,.0f}{obs[L-1]/exp[L-1]:8.4f}")

    print(f"\n  --- 4. is any of it tradeable? ---")
    sp = SPREAD_PCT.get(code)
    if sp and step_pct == step_pct:
        cost_steps = sp / step_pct
        for edge in (abs(pu - 0.5), 0.001, 0.005):
            if edge <= 0:
                continue
            need = cost_steps / (2 * edge)
            print(f"    with P(up)-0.5 = {edge:.5f}: need to hold {need:,.0f} steps "
                  f"({need*1/60:,.0f} min) just to cover the {cost_steps:.2f}-step round trip")


if __name__ == "__main__":
    for c in (sys.argv[1:] or ["stpRNG"]):
        main(c)
