"""Phase 1a: has each generator's parameters drifted over the instrument's life?

One year of ticks cannot answer this; seven years of H1 bars can. Two questions:

1. **Is the volatility constant?** A constant-vol GBM has *no volatility
   clustering* - the autocorrelation of squared returns is ~0 at every lag. Every
   real market has strong positive ACF(r^2). This is the sharpest available
   discriminator between "generated process" and "market", and it needs no
   distributional assumption.
2. **Is the drift zero?** Reported with a confidence interval, because the honest
   answer is that drift is barely estimable: the SE of an annualised drift
   estimate is (annual vol)/sqrt(years) regardless of sampling frequency. At 75%
   vol and 7.7 years that is +-27%/yr, and no amount of tick data shrinks it.
   Stating the CI is the point - it stops a noisy drift estimate being read as an
   edge.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from datetime import datetime, timezone

BARS = Path(__file__).resolve().parent / "bars"
HOURS_PER_YEAR = 24 * 365.25


def acf(x: np.ndarray, lags: tuple[int, ...]) -> dict[int, float]:
    x = x - x.mean()
    denom = float((x * x).sum())
    return {k: float((x[:-k] * x[k:]).sum() / denom) if denom else np.nan for k in lags}


def main() -> None:
    files = sorted(BARS.glob("*_H1.npz"))
    if not files:
        raise SystemExit("no H1 bars - run synth_bar_harvest.py first")

    print("=" * 108)
    print("VOLATILITY CLUSTERING - the market/generator discriminator")
    print("  ACF(r^2) ~ 0 at every lag => constant-volatility generated process")
    print("  ACF(r^2) >> 0            => volatility clusters, like a real market")
    print("=" * 108)
    print(f"{'symbol':26s}{'years':>7s}{'ann.vol%':>10s}{'ACF(r) 1':>10s}"
          f"{'ACF(r2) 1':>11s}{'lag6':>8s}{'lag24':>8s}{'lag168':>8s}  reading")

    rows = []
    for f in files:
        z = np.load(f, allow_pickle=True)
        t, c = z["t"], z["c"]
        r = np.diff(np.log(c))
        r = r[np.isfinite(r)]
        if len(r) < 5000:
            continue
        name = f.name.replace("_H1.npz", "").replace("_", " ")
        yrs = (t[-1] - t[0]) / 86400 / 365.25
        vol = r.std() * np.sqrt(HOURS_PER_YEAR) * 100
        a1 = acf(r, (1,))[1]
        a2 = acf(r * r, (1, 6, 24, 168))
        clustered = a2[24] > 0.05
        reading = "MARKET-LIKE (clusters)" if clustered else "generated, constant vol"
        print(f"{name:26s}{yrs:7.1f}{vol:10.2f}{a1:10.4f}{a2[1]:11.4f}"
              f"{a2[6]:8.4f}{a2[24]:8.4f}{a2[168]:8.4f}  {reading}")
        rows.append((name, t, c, r, yrs, vol))

    print("\n" + "=" * 108)
    print("DRIFT - with the confidence interval that makes it interpretable")
    print("  SE(annual drift) = annual_vol / sqrt(years). Frequency does not help.")
    print("=" * 108)
    print(f"{'symbol':26s}{'ann.drift%':>12s}{'SE%':>9s}{'95% CI':>22s}{'t':>7s}  verdict")
    for name, t, c, r, yrs, vol in rows:
        drift = r.mean() * HOURS_PER_YEAR * 100
        se = vol / np.sqrt(yrs)
        tstat = drift / se if se else np.nan
        lo, hi = drift - 1.96 * se, drift + 1.96 * se
        verdict = ("indistinguishable from zero" if abs(tstat) < 2
                   else "NON-ZERO - investigate")
        print(f"{name:26s}{drift:12.2f}{se:9.2f}{f'[{lo:+.1f}, {hi:+.1f}]':>22s}"
              f"{tstat:7.2f}  {verdict}")

    print("\n" + "=" * 108)
    print("IS EVERY SYMBOL A FAIR (ARITHMETIC-MARTINGALE) PROCESS?")
    print("  A process with zero expected PRICE return has LOG drift of exactly")
    print("  -sigma^2/2 (Ito). So the null to test is not 'log drift = 0' but")
    print("  'log drift = -sigma^2/2'. Testing against 0 makes high-vol symbols")
    print("  look like they have a short edge when they are merely convex.")
    print("=" * 108)
    print(f"{'symbol':26s}{'log drift%':>12s}{'-sig^2/2':>11s}{'excess%':>10s}"
          f"{'SE%':>8s}{'t':>7s}  verdict")
    for name, t, c, r, yrs, vol in rows:
        drift = r.mean() * HOURS_PER_YEAR * 100
        ito = -(vol / 100) ** 2 / 2 * 100
        se = vol / np.sqrt(yrs)
        excess = drift - ito
        tstat = excess / se if se else np.nan
        verdict = ("fair - no directional edge" if abs(tstat) < 2
                   else "EXCESS DRIFT - real edge candidate")
        print(f"{name:26s}{drift:12.2f}{ito:11.2f}{excess:10.2f}{se:8.2f}"
              f"{tstat:7.2f}  {verdict}")

    print("\n" + "=" * 108)
    print("PARAMETER STABILITY - annualised vol per calendar year")
    print("=" * 108)
    for name, t, c, r, yrs, vol in rows:
        years: dict[int, list[float]] = {}
        yr = np.array([datetime.fromtimestamp(x, tz=timezone.utc).year for x in t[1:]])
        line = f"{name:26s}"
        for y in sorted(set(yr.tolist())):
            v = r[yr == y]
            if len(v) < 500:
                continue
            line += f" {y}:{v.std() * np.sqrt(HOURS_PER_YEAR) * 100:6.1f}"
        print(line)


if __name__ == "__main__":
    main()
