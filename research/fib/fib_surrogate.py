"""
research/fib/fib_surrogate.py - sign-flip surrogate test for the fib_v2 variants.

Each surrogate keeps a market's real bars (range, wicks, volatility clustering,
spread) but mirrors each bar around its open with probability 0.5 and re-chains
them, so any trend or level structure is destroyed. The harness's own biases
(stop fills, tie-breaks) are identical in real and surrogate runs, so

    edge = real mean R - surrogate mean R,  p = share of surrogates >= real.

Both run on H4 only (no M15) so the comparison is like for like.
"""

from __future__ import annotations

import dataclasses as dc
import json
import sys

import numpy as np

from fib_backtest import RESULTS, SYMBOLS, Market, backtest
from fib_v2 import REAL, SYNTH, VARIANTS, W9, W36

N_SUR = int(sys.argv[1]) if len(sys.argv) > 1 else 30


class Flipped(Market):
    def __init__(self, base: Market, seed: int):
        rng = np.random.default_rng(seed)
        o, h, l, c = base.o, base.h, base.l, base.c
        flip = rng.random(len(o)) < 0.5
        dh, dl, dc_ = h - o, l - o, c - o
        ndh = np.where(flip, -dl, dh)
        ndl = np.where(flip, -dh, dl)
        ndc = np.where(flip, -dc_, dc_)
        gap = np.r_[0.0, o[1:] - c[:-1]] * np.where(rng.random(len(o)) < 0.5, -1, 1)
        no = np.empty_like(o)
        no[0] = o[0]
        for i in range(1, len(o)):
            no[i] = no[i - 1] + ndc[i - 1] + gap[i]
        shift = max(0.0, -(no + ndl).min() + 1.0)       # keep prices positive
        no = no + shift
        self.sym, self.t, self.sp, self.atr, self.m15, self._piv = base.sym, base.t, base.sp, base.atr, None, {}
        self.o, self.h, self.l, self.c = no, no + ndh, no + ndl, no + ndc
        from fib_backtest import atr
        self.atr = atr(self.h, self.l, self.c)
        cs = np.r_[0, np.cumsum(self.c)]
        self.sma = np.full(len(self.c), np.nan)
        self.sma[299:] = (cs[300:] - cs[:-300]) / 300


def means(per, ws, syms):
    r = [x["r"] for s in syms for x in per[s] if x["t"] >= ws]
    return (float(np.mean(r)) if r else float("nan")), len(r)


def main():
    markets = {s: Market(s) for s in SYMBOLS}
    out = {}
    for v in VARIANTS:
        v = dc.replace(v, intrabar=False)
        real = {s: backtest(mk, v)["trades"] for s, mk in markets.items()}
        sur = []
        for k in range(N_SUR):
            sur.append({s: backtest(Flipped(mk, 1000 * k + i), v)["trades"] for i, (s, mk) in enumerate(markets.items())})
        row = {}
        for wn, ws in (("9m", W9), ("3y", W36)):
            for book, syms in (("real", REAL), ("synth", SYNTH)):
                m, n = means(real, ws, syms)
                sm = np.array([means(sp, ws, syms)[0] for sp in sur])
                row[f"{wn}_{book}"] = dict(real=m, n=n, sur_mean=float(np.nanmean(sm)), sur_p95=float(np.nanpercentile(sm, 95)),
                                           edge=m - float(np.nanmean(sm)), p=float((sm >= m).mean()))
            # per market, 3y
        row["per_market_3y"] = {}
        for s in SYMBOLS:
            m, n = means(real, W36, [s])
            sm = np.array([means(sp, W36, [s])[0] for sp in sur])
            row["per_market_3y"][s] = dict(real=m, n=n, sur_mean=float(np.nanmean(sm)), p=float((sm >= m).mean()))
        out[v.name] = row
        for key in ("9m_real", "3y_real", "9m_synth", "3y_synth"):
            x = row[key]
            print(f"{v.name:50s} {key:9s} n={x['n']:4d} real {x['real']:+.3f}R  surrogate {x['sur_mean']:+.3f}R (p95 {x['sur_p95']:+.3f})  "
                  f"edge {x['edge']:+.3f}R  p={x['p']:.2f}", flush=True)
    (RESULTS / "fib_surrogate.json").write_text(json.dumps(out))


if __name__ == "__main__":
    main()
