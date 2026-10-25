"""$10k combined book: G (Fib pullback in trend, trailing) on ALL synthetics + X (trend + trail, no Fib) on ALL real CFDs."""
import datetime as dt, json, numpy as np
from fib_backtest import Market, backtest
from fib_v2 import VARIANTS, REAL, SYNTH, W9, W36, account
G = [v for v in VARIANTS if v.name.startswith("G ")][0]
X = [v for v in VARIANTS if v.name.startswith("X ")][0]
trades = {"G synth": [x for s in SYNTH for x in backtest(Market(s), G)["trades"]],
          "X real": [x for s in REAL for x in backtest(Market(s), X)["trades"]]}
trades["combined"] = trades["G synth"] + trades["X real"]
out = {}
for risk in (0.01, 0.005):
    for wn, ws in (("last 9 months", W9), ("last 3 years", W36)):
        for book, tr in trades.items():
            a = account(tr, ws, risk=risk)
            out[f"{risk}|{wn}|{book}"] = {k: v for k, v in a.items() if k != "curve"}
            print(f"risk {risk*100:.1f}%  {wn:14s} {book:9s} n={a['n']:4d} win {a['win']:4.1f}% RR {a['rr']:.2f} exp {a['exp']:+.3f}R  "
                  f"$10,000 -> ${a['final']:>9,.0f} ({a['ret']:+6.1f}%)  maxDD {a['dd']:4.1f}%  max open {a['max_open']}")
            if book == "combined" and risk == 0.01:
                # calendar-year returns from the equity curve
                c = a["curve"]; yr = {}
                prev = 10_000.0
                for t, e in c:
                    y = dt.datetime.fromtimestamp(t, dt.UTC).year
                    yr.setdefault(y, [prev, e]); yr[y][1] = e; prev = e
                print("      by year:", {y: f"{(e/s-1)*100:+.1f}%" for y, (s, e) in yr.items()})
json.dump(out, open("results/fib_book.json", "w"), default=float)
