"""Phase 3: Monte Carlo validation of the index intraday-reversal candidate.

Report 19 section 2 set this phase's rule, and it was written for the synthetic
universe: fit the generating process, then validate on unlimited freshly
simulated data, so that a strategy is tested on data it cannot have been fitted
to. Real markets have no known generator, so the rule needs adapting rather than
abandoning. The adaptation keeps the logic and replaces the generator with a
*fitted* model whose one relevant parameter can be switched off:

    r_t = phi * r_{t-1} + sigma_t * e_t,   sigma_t^2 = omega + a*u_{t-1}^2 + b*sigma_{t-1}^2

`phi` is the daily autocorrelation the strategy claims to harvest. The GARCH part
reproduces the volatility clustering report 21 section 1 measured, so simulated
data has realistic fat tails and vol persistence rather than being Gaussian noise.

Four tests, each answering a different way the finding could be false:

1. **IID bootstrap** - resample returns independently, destroying autocorrelation
   while keeping the exact return distribution. The strategy must make nothing. If
   it still profits, it is exploiting something other than what it claims.
2. **Block bootstrap** - resample in blocks, preserving autocorrelation and
   clustering. Gives an honest confidence interval on Sharpe that does not assume
   independent days.
3. **Simulation with phi = measured vs phi = 0** - the report 19 three-way test.
   Profit at the measured phi and none at phi = 0 means the edge is structural.
4. **Data-mining deflation** - I searched 28 symbols and several structures. The
   bar is not a single-hypothesis t, it is the maximum over the whole search.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

BARS = Path(__file__).resolve().parent / "bars_live"
INDICES = ["US_SP_500", "US_Tech_100", "Germany_40", "UK_100", "Japan_225",
           "Hong_Kong_50", "Netherlands_25", "France_40", "Australia_200"]
N_SIM = 2000
rng = np.random.default_rng(20260904)


def load(name):
    f = BARS / f"{name}_D1.npz"
    if not f.exists():
        return None
    z = np.load(f, allow_pickle=True)
    o, c = z["o"].astype(float), z["c"].astype(float)
    t = z["t"].astype(np.int64)
    sp = float(np.median(z["spread"].astype(float) * float(z["meta"][2]) / c))
    cc = np.diff(np.log(c))          # signal source: close-to-close
    oc = np.log(c / o)[1:]           # tradeable leg: open-to-close, same days
    return cc, oc, sp, (t[1:] // 86400)


def pool_by_date(series_by_name: dict) -> np.ndarray:
    """Equal-weight portfolio aligned on calendar date.

    Aligning by POSITION instead is a trap worth naming: the nine indices have
    different holiday calendars and bar counts, so positional averaging silently
    pairs different days together. That destroys their real 0.37 cross-correlation,
    which shrinks the portfolio variance and inflates Sharpe - the first run of
    this script read t = +4.12 and Sharpe +2.37 that way, against a correct +2.30
    and +1.35.
    """
    days = sorted(set.intersection(*[set(d.tolist()) for _, d in
                                     series_by_name.values()]))
    idx = {d: i for i, d in enumerate(days)}
    M = np.full((len(days), len(series_by_name)), np.nan)
    for j, (v, d) in enumerate(series_by_name.values()):
        for dd, x in zip(d.tolist(), v):
            if dd in idx:
                M[idx[dd], j] = x
    p = np.nanmean(M, axis=1)
    return p[np.isfinite(p)]


def strat(cc, oc, sp):
    """Intraday reversal: yesterday's close-to-close sign, traded open->close."""
    sig = -np.sign(cc[:-1])
    return sig * oc[1:] - sp


def stats(x):
    se = x.std(ddof=1) / np.sqrt(len(x))
    return x.mean(), (x.mean() / se if se else np.nan), \
        x.mean() / x.std(ddof=1) * np.sqrt(252)


def fit_garch(r, iters=60):
    """Crude GARCH(1,1) by grid search on the likelihood. No `arch` package here,
    and the exact parameters matter less than reproducing clustering at all."""
    v = r.var()
    best, bp = -np.inf, (v * 0.05, 0.08, 0.90)
    for a in np.linspace(0.02, 0.20, 10):
        for b in np.linspace(0.70, 0.97, 10):
            if a + b >= 0.999:
                continue
            w = v * (1 - a - b)
            s2 = np.empty(len(r))
            s2[0] = v
            for i in range(1, len(r)):
                s2[i] = w + a * r[i - 1] ** 2 + b * s2[i - 1]
            ll = -0.5 * np.sum(np.log(s2) + r ** 2 / s2)
            if ll > best:
                best, bp = ll, (w, a, b)
    return bp


def simulate(n, phi, w, a, b, common=None, load_f=0.0):
    """One AR(1)+GARCH(1,1) path; `common` injects a shared factor for realism."""
    r = np.zeros(n)
    s2 = w / max(1 - a - b, 1e-6)
    prev = 0.0
    for i in range(n):
        s2 = w + a * prev ** 2 + b * s2
        e = rng.standard_normal()
        if common is not None:
            e = np.sqrt(1 - load_f ** 2) * e + load_f * common[i]
        u = np.sqrt(s2) * e
        r[i] = phi * (r[i - 1] if i else 0.0) + u
        prev = u
    return r


def main() -> None:
    data = {n: load(n) for n in INDICES}
    data = {k: v for k, v in data.items() if v is not None}
    print(f"{len(data)} indices\n")

    # observed pooled strategy, aligned by DATE (see pool_by_date)
    L = min(len(v[0]) for v in data.values())
    real = pool_by_date({k: (strat(v[0], v[1], v[2]), v[3][2:])
                         for k, v in data.items()})
    m, t, sh = stats(real)
    print(f"OBSERVED  n={len(real)}  {m*100:+.4f}%/day  t {t:+.2f}  "
          f"Sharpe {sh:+.2f}  ann {m*252*100:+.1f}%")

    # ---- 1. IID bootstrap: autocorrelation destroyed
    print("\n--- 1. IID bootstrap (autocorrelation destroyed) ---")
    print("    the strategy should make NOTHING here")
    out = []
    for _ in range(N_SIM // 4):
        legs = []
        for cc, oc, sp, _ in data.values():
            k = rng.integers(0, len(cc), len(cc))
            legs.append(strat(cc[k], oc[k], sp)[:L - 2])
        out.append(np.mean(legs, axis=0).mean())
    out = np.asarray(out)
    print(f"    null mean {out.mean()*252*100:+.2f}%/yr   "
          f"95% range [{np.percentile(out,2.5)*252*100:+.1f}, "
          f"{np.percentile(out,97.5)*252*100:+.1f}]%/yr")
    print(f"    observed {m*252*100:+.1f}%/yr  ->  p = "
          f"{(out >= m).mean():.4f}")

    # ---- 2. block bootstrap: structure preserved
    print("\n--- 2. block bootstrap, 21-day blocks (structure preserved) ---")
    bl = 21
    sh_out = []
    for _ in range(N_SIM // 4):
        idx = []
        while len(idx) < len(real):
            s = rng.integers(0, max(1, len(real) - bl))
            idx.extend(range(s, s + bl))
        x = real[np.asarray(idx[:len(real)])]
        sh_out.append(x.mean() / x.std(ddof=1) * np.sqrt(252))
    sh_out = np.asarray(sh_out)
    print(f"    Sharpe {sh:+.2f}   bootstrap 95% CI "
          f"[{np.percentile(sh_out,2.5):+.2f}, {np.percentile(sh_out,97.5):+.2f}]")
    print(f"    P(Sharpe <= 0) = {(sh_out <= 0).mean():.4f}")

    # ---- 3. fitted-generator simulation, phi on vs phi off
    print("\n--- 3. simulation from a fitted AR(1)+GARCH(1,1) ---")
    fits = {}
    for name, (cc, oc, sp, _) in data.items():
        phi = float(np.corrcoef(cc[:-1], cc[1:])[0, 1])
        fits[name] = (phi, *fit_garch(cc - cc.mean()), sp,
                      float(np.std(oc) / np.std(cc)))
    print(f"    fitted phi: mean {np.mean([f[0] for f in fits.values()]):+.3f}  "
          f"range [{min(f[0] for f in fits.values()):+.3f}, "
          f"{max(f[0] for f in fits.values()):+.3f}]")
    for label, use_phi in (("phi = measured", True), ("phi = 0 (null)", False)):
        res = []
        for _ in range(N_SIM // 10):
            common = rng.standard_normal(L)
            legs = []
            for name, (phi, w, a, b, sp, ocr) in fits.items():
                r = simulate(L, phi if use_phi else 0.0, w, a, b, common, 0.6)
                # the tradeable leg is a scaled share of the daily move
                oc_sim = r * ocr
                legs.append(strat(r, oc_sim, sp))
            res.append(np.mean(legs, axis=0).mean())
        res = np.asarray(res)
        print(f"    {label:16s} ann {res.mean()*252*100:+7.2f}%/yr   "
              f"95% [{np.percentile(res,2.5)*252*100:+7.1f}, "
              f"{np.percentile(res,97.5)*252*100:+7.1f}]  "
              f"P(>0) {(res > 0).mean():.2f}")

    # ---- 4. data-mining deflation
    print("\n--- 4. data-mining deflation ---")
    n_tests = 28 * 3          # symbols x {clustering, drift, autocorrelation}
    mx = np.array([np.max(np.abs(rng.standard_normal(n_tests)))
                   for _ in range(20000)])
    print(f"    hypotheses effectively searched: ~{n_tests}")
    print(f"    null expects max |t| = {np.median(mx):.2f} "
          f"(95th pct {np.percentile(mx,95):.2f})")
    print(f"    observed pooled t = {t:+.2f}  ->  "
          f"{'clears' if t > np.percentile(mx,95) else 'INSIDE'} the search band")


if __name__ == "__main__":
    main()
