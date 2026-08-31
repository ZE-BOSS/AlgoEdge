"""INVALID - DO NOT USE. Retained only as a record of a wrong turn.

This script charges extra cost to seven symbols on the belief that they were
backtested with no cost model. That belief was false (see debug/backtest-bugs.md,
B8 RETRACTED): all 21 symbols have costs, stored under UPPERCASED keys which my
audit lookup missed. Every number this produces is therefore double-charged.

The valid out-of-sample analysis is research/data/walk_forward.py.
"""

"""Out-of-sample test on cost-corrected data.

Same method as walk_forward.py: pick the cells using only Jan-Apr, then trade
them blind through May-Aug. The only change is that the seven zero-cost symbols
are now charged their real spread first, so the selection is not being made on
inflated numbers.
"""
import sqlite3, collections, os
import numpy as np

DB = "../../algoedge.db"
CACHE = "cache"
SPLIT = "2026-05-01"
ZERO_COST = ["Crash 1000 Index", "Crash 300 Index", "US Tech 100",
             "Volatility 75 Index", "Germany 40", "Netherlands 25",
             "Hong Kong 50"]


def charges(c):
    out = {}
    for sym in ZERO_COST:
        f = os.path.join(CACHE, sym.replace(" ", "_") + "__M15.npz")
        if not os.path.exists(f):
            continue
        z = np.load(f)
        spread = float(z["spread"]) * float(z["point"])
        risk = [abs(e - s) for e, s in c.execute(
            "select entry_price, stop_loss from backtest_trades where symbol=?",
            (sym,)) if e and s and abs(e - s) > 0]
        if risk:
            out[sym] = spread / float(np.median(risk))
    return out


def main():
    c = sqlite3.connect(DB)
    ch = charges(c)
    IS, OOS = collections.defaultdict(list), collections.defaultdict(list)
    for sid, sym, r, et in c.execute(
            "select strategy_id, symbol, pnl_r, entry_time from backtest_trades"):
        adj = r - ch.get(sym, 0.0)
        (IS if str(et) < SPLIT else OOS)[(sid, sym)].append(adj)

    print("cost charged to the seven zero-cost symbols:")
    for k, v in sorted(ch.items()):
        print(f"  {k:22s} {v:.3f} R/trade")
    print()
    oos_all = [x for v in OOS.values() for x in v]
    print(f"{'rule':34s}{'cells':>7s}{'OOS n':>8s}{'OOS expR':>10s}{'OOS totR':>10s}")
    for min_n in (20, 30, 50):
        sel = {k for k, v in IS.items() if len(v) >= min_n and sum(v) > 0}
        o = [x for k in sel for x in OOS.get(k, [])]
        if not o:
            continue
        print(f"{'select IS n>=' + str(min_n) + ' and positive':34s}"
              f"{len(sel):7d}{len(o):8d}{sum(o)/len(o):+10.3f}{sum(o):+10.1f}")
    print(f"{'whole book (no selection)':34s}{len(OOS):7d}{len(oos_all):8d}"
          f"{sum(oos_all)/len(oos_all):+10.3f}{sum(oos_all):+10.1f}")

    print("\n--- the n>=50 selection, cell by cell, out of sample ---")
    sel = {k for k, v in IS.items() if len(v) >= 50 and sum(v) > 0}
    print(f"{'strategy':20s}{'symbol':20s}{'IS R':>8s}{'OOS n':>7s}{'OOS R':>8s}{'OOS expR':>10s}")
    for k in sorted(sel, key=lambda k: -sum(OOS.get(k, [0]))):
        o = OOS.get(k, [])
        if not o:
            continue
        print(f"{k[0]:20s}{k[1]:20s}{sum(IS[k]):+8.1f}{len(o):7d}"
              f"{sum(o):+8.1f}{sum(o)/len(o):+10.3f}")


if __name__ == "__main__":
    main()
