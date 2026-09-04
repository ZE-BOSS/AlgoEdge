"""Where can an edge exist in the live-market book?

Report 20 closed the synthetic universe by measuring three things and finding
nothing in any of them. The same three measurements are the right lens on real
markets, because real markets are supposed to differ on exactly these axes:

1. **Volatility clustering** - ACF(r^2). Synthetics: within +-0.009 of zero at
   every lag. Real markets should be strongly positive and persistent. This is the
   most robust regularity in empirical finance, and unlike a directional edge it
   is not competed away, because it describes risk rather than return. Clustering
   is what makes vol targeting, position sizing and regime filters work.
2. **Risk premium** - Ito-corrected drift. Synthetics are fair by construction
   (all 11 symbols, |t| < 1). Equities are compensated for bearing risk, so a real
   non-zero drift is an edge that requires no forecasting at all.
3. **Return autocorrelation** - momentum or mean reversion in the returns
   themselves. This is what a directional entry rule needs, and the one most
   likely to have been arbitraged away.

Reporting all three together is the point. A symbol with clustering but no drift
and no return autocorrelation supports *risk management*, not entry signals -
conflating those is how a project ends up fitting entry rules to noise.

Daily bars, with periods-per-year derived from the data rather than assumed: FX
and indices trade ~252 days a year, crypto trades 365, and using one constant for
both would misstate every annualised figure.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
LIVE = HERE / "bars_live"
SYNTH = HERE / "bars"
LAGS = (1, 5, 22, 66)          # day, week, month, quarter


def acf(x: np.ndarray, lags) -> dict:
    x = x - x.mean()
    d = float((x * x).sum())
    return {k: float((x[:-k] * x[k:]).sum() / d) if d and k < len(x) else np.nan
            for k in lags}


def ljung_box(x: np.ndarray, m: int = 20) -> float:
    n = len(x)
    a = acf(x, range(1, m + 1))
    return n * (n + 2) * sum(a[k] ** 2 / (n - k) for k in range(1, m + 1))


def load(folder: Path, min_bars: int = 400):
    out = []
    for f in sorted(folder.glob("*_D1.npz")):
        z = np.load(f, allow_pickle=True)
        t, c = z["t"], z["c"]
        r = np.diff(np.log(c.astype(float)))
        ok = np.isfinite(r)
        r, t = r[ok], t[1:][ok]
        if len(r) < min_bars:
            continue
        yrs = (t[-1] - t[0]) / 86400 / 365.25
        ppy = len(r) / yrs if yrs > 0 else 252.0      # derived, not assumed
        out.append((f.name.replace("_D1.npz", "").replace("_", " "), r, yrs, ppy))
    return out


def main() -> None:
    live = load(LIVE)
    synth = load(SYNTH)
    if not live:
        raise SystemExit("no live D1 bars - run live_bar_harvest.py first")

    print("=" * 112)
    print("1. VOLATILITY CLUSTERING - ACF of squared daily returns")
    print("   The discriminator that closed the synthetic universe.")
    print("=" * 112)
    print(f"{'symbol':20s}{'years':>7s}{'bars':>7s}{'ann.vol%':>9s}"
          + "".join(f"{'lag'+str(k):>8s}" for k in LAGS)
          + f"{'LB Q(20)':>10s}  reading")

    def block(rows, tag):
        print(f"\n  -- {tag} --")
        for name, r, yrs, ppy in rows:
            vol = r.std() * np.sqrt(ppy) * 100
            a = acf(r * r, LAGS)
            q = ljung_box(r * r, 20)
            reading = ("STRONG clustering" if a[22] > 0.05 and q > 45 else
                       "some clustering" if a[1] > 0.05 else "none")
            print(f"{name[:19]:20s}{yrs:7.1f}{len(r):7,d}{vol:9.1f}"
                  + "".join(f"{a[k]:8.3f}" for k in LAGS)
                  + f"{q:10,.0f}  {reading}")

    block(live, "LIVE MARKETS")
    block(synth[:4], "SYNTHETIC baseline (report 20)")

    print("\n" + "=" * 112)
    print("2. RISK PREMIUM - Ito-corrected drift. Null is log drift = -sigma^2/2.")
    print("   SE(annual drift) = vol / sqrt(years) - short histories cannot resolve this.")
    print("=" * 112)
    print(f"{'symbol':20s}{'years':>7s}{'ann.vol%':>9s}{'log drift%':>12s}"
          f"{'excess%/yr':>12s}{'SE':>8s}{'t':>7s}  verdict")
    prem = []
    for name, r, yrs, ppy in live:
        vol = r.std() * np.sqrt(ppy) * 100
        drift = r.mean() * ppy * 100
        ito = -(vol / 100) ** 2 / 2 * 100
        se = vol / np.sqrt(yrs)
        ex = drift - ito
        t = ex / se if se else np.nan
        prem.append((t, name, ex, se, yrs))
        v = ("REAL PREMIUM" if t > 2 else "negative" if t < -2 else
             "not resolvable" if yrs < 5 else "not distinguishable")
        print(f"{name[:19]:20s}{yrs:7.1f}{vol:9.1f}{drift:12.2f}{ex:12.2f}"
              f"{se:8.2f}{t:7.2f}  {v}")

    print("\n" + "=" * 112)
    print("3. RETURN AUTOCORRELATION - what a directional entry rule would need.")
    print("   SE under iid is 1/sqrt(n). |ACF| under ~2 SE is noise.")
    print("=" * 112)
    print(f"{'symbol':20s}{'n':>7s}{'SE':>8s}"
          + "".join(f"{'lag'+str(k):>9s}" for k in LAGS) + f"{'max |t|':>9s}")
    for name, r, yrs, ppy in live:
        se = 1 / np.sqrt(len(r))
        a = acf(r, LAGS)
        mx = max(abs(v) for v in a.values() if v == v) / se
        print(f"{name[:19]:20s}{len(r):7,d}{se:8.4f}"
              + "".join(f"{a[k]:9.4f}" for k in LAGS) + f"{mx:9.1f}")

    print("\n" + "=" * 112)
    print("SUMMARY")
    print("=" * 112)
    clus = [n for n, r, y, p in live if acf(r * r, LAGS)[22] > 0.05]
    print(f"  strong volatility clustering: {len(clus)}/{len(live)} symbols")
    print(f"     {', '.join(clus[:12])}")
    prem.sort(reverse=True)
    good = [p for p in prem if p[0] > 2]
    print(f"\n  statistically real risk premium: {len(good)}/{len(prem)}")
    for t, name, ex, se, yrs in good:
        print(f"     {name[:22]:24s}{ex:+8.2f}%/yr  SE {se:5.2f}  t {t:5.2f}  "
              f"({yrs:.1f} yrs)")
    short = [p for p in prem if p[4] < 5]
    if short:
        print(f"\n  {len(short)} symbols have under 5 years of history, so their drift")
        print(f"  cannot be resolved either way: "
              f"{', '.join(p[1][:16] for p in short[:8])}")
    ret_sig = [n for n, r, y, p in live
               if max(abs(v) for v in acf(r, LAGS).values() if v == v) > 3 / np.sqrt(len(r))]
    print(f"\n  return autocorrelation beyond 3 SE: {len(ret_sig)}/{len(live)}")
    if ret_sig:
        print(f"     {', '.join(ret_sig[:12])}")


if __name__ == "__main__":
    main()
