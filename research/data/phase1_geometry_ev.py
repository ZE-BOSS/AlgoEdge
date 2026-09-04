"""Phase 1c: does tight-stop/far-target geometry create expectancy? (research/19 section 1)

The contradiction to settle: Crash/Boom jumps are memoryless and the process is a
fair martingale (phase1_bar_stability.py: every symbol passes the Ito-corrected
test), yet report 18 measures DriftJumpAlpha at +0.2039 R out of sample on
Crash 1000. Something has to give.

The theory is sharp and worth stating because it makes this test decisive. For a
martingale, the optional stopping theorem says the expected value at any bounded
stopping time equals the starting value. A stop/target bracket IS a stopping time.
So **gross expectancy is exactly zero for every stop/target pair**, no matter how
tight the stop or how far the target - the low win rate and the large payoff
cancel exactly. Net of a round-trip spread s with stop distance S:

    E[R] = -s / S      and nothing else.

That is a parameter-free prediction. If real Boom/Crash ticks reproduce it, then
no geometry creates edge, DJA's result is not geometric, and we look at the
harness. If real ticks beat it, the asymmetric jump structure genuinely breaks
optional stopping in a tradeable way, and we have found the mechanism.

Ticks, not bars: a bar-based test cannot say whether the stop or the target was
touched first inside a bar, and that ambiguity is the same size as the effect.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

TICKS = Path(__file__).resolve().parent / "ticks"
MAX_LOOKAHEAD = 200_000          # ticks; beyond this the trade is unresolved
STRIDE = 40                      # entries every N ticks


def resolve(p: np.ndarray, i: int, up: float, dn: float) -> tuple[int, float]:
    """Which level was touched first, and AT WHAT PRICE.

    Returning the fill price rather than the level is the whole point. Boom and
    Crash move in single-tick jumps of ~0.11%, while a 0.5 x ATR stop sits 0.027%
    away - so a spike does not touch the stop, it leaps clear over it by four
    times the stop distance. Assuming a fill at the stop level understates the
    loss by that factor, and it is exactly the term that makes these instruments
    fair. `0` means unresolved within the lookahead.
    """
    hi = min(i + MAX_LOOKAHEAD, len(p))
    seg = p[i + 1:hi]
    if seg.size == 0:
        return 0, 0.0
    a = np.flatnonzero(seg >= up)
    b = np.flatnonzero(seg <= dn)
    ja = a[0] if a.size else np.inf
    jb = b[0] if b.size else np.inf
    if ja == np.inf and jb == np.inf:
        return 0, 0.0
    if ja < jb:
        return 1, float(seg[int(ja)])
    return -1, float(seg[int(jb)])


def run(code: str, spread_pct: float, atr_pct: float) -> None:
    f = sorted(TICKS.glob(f"{code}_*d.npz"), key=lambda x: x.stat().st_size)
    if not f:
        print(f"{code}: no tick file"); return
    z = np.load(f[-1], allow_pickle=True)
    p = z["p"]
    t = z["t"]
    days = (t[-1] - t[0]) / 86400
    print(f"\n{'='*100}")
    print(f"{code}   {len(p):,} ticks over {days:.1f} days   file={f[-1].name}")
    print(f"  M5 ATR = {atr_pct:.4f}% of price | round-trip spread = {spread_pct:.5f}%")
    print(f"{'='*100}")
    print(f"{'stop':>7s}{'RR':>4s}{'side':>7s}{'n':>7s}{'win%':>6s}"
          f"{'idealR':>9s}{'realR':>9s}{'SE':>7s}{'slip':>8s}{'netR':>9s}"
          f"{'pred':>8s}{'  verdict'}")

    idx = np.arange(0, len(p) - 1, STRIDE)
    for stop_atr in (0.5, 1.0, 2.0):
        S = atr_pct / 100 * stop_atr
        for rr in (2.0, 5.0):
            T = S * rr
            for side in ("long", "short"):
                ideal, real = [], []
                for i in idx:
                    e = p[i]
                    if side == "long":
                        o, fill = resolve(p, i, e * (1 + T), e * (1 - S))
                        move = (fill - e) / e
                    else:
                        o, fill = resolve(p, i, e * (1 + S), e * (1 - T))
                        o, move = -o, (e - fill) / e
                    if o == 0:
                        continue
                    ideal.append(rr if o > 0 else -1.0)
                    real.append(move / S)          # actual excursion, in R
                if len(ideal) < 200:
                    continue
                a, b = np.asarray(ideal), np.asarray(real)
                se = b.std() / np.sqrt(len(b))
                cost_r = (spread_pct / 100) / S
                net = b.mean() - cost_r
                pred = -cost_r
                z_ = (net - pred) / se
                verdict = ("as predicted" if abs(z_) < 2
                           else ("BEATS martingale" if z_ > 0 else "worse than predicted"))
                print(f"{stop_atr:7.1f}{rr:4.0f}{side:>7s}{len(b):7d}"
                      f"{(a > 0).mean()*100:6.1f}{a.mean():9.4f}{b.mean():9.4f}{se:7.4f}"
                      f"{b.mean()-a.mean():8.4f}{net:9.4f}{pred:8.4f}"
                      f"  {verdict} (z={z_:+.1f})")


if __name__ == "__main__":
    # spread and ATR from synth_spread_audit.py (HISTORICAL spread, not the
    # inflated overnight live read)
    cfg = {
        "BOOM1000":  (0.00100, 0.0540),
        "BOOM500":   (0.00139, 0.0885),
        "CRASH1000": (0.00099, 0.0381),
        "CRASH500":  (0.00138, 0.1117),
        "stpRNG":    (0.00256, 0.0320),
        "R_75":      (0.03467, 0.3008),
        "RB100":     (0.00185, 0.0545),
        "JD25":      (0.00588, 0.1102),
    }
    want = sys.argv[1:] or list(cfg)
    for c in want:
        if c in cfg:
            run(c, *cfg[c])
