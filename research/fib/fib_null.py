"""Harness bias check: the v2 variants on pure Gaussian random walks (no edge can exist)."""
import dataclasses as dc, numpy as np
from fib_backtest import Market, backtest, atr, stats
from fib_v2 import VARIANTS

class Fake(Market):
    def __init__(self, seed, n=15000, sub=16):
        rng = np.random.default_rng(seed)
        x = 10000 + np.cumsum(rng.normal(0, 2.0, n * sub)).reshape(n, sub)
        prev = np.r_[10000, x[:-1, -1]]
        self.o, self.c = prev, x[:, -1]
        self.h, self.l = np.maximum(x.max(1), prev), np.minimum(x.min(1), prev)
        self.t = np.arange(n) * 14400 + 1_500_000_000
        self.sp = np.full(n, 0.2)
        self.atr = atr(self.h, self.l, self.c); self.m15 = None; self._piv = {}
        cs = np.r_[0, np.cumsum(self.c)]; self.sma = np.full(n, np.nan); self.sma[299:] = (cs[300:] - cs[:-300]) / 300

for v in VARIANTS:
    v = dc.replace(v, intrabar=False)
    rs = [x["r"] for s in range(12) for x in backtest(Fake(s), v)["trades"]]
    st = stats(rs, 1)
    print(f"{v.name:50s} n={st['n']:5d} exp {st['exp']:+.3f}R t {st['t']:+.2f}")
