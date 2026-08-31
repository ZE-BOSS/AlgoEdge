"""INVALID - DO NOT USE. Retained only as a record of a wrong turn.

This script charges extra cost to seven symbols on the belief that they were
backtested with no cost model. That belief was false (see debug/backtest-bugs.md,
B8 RETRACTED): all 21 symbols have costs, stored under UPPERCASED keys which my
audit lookup missed. Every number this produces is therefore double-charged.

The valid out-of-sample analysis is research/data/walk_forward.py.
"""

"""Stage E, redone with the zero-cost bug corrected.

Seven symbols were backtested with no cost model at all (report 08 section 1).
Every profitable cell in the sweep sits on one of them, so the portfolio has to
be rebuilt with the real spread charged before any of it can be believed.

Cost per trade is charged in R as: real_spread_price / median_risk_price, using
the actual per-trade risk distance from the backtest (risk_pips) rather than an
assumed ATR multiple - so it reflects the stops the strategies really used.
"""
import sqlite3, collections, math, glob, os
import numpy as np

DB = "../../algoedge.db"
CACHE = "cache"

# symbols the backtest charged nothing for
ZERO_COST = ["Crash 1000 Index", "Crash 300 Index", "US Tech 100",
             "Volatility 75 Index", "Germany 40", "Netherlands 25",
             "Hong Kong 50"]


def live_spread(sym):
    f = os.path.join(CACHE, sym.replace(" ", "_") + "__M15.npz")
    if not os.path.exists(f):
        return None
    z = np.load(f)
    return float(z["spread"]) * float(z["point"])


def main():
    c = sqlite3.connect(DB)
    # Median risk distance in TRUE PRICE UNITS, taken from the trades' own
    # entry and stop prices. risk_pips cannot be used here: "pip" is not "point"
    # and the ratio differs per instrument, which produced impossible costs
    # (Hong Kong 50 at 30 R) on the first pass.
    risk_px = {}
    for sym in ZERO_COST:
        rows = [abs(e - s) for e, s in c.execute(
            "select entry_price, stop_loss from backtest_trades "
            "where symbol=? and stop_loss is not null and entry_price is not null",
            (sym,)) if e and s and abs(e - s) > 0]
        if rows:
            risk_px[sym] = float(np.median(rows))

    charge = {}
    print("=== cost that should have been charged, per trade ===")
    print(f"{'symbol':22s}{'spread(px)':>12s}{'median risk(px)':>17s}{'cost in R':>11s}")
    for sym in ZERO_COST:
        sp, rp = live_spread(sym), risk_px.get(sym)
        if sp is None or not rp:
            print(f"{sym:22s}  no data"); continue
        charge[sym] = sp / rp
        print(f"{sym:22s}{sp:12.4f}{rp:17.4f}{charge[sym]:11.3f}")

    # rebuild every cell with the correction applied
    cells = collections.defaultdict(list)
    daily = collections.defaultdict(lambda: collections.defaultdict(float))
    for sid, sym, r, et in c.execute(
            "select strategy_id, symbol, pnl_r, entry_time from backtest_trades"):
        adj = r - charge.get(sym, 0.0)
        cells[(sid, sym)].append(adj)
        daily[(sid, sym)][str(et)[:10]] += adj

    print("\n=== cells that were profitable, re-scored with real costs ===")
    print(f"{'strategy':20s}{'symbol':20s}{'n':>6s}{'was R':>9s}{'now R':>9s}{'now expR':>10s}")
    survivors = []
    for (sid, sym), v in sorted(cells.items(), key=lambda kv: -sum(kv[1])):
        raw = sum(v) + charge.get(sym, 0.0) * len(v)
        if raw <= 0:
            continue
        tot = sum(v)
        flag = "" if tot > 0 else "   <== was only unpaid spread"
        print(f"{sid:20s}{sym:20s}{len(v):6d}{raw:+9.1f}{tot:+9.1f}"
              f"{tot/len(v):+10.3f}{flag}")
        if tot > 0 and len(v) >= 30:
            survivors.append((sid, sym))

    # portfolio stats on the corrected survivors
    days = sorted({d for k in daily for d in daily[k]})
    def stats(keys, label):
        tot = [sum(daily[k].get(d, 0.0) for k in keys) for d in days]
        n = len(tot); mean = sum(tot) / n
        sd = math.sqrt(sum((x - mean) ** 2 for x in tot) / n)
        eq = peak = mdd = 0.0
        for x in tot:
            eq += x; peak = max(peak, eq); mdd = max(mdd, peak - eq)
        print(f"\n{label}")
        print(f"  components {len(keys)} | total {sum(tot):+.1f} R over {n} days")
        print(f"  daily mean {mean:+.3f} R, sd {sd:.3f}")
        if sd: print(f"  annualised Sharpe {mean/sd*math.sqrt(252):.2f}")
        if mdd: print(f"  max drawdown {mdd:.1f} R | return/drawdown {sum(tot)/mdd:.2f}")

    if survivors:
        print("\n=== corrected portfolio ===")
        for s in survivors:
            print(f"   {s[0]} x {s[1]}")
        stats(survivors, "CORRECTED SURVIVOR PORTFOLIO")
    stats(list(cells), "WHOLE BOOK (corrected)")


if __name__ == "__main__":
    main()
