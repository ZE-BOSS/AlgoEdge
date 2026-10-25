"""
research/fib/fib_v2.py - can Fibonacci be USED profitably? (2026-10-25)

Fib as the ENTRY inside things that have an edge of their own (trend direction,
trailing exits), against a no-Fib control. $10,000 account, 1% of current equity
risked per trade, compounded, one position per market, no pyramiding.
Windows: last 9 months and last 3 years to 2026-10-24.

    PYTHONPATH="venv/Lib/site-packages;." py -3.12 research/fib/fib_v2.py
"""

from __future__ import annotations

import json
import math

import numpy as np

from fib_backtest import RESULTS, SYMBOLS, V, Market, backtest

END = 1_761_350_400            # 2026-10-25 00:00 UTC
W9 = 1_769_212_800             # 2026-01-24: last 9 months
W36 = 1_698_105_600            # 2023-10-24: last 3 years
REAL = ["XAUUSD", "XAGUSD", "US Tech 100", "US SP 500", "Wall Street 30", "GBPJPY", "EURUSD", "BTCUSD"]
SYNTH = [s for s in SYMBOLS if s not in REAL]

VARIANTS = [
    V("A  your rules: 0.5 / SL 0.786 / TP 0"),
    V("B  your setup: CHoCH + OB"),                                              # filled below
    V("C  trend-aligned, TP 0", trend="htf"),
    V("D  trend-aligned, TP 1.618 ext", trend="htf", tp=-0.618),
    V("E  trend-aligned, trailing stop", trend="htf", exit="trail"),
    V("F  trend-aligned, 0.382 entry, trailing", trend="htf", exit="trail", entry=0.382, sl=0.786),
    V("G  trend-aligned, trailing, big legs K=5", trend="htf", exit="trail", k=5),
    V("H  trailing stop, no trend filter", exit="trail"),
    V("X  CONTROL: trend + trail, no Fib (enter at once)", trend="htf", exit="trail", entry_mode="market", sl=0.5),
]
VARIANTS[1] = V("B  your setup: CHoCH + OB", structure="choch", ob=True)


def account(trades, start, capital=10_000.0, risk=0.01):
    """Chronological $ simulation. Risk is 1% of CLOSED equity when the trade opens."""
    tr = sorted((x for x in trades if x["t"] >= start), key=lambda x: x["t"])
    if not tr:
        return dict(n=0)
    events = []                                         # (time, kind, idx) kind 0 = exit first at equal time
    for i, x in enumerate(tr):
        events += [(x["t"], 1, i), (x["tx"] + 14_399, 0, i)]       # exits land at the end of their H4 bar
    eq, peak, dd, stake, open_n, max_open = capital, capital, 0.0, {}, 0, 0
    curve = []
    for t, kind, i in sorted(events):
        if kind == 1:
            stake[i] = eq * risk
            open_n += 1
            max_open = max(max_open, open_n)
        else:
            eq += tr[i]["r"] * stake.pop(i)
            open_n -= 1
            peak = max(peak, eq)
            dd = max(dd, (peak - eq) / peak)
            curve.append((t, eq))
    rs = np.array([x["r"] for x in tr])
    w, l = rs[rs > 0], rs[rs <= 0]
    months = (END - start) / (86400 * 30.44)
    return dict(n=len(tr), per_month=len(tr) / months, win=float((rs > 0).mean() * 100), exp=float(rs.mean()),
                pf=float(w.sum() / -l.sum()) if l.sum() < 0 else math.inf,
                rr=float(w.mean() / -l.mean()) if len(w) and len(l) else math.nan,
                t=float(rs.mean() / rs.std(ddof=1) * math.sqrt(len(rs))) if len(rs) > 2 else math.nan,
                final=eq, ret=(eq / capital - 1) * 100, dd=dd * 100, max_open=max_open,
                curve=curve)


def main():
    markets = {s: Market(s) for s in SYMBOLS}
    out = {}
    for v in VARIANTS:
        per = {s: backtest(mk, v)["trades"] for s, mk in markets.items()}
        row = {}
        for wname, ws in (("9m", W9), ("3y", W36)):
            row[wname] = {
                "markets": {s: {k: x for k, x in account(per[s], ws).items() if k != "curve"} for s in SYMBOLS},
                "real": account([x for s in REAL for x in per[s]], ws),
                "synth": account([x for s in SYNTH for x in per[s]], ws),
                "all": account([x for s in SYMBOLS for x in per[s]], ws),
            }
        out[v.name] = row
        for wname in ("9m", "3y"):
            for book in ("real", "synth"):
                a = row[wname][book]
                if a["n"]:
                    print(f"{v.name:50s} {wname} {book:5s} n={a['n']:4d} win {a['win']:5.1f}% exp {a['exp']:+.3f}R "
                          f"t {a['t']:+.2f}  $10k -> ${a['final']:>9,.0f} ({a['ret']:+6.1f}%)  maxDD {a['dd']:5.1f}%  open<= {a['max_open']}",
                          flush=True)
    (RESULTS / "fib_v2.json").write_text(json.dumps(out, default=float))


if __name__ == "__main__":
    main()
