"""Run the full analysis stack against ONE broker's cache, then compare.

Same code paths as the Deriv work, pointed at whichever cache ALGOEDGE_CACHE
names. Reports drawdown everywhere (it was missing from the earlier write-ups).

  PART 1  cost and raw geometry   - what does a random entry cost and return?
  PART 2  exit ladders            - report 11 re-run
  PART 3  Riptide                 - report 12 re-run, 1:2 / 1:3 / 1:5, with DD
"""
from __future__ import annotations
import os, sys
import numpy as np

from exit_lab import LADDERS, load, simulate, summarise, hurst, atr, CACHE
from riptide import signals, backtest

# Deriv name -> FundedNext name, for side-by-side reporting
PAIRS = [
    ("BTCUSD", "BTCUSD"), ("ETHUSD", "ETHUSD"), ("XRPUSD", "XRPUSD"),
    ("US Tech 100", "NDX100"), ("US SP 500", "SPX500"),
    ("Germany 40", "GER30"), ("Netherlands 25", "NTH25"),
    ("Hong Kong 50", "HK50"),
    ("EURUSD", "EURUSD"), ("GBPUSD", "GBPUSD"), ("USDJPY", "USDJPY"),
    ("GBPJPY", "GBPJPY"), ("EURGBP", "EURGBP"), ("USDCHF", "USDCHF"),
    ("AUDUSD", "AUDUSD"),
    ("XAUUSD", "XAUUSD"), ("XAGUSD", "XAGUSD"), ("XPTUSD", "XPTUSD"),
]

BROKER = "FundedNext" if os.environ.get("ALGOEDGE_CACHE") == "cache_fn" else "Deriv"
COL = 1 if BROKER == "FundedNext" else 0
STOP_MULT, TARGET = 1.0, 5.0


def cost_r(b, stop_mult=STOP_MULT):
    a = atr(b["high"], b["low"], b["close"])
    med = np.nanmedian(stop_mult * a)
    return (b["spread"] * b["point"]) / med if med > 0 else float("nan")


def main():
    syms = [p[COL] for p in PAIRS]
    print(f"### BROKER: {BROKER}   cache={CACHE.name}\n")

    # ---------------- PART 1 : cost + raw geometry ----------------
    print("=" * 92)
    print("PART 1 - COST AND RAW GEOMETRY (random entry, 1xATR stop, 5R target)")
    print("=" * 92)
    print(f"{'symbol':14s}{'bars':>7s}{'spread(px)':>12s}{'cost R':>9s}"
          f"{'H':>7s}{'BUY expR':>10s}{'SELL expR':>11s}{'best DD':>9s}")
    geo = {}
    for s in syms:
        b = load(s, "M15")
        if b is None:
            print(f"{s:14s}  no data")
            continue
        c = cost_r(b)
        hh = hurst(b["close"])
        row = {}
        for d in ("BUY", "SELL"):
            rs = simulate(b, d, STOP_MULT, TARGET, [], cost_r=c)
            st = summarise(rs)
            row[d] = st
        best = max(row.values(), key=lambda x: x["exp"])
        geo[s] = dict(cost=c, hurst=hh, buy=row["BUY"]["exp"],
                      sell=row["SELL"]["exp"], dd=best["dd"])
        print(f"{s:14s}{len(b['close']):7d}{b['spread']*b['point']:12.5f}"
              f"{c:9.3f}{hh:7.3f}{row['BUY']['exp']:+10.4f}"
              f"{row['SELL']['exp']:+11.4f}{best['dd']:9.0f}")

    # ---------------- PART 2 : exit ladders ----------------
    print("\n" + "=" * 92)
    print("PART 2 - EXIT LADDERS (random entry isolates the exit method)")
    print("=" * 92)
    agg = {k: [] for k in LADDERS}
    aggdd = {k: [] for k in LADDERS}
    for s in syms:
        b = load(s, "M15")
        if b is None:
            continue
        c = cost_r(b)
        for name, lad in LADDERS.items():
            rs = np.concatenate([simulate(b, d, STOP_MULT, TARGET, lad, cost_r=c)
                                 for d in ("BUY", "SELL")])
            st = summarise(rs)
            if st:
                agg[name].append(st["exp"])
                aggdd[name].append(st["dd"])
    print(f"{'ladder':20s}{'mean expR':>12s}{'mean maxDD':>13s}{'beat fixed':>12s}")
    base = agg["fixed_TP_only"]
    for name in LADDERS:
        v, dd = agg[name], aggdd[name]
        if not v:
            continue
        won = sum(1 for i, x in enumerate(v) if x > base[i])
        print(f"{name:20s}{np.mean(v):+12.4f}{np.mean(dd):13.0f}"
              f"{won:>8d}/{len(v)}")

    # ---------------- PART 3 : Riptide ----------------
    print("\n" + "=" * 92)
    print("PART 3 - RIPTIDE  ($10,000 per asset, 1% risk, min confluence 2)")
    print("=" * 92)
    print(f"{'symbol':14s}{'sigs':>6s}" +
          "".join(f"{'1:%g P&L' % t:>11s}{'DD%':>7s}{'PF':>6s}" for t in (2, 3, 5)))
    rip = {}
    for s in syms:
        b = load(s, "M15")
        if b is None:
            continue
        sg = signals(b)
        if not sg:
            print(f"{s:14s}{0:6d}  no signals")
            continue
        med = np.median([abs(e - st_) for _, _, e, st_, _ in sg])
        cr = (b["spread"] * b["point"]) / med if med > 0 else 0.0
        line = f"{s:14s}{len(sg):6d}"
        rip[s] = {}
        for t in (2.0, 3.0, 5.0):
            r = backtest(b, sg, t, cost_r=cr)
            if r is None:
                line += f"{'-':>11s}{'-':>7s}{'-':>6s}"
                continue
            rip[s][t] = r
            line += f"{r['pnl']:+11.0f}{r['maxdd_pct']:7.1f}{r['pf']:6.2f}"
        print(line)
    for t in (2.0, 3.0, 5.0):
        v = [rip[s][t] for s in rip if t in rip[s]]
        if v:
            print(f"  1:{t:g} totals  P&L ${sum(x['pnl'] for x in v):+,.0f}   "
                  f"mean DD {np.mean([x['maxdd_pct'] for x in v]):.1f}%   "
                  f"profitable {sum(1 for x in v if x['pnl'] > 0)}/{len(v)}")

    np.save(CACHE.parent / f"geo_{BROKER}.npy", geo, allow_pickle=True)


if __name__ == "__main__":
    main()
