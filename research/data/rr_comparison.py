"""Fixed-RR comparison — no per-cell cherry-picking.

portfolio_report.py reports each cell at ITS best RR, which is in-sample
selection across four choices and inflates the total. This holds one RR for the
whole book at a time, which is the number you could actually have traded.
"""
from __future__ import annotations
import json, math
from pathlib import Path
import numpy as np

HERE = Path(__file__).parent
RES = json.loads((HERE / "full_backtest_results.json").read_text())
RRS = ("2.0", "3.0", "4.0", "5.0")


def agg(cells, rr):
    ms = [c["rr"][rr] for c in cells if rr in c["rr"]]
    if not ms:
        return None
    pnl = sum(m["pnl"] for m in ms)
    tr = sum(m["trades"] for m in ms)
    wins = sum(m["wr"] * m["trades"] for m in ms)
    prof = sum(1 for m in ms if m["pnl"] > 0)
    return dict(cells=len(ms), trades=tr, pnl=pnl,
                wr=wins / tr if tr else 0,
                prof=prof, dd=float(np.mean([m["maxdd_pct"] for m in ms])),
                ddmax=float(np.max([m["maxdd_pct"] for m in ms])),
                daydd=float(np.mean([m["max_daily_dd_pct"] for m in ms])),
                expr=float(np.mean([m["exp_r"] for m in ms])))


def main():
    for broker in ("Deriv", "FundedNext"):
        cells = [v for v in RES.values() if v["broker"] == broker]
        if not cells:
            continue
        print("=" * 100)
        print(f"{broker} — FIXED RR ACROSS THE WHOLE BOOK ($10,000/cell, 1% risk)")
        print("=" * 100)
        print(f"{'RR':>6s}{'cells':>7s}{'trades':>8s}{'total P&L':>14s}"
              f"{'expR':>9s}{'WR':>8s}{'profitable':>12s}{'meanDD%':>9s}"
              f"{'worstDD%':>10s}{'meanDayDD%':>12s}")
        for rr in RRS:
            a = agg(cells, rr)
            if not a:
                continue
            print(f"  1:{rr[0]:>3s}{a['cells']:7d}{a['trades']:8d}"
                  f"{'$' + format(a['pnl'], '+,.2f'):>14s}{a['expr']:+9.4f}"
                  f"{a['wr']:8.1%}{a['prof']:>8d}/{a['cells']:<4d}"
                  f"{a['dd']:9.2f}{a['ddmax']:10.2f}{a['daydd']:12.2f}")

        # per strategy at each RR
        print(f"\n  PER-STRATEGY, FIXED RR — total P&L")
        strats = sorted({c["strategy"] for c in cells})
        print(f"    {'strategy':20s}" + "".join(f"{'1:' + r[0]:>14s}" for r in RRS)
              + f"{'best':>8s}")
        for s in strats:
            sub = [c for c in cells if c["strategy"] == s]
            vals = {}
            line = f"    {s.replace('_v1',''):20s}"
            for rr in RRS:
                a = agg(sub, rr)
                vals[rr] = a["pnl"] if a else float("nan")
                line += f"{'$' + format(vals[rr], '+,.0f'):>14s}" if a else f"{'-':>14s}"
            best = max(vals, key=lambda k: vals[k] if vals[k] == vals[k] else -1e18)
            print(line + f"{'1:' + best[0]:>8s}")
        print()

    # cross-broker, matched symbols only
    print("=" * 100)
    print("SAME-SYMBOL COMPARISON (instruments present on BOTH brokers)")
    print("=" * 100)
    MAP = {"US Tech 100": "NDX100", "US SP 500": "SPX500", "Germany 40": "GER30",
           "Netherlands 25": "NTH25", "Hong Kong 50": "HK50"}
    inv = {v: k for k, v in MAP.items()}
    d = {v["symbol"]: v for v in RES.values() if v["broker"] == "Deriv"}
    f = {v["symbol"]: v for v in RES.values() if v["broker"] == "FundedNext"}
    dsyms = {c["symbol"] for c in RES.values() if c["broker"] == "Deriv"}
    fsyms = {c["symbol"] for c in RES.values() if c["broker"] == "FundedNext"}
    pairs = []
    for ds in dsyms:
        fs = MAP.get(ds, ds)
        if fs in fsyms:
            pairs.append((ds, fs))
    print(f"{'instrument':22s}{'Deriv P&L 1:3':>16s}{'FN P&L 1:3':>15s}{'winner':>12s}")
    tot_d = tot_f = 0.0
    for ds, fs in sorted(pairs):
        dc = [v for v in RES.values() if v["broker"] == "Deriv" and v["symbol"] == ds]
        fc = [v for v in RES.values() if v["broker"] == "FundedNext" and v["symbol"] == fs]
        ad, af = agg(dc, "3.0"), agg(fc, "3.0")
        if not ad or not af:
            continue
        tot_d += ad["pnl"]; tot_f += af["pnl"]
        w = "Deriv" if ad["pnl"] > af["pnl"] else "FundedNext"
        print(f"{fs:22s}{'$' + format(ad['pnl'], '+,.0f'):>16s}"
              f"{'$' + format(af['pnl'], '+,.0f'):>15s}{w:>12s}")
    print(f"{'TOTAL':22s}{'$' + format(tot_d, '+,.0f'):>16s}"
          f"{'$' + format(tot_f, '+,.0f'):>15s}")


if __name__ == "__main__":
    main()
