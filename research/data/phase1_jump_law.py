"""Phase 1b: the jump law for Class B symbols (Boom, Crash, Jump).

These are compound processes: a dense one-directional grind plus rare violent
jumps the other way. Three questions decide whether anything is tradeable:

1. **Arrival.** Is it Poisson? If inter-arrival times are exponential, waiting
   tells you nothing and no timing rule can work - the hazard is flat by
   construction. Tested two ways that must agree: the coefficient of variation of
   inter-arrival gaps (1.0 for exponential) and Fano factor of counts in fixed
   windows (1.0 for Poisson). Two tests because CV alone is fooled by a mixture.
2. **Magnitude.** What is the distribution, and does it *depend on anything we
   can observe at entry time*? Dependence on time-since-last-jump or time-of-day
   would be a genuine, structural, exploitable edge. Independence closes the
   question for good.
3. **Nameplate.** Does "Boom 1000" really mean one spike per 1000 ticks? With
   ~29 M ticks the rate is measurable to about +-0.6% relative, so this is a
   sharp test of the documented specification rather than a vague sanity check.

Jump identification exploits the bimodality: for these symbols the grind and the
jump populations are separated by orders of magnitude, so any threshold in the
gap between them gives the same answer. `_threshold` puts it at the widest gap in
the sorted magnitude distribution rather than at an arbitrary sigma multiple.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

TICKS = Path(__file__).resolve().parent / "ticks"
NOMINAL = {"BOOM1000": 1000, "BOOM500": 500, "CRASH1000": 1000, "CRASH500": 500,
           "JD25": None, "JD100": None}


def _threshold(mag: np.ndarray) -> float:
    """Split grind from jump by Otsu's method on log10 magnitude.

    The two populations are separated by orders of magnitude - on Boom 1000 the
    grind sits at 1e-5.9 and jumps at 1e-2.9, with a clean empty valley between -
    so the split is found by maximising between-class variance on the log
    histogram rather than by picking a sigma multiple. An earlier version took the
    widest consecutive gap in the sorted tail; with 31 M ticks the widest gap is
    between the two largest values, which returned a threshold near the maximum
    and identified almost no jumps.
    """
    m = mag[mag > 0]
    if len(m) < 100:
        return float("inf")
    a = np.log10(m)
    hist, edges = np.histogram(a, bins=256)
    w = hist.astype(float) / hist.sum()
    centres = (edges[:-1] + edges[1:]) / 2
    w0 = np.cumsum(w)
    w1 = 1.0 - w0
    mu = np.cumsum(w * centres)
    mu_t = mu[-1]
    with np.errstate(invalid="ignore", divide="ignore"):
        between = (mu_t * w0 - mu) ** 2 / (w0 * w1)
    between[~np.isfinite(between)] = -1
    return float(10 ** edges[int(np.argmax(between)) + 1])


def analyse(code: str) -> None:
    f = sorted(TICKS.glob(f"{code}_*d.npz"), key=lambda x: x.stat().st_size)
    if not f:
        print(f"{code}: no ticks yet"); return
    z = np.load(f[-1], allow_pickle=True)
    t, p = z["t"], z["p"]
    r = np.diff(p) / p[:-1]
    days = (t[-1] - t[0]) / 86400

    print(f"\n{'='*94}\n{code}  {len(p):,} ticks over {days:.0f} days  ({f[-1].name})\n{'='*94}")

    up = r > 0
    # the jump side is whichever direction carries the fat tail
    jump_up = float(np.abs(r[up]).max() if up.any() else 0) > \
              float(np.abs(r[~up]).max() if (~up).any() else 0)
    side = r if jump_up else -r
    # On Boom/Crash the generator is one-directional: 98% of ticks grind one way
    # and EVERY move the other way is a spike. When the jump side holds under 5%
    # of non-zero ticks, direction alone is the correct split and a magnitude
    # threshold only discards genuine small jumps - Otsu cut 22% of Boom's
    # up-moves and turned an exact 1-per-1004 rate into 1-per-1293. Otsu is kept
    # for the two-sided Class B members (the Jump indices).
    nz = int((r != 0).sum())
    frac = float((side > 0).sum()) / max(nz, 1)
    if frac < 0.05:
        thr = 0.0
        rule = f"direction only ({frac:.3%} of non-zero ticks are jump-side)"
    else:
        thr = _threshold(np.abs(side[side > 0]))
        rule = f"Otsu on log magnitude, threshold {thr*100:.4f}%"
    idx = np.flatnonzero(side > thr)
    if len(idx) < 50:
        print("  too few jumps identified"); return

    mag = side[idx] * 100
    gaps = np.diff(idx).astype(float)
    rate = len(r) / len(idx)
    print(f"  jump direction: {'UP (Boom-like)' if jump_up else 'DOWN (Crash-like)'}"
          f"   split: {rule}")
    print(f"  jumps: {len(idx):,}  ->  1 per {rate:,.1f} ticks"
          + (f"   nameplate {NOMINAL[code]}  ratio {rate/NOMINAL[code]:.3f}"
             if NOMINAL.get(code) else ""))
    se_rate = rate / np.sqrt(len(idx))
    print(f"     rate 95% CI: [{rate-1.96*se_rate:,.1f}, {rate+1.96*se_rate:,.1f}] ticks")

    # ---- 1. arrival law
    cv = gaps.std() / gaps.mean()
    win = int(rate * 20)
    counts = np.bincount(idx // win, minlength=len(r) // win + 1)
    fano = counts.var() / counts.mean() if counts.mean() else np.nan
    print(f"\n  ARRIVAL")
    print(f"    inter-arrival CV   {cv:.4f}   (1.000 = exponential = memoryless)")
    print(f"    Fano factor        {fano:.4f}   (1.000 = Poisson counts)")
    verdict = ("memoryless - NO timing edge possible"
               if abs(cv - 1) < 0.08 and abs(fano - 1) < 0.15
               else "DEVIATES from Poisson - investigate")
    print(f"    -> {verdict}")

    # Hazard: having already waited w ticks, what is P(jump within the next k)?
    # Memorylessness means this column is FLAT. A rising column would be a
    # tradeable timing edge; a falling one would mean jumps cluster.
    k = max(1, int(rate / 4))
    print(f"    hazard - P(jump within next {k} ticks | already waited w):")
    for w in (0, int(rate * 0.25), int(rate * 0.5), int(rate), int(rate * 2)):
        surv = gaps[gaps > w]
        if len(surv) < 200:
            continue
        h = float((surv <= w + k).sum()) / len(surv)
        print(f"       w={w:6,d}  n={len(surv):7,d}  P={h:.4f}")
    print(f"       exponential predicts a constant {1-np.exp(-k/gaps.mean()):.4f}")

    # ---- 2. magnitude
    print(f"\n  MAGNITUDE (% of price)")
    print(f"    mean {mag.mean():.4f}  median {np.median(mag):.4f}  "
          f"sd {mag.std():.4f}  p95 {np.percentile(mag,95):.4f}  max {mag.max():.4f}")
    # does size depend on what we could know beforehand?
    prev_gap = gaps
    m2 = mag[1:]
    cc = np.corrcoef(prev_gap, m2)[0, 1]
    hour = np.array([datetime.fromtimestamp(x, timezone.utc).hour for x in t[idx]])
    by_h = np.array([mag[hour == h].mean() if (hour == h).sum() > 30 else np.nan
                     for h in range(24)])
    lvl = p[idx]
    cl = np.corrcoef(lvl, mag)[0, 1]
    print(f"    corr(magnitude, ticks since previous jump) = {cc:+.4f}  "
          f"(SE ~{1/np.sqrt(len(m2)):.4f})")
    print(f"    corr(magnitude, price level)               = {cl:+.4f}")
    if np.isfinite(by_h).sum() > 12:
        sp = (np.nanmax(by_h) - np.nanmin(by_h)) / np.nanmean(by_h)
        print(f"    hour-of-day spread in mean magnitude       = {sp:.2%} "
              f"(min {np.nanmin(by_h):.4f} max {np.nanmax(by_h):.4f})")
    print(f"    -> magnitude is {'PREDICTABLE - exploitable' if abs(cc) > 4/np.sqrt(len(m2)) else 'independent of everything observable'}")

    # ---- 3. the grind
    grind = side[side < thr]
    print(f"\n  GRIND (the {len(grind)/len(r):.2%} of ticks that are not jumps)")
    print(f"    mean {grind.mean()*100:+.6f}%   sd {grind.std()*100:.6f}%")
    print(f"    jump contribution to drift {side[idx].sum()/len(r)*100:+.6f}% per tick")
    print(f"    grind contribution         {grind.sum()/len(r)*100:+.6f}% per tick")
    print(f"    NET                        {side.mean()*100:+.6f}% per tick"
          f"   (fair process => ~0)")


if __name__ == "__main__":
    for c in (sys.argv[1:] or ["BOOM1000", "BOOM500", "CRASH1000", "CRASH500",
                               "JD25", "JD100"]):
        analyse(c)
