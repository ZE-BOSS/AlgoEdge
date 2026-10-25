"""A fib_v2 variant (argv[1], default D) on synthetics: per market, per year, long vs short."""
import datetime as dt, numpy as np
from fib_backtest import Market
from fib_v2 import VARIANTS, SYNTH, W36
from fib_backtest import backtest
import sys
v = [x for x in VARIANTS if x.name.startswith(sys.argv[1] if len(sys.argv) > 1 else "D ")][0]
allt = []
for s in SYNTH:
    tr = [x for x in backtest(Market(s), v)["trades"]]
    allt += [(s, x) for x in tr]
    r3 = np.array([x["r"] for x in tr if x["t"] >= W36]); rf = np.array([x["r"] for x in tr])
    print(f"{s:26s} 3y n={len(r3):3d} {r3.mean() if len(r3) else 0:+.3f}R | full history n={len(rf):3d} {rf.mean() if len(rf) else 0:+.3f}R")
by = {}
for s, x in allt:
    by.setdefault(dt.datetime.fromtimestamp(x["t"], dt.UTC).year, []).append(x["r"])
print("by year:", {y: (len(r), round(float(np.mean(r)), 3)) for y, r in sorted(by.items())})
for d in (1, -1):
    r = [x["r"] for _, x in allt if x["d"] == d and x["t"] >= W36]
    print("long" if d > 0 else "short", len(r), round(float(np.mean(r)), 3))
