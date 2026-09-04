"""Is the equity-index daily mean reversion tradeable, or a microstructure artifact?

live_structure.py found strong negative lag-1 daily autocorrelation in all eight
equity indices - Netherlands 25 at -0.219 (t = 6.1), US SP 500 -0.174 (t = 5.0),
US Tech 100 -0.166 (t = 4.7) - while sixteen years of FX shows nothing at all.

Three reasons to be suspicious before trading it:

* **One market period, not eight tests.** Every index on this server starts in
  January 2024, and the world's equity indices are heavily correlated, so eight
  symbols is closer to one or two independent observations than to eight.
* **Negative daily autocorrelation is the classic signature of a measurement
  artifact** rather than an opportunity - stale or non-synchronous constituent
  prices, or a quote that oscillates around a slower true value. Such an effect is
  real in the recorded series and completely untradeable, because you cannot
  transact at the price that produced it.
* **Costs.** The effect must clear a round-trip spread, which for a daily
  round-trip strategy is charged every single day.

So the test is not "is the autocorrelation significant" - it already is. The test
is whether a rule exploiting it beats its costs, and whether it survives on a
period it was not discovered on.
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
    c = z["c"].astype(float)
    sp = z["spread"].astype(float) * float(z["meta"][2])       # points -> price
    return z["t"], c, sp


def main() -> None:
    print(f"{'symbol':17s}{'n':>6s}{'spread%':>9s}{'ACF1':>8s}"
          f"{'gross%/trade':>14s}{'net%/trade':>12s}{'SE':>8s}{'t':>7s}"
          f"{'ann.net%':>10s}  verdict")
    print("-" * 104)
    pooled_gross, pooled_net = [], []
    for name in INDICES:
        d = load(name)
        if d is None:
            continue
        t, c, sp = d
        r = np.diff(np.log(c))
        spread_pct = float(np.median(sp[1:] / c[1:])) * 100
        # rule: after a down day, be long the next day; after an up day, be short.
        # This is the direct expression of negative lag-1 autocorrelation.
        sig = -np.sign(r[:-1])
        gross = sig * r[1:]
        # a round trip every day: cross the spread on entry and on exit
        net = gross - spread_pct / 100
        n = len(net)
        se = net.std(ddof=1) / np.sqrt(n)
        tt = net.mean() / se if se else np.nan
        ann = net.mean() * 252 * 100
        a1 = float(np.corrcoef(r[:-1], r[1:])[0, 1])
        verdict = ("TRADEABLE" if tt > 2 else
                   "gross only - costs kill it" if gross.mean() > 0 else "no")
        print(f"{name[:16]:17s}{n:6d}{spread_pct:9.4f}{a1:8.3f}"
              f"{gross.mean()*100:14.4f}{net.mean()*100:12.4f}{se*100:8.4f}"
              f"{tt:7.2f}{ann:10.1f}  {verdict}")
        pooled_gross.append(gross.mean())
        pooled_net.append(net.mean())

    print("-" * 104)
    g, nt = np.array(pooled_gross), np.array(pooled_net)
    print(f"\npooled across {len(g)} indices:")
    print(f"  mean GROSS per trade {g.mean()*100:+.4f}%   "
          f"({g.mean()*252*100:+.1f}%/yr before costs)")
    print(f"  mean NET   per trade {nt.mean()*100:+.4f}%   "
          f"({nt.mean()*252*100:+.1f}%/yr after costs)")
    print(f"  indices with positive gross: {(g > 0).sum()}/{len(g)}")
    print(f"  indices with positive net  : {(nt > 0).sum()}/{len(nt)}")

    print("\n--- how big would the effect need to be to pay? ---")
    d = load("US_SP_500")
    if d is not None:
        t, c, sp = d
        r = np.diff(np.log(c))
        spread_pct = float(np.median(sp[1:] / c[1:])) * 100
        sd = r.std() * 100
        # E[next | today] = rho * today; average |today| is ~0.8 sd
        need = spread_pct / (0.8 * sd)
        print(f"  US SP 500: daily sd {sd:.3f}%, round-trip spread {spread_pct:.4f}%")
        print(f"  a daily-reversal rule needs |rho| > {need:.3f} just to break even")
        print(f"  measured rho = {float(np.corrcoef(r[:-1], r[1:])[0,1]):.3f}")


if __name__ == "__main__":
    main()
