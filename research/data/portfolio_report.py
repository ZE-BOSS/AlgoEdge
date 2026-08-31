"""Portfolio view over the full backtest: per-asset, cumulative, correlation."""
from __future__ import annotations
import json, math
from pathlib import Path
import numpy as np

HERE = Path(__file__).parent
RES = json.loads((HERE / "full_backtest_results.json").read_text())


def fmt(v, nd=2, dollar=False):
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return "n/a"
    return (f"${v:+,.{nd}f}" if dollar else f"{v:,.{nd}f}")


def main():
    for broker in ("Deriv", "FundedNext"):
        cells = {k: v for k, v in RES.items() if v["broker"] == broker}
        if not cells:
            continue
        print("=" * 118)
        print(f"BROKER: {broker}   —   $10,000 per cell, 1% risk")
        print("=" * 118)
        print(f"{'symbol':20s}{'strategy':18s}{'RR':>5s}{'sig':>5s}{'trades':>7s}"
              f"{'P&L':>12s}{'ret%':>8s}{'WR':>7s}{'PF':>7s}{'maxDD%':>8s}"
              f"{'dayDD%':>8s}{'Sharpe':>8s}{'Sortino':>8s}")
        rows = []
        for k, v in sorted(cells.items()):
            best = None
            for rr, m in v["rr"].items():
                if best is None or m["pnl"] > best[1]["pnl"]:
                    best = (rr, m)
            if not best:
                continue
            rr, m = best
            rows.append((v, rr, m))
            print(f"{v['symbol'][:19]:20s}{v['strategy'].replace('_v1','')[:17]:18s}"
                  f"{rr[:3]:>5s}{v['signals']:5d}{m['trades']:7d}"
                  f"{fmt(m['pnl'], 2, True):>12s}{m['ret_pct']:8.2f}"
                  f"{m['wr']:7.1%}{m['pf']:7.2f}{m['maxdd_pct']:8.2f}"
                  f"{m['max_daily_dd_pct']:8.2f}{fmt(m['sharpe']):>8s}"
                  f"{fmt(m['sortino']):>8s}")

        # cumulative
        tot_pnl = sum(m["pnl"] for _, _, m in rows)
        tot_tr = sum(m["trades"] for _, _, m in rows)
        tot_sig = sum(v["signals"] for v, _, m in rows)
        prof = sum(1 for _, _, m in rows if m["pnl"] > 0)
        print("-" * 118)
        print(f"{'CUMULATIVE':38s}{tot_sig:5d}{tot_tr:7d}"
              f"{fmt(tot_pnl, 2, True):>12s}"
              f"   cells {prof}/{len(rows)} profitable"
              f"   mean maxDD {np.mean([m['maxdd_pct'] for _,_,m in rows]):.2f}%")

        # rejection accounting
        rej = {}
        for v, rr, m in rows:
            for reason, cnt in (m.get("rejected") or {}).items():
                rej[reason] = rej.get(reason, 0) + cnt
        print(f"  signals generated {tot_sig:,} | accepted {tot_tr:,} | "
              f"rejected {sum(rej.values()):,}  {rej}")

        # best cells
        print(f"\n  TOP 10 BY P&L ({broker})")
        for v, rr, m in sorted(rows, key=lambda x: -x[2]["pnl"])[:10]:
            print(f"    {v['symbol'][:18]:19s}{v['strategy'].replace('_v1',''):17s}"
                  f"1:{rr[:3]:4s}{fmt(m['pnl'],2,True):>11s}  DD {m['maxdd_pct']:5.2f}%"
                  f"  n={m['trades']:3d}  WR {m['wr']:5.1%}  PF {m['pf']:.2f}")
        print(f"\n  WORST 5 ({broker})")
        for v, rr, m in sorted(rows, key=lambda x: x[2]["pnl"])[:5]:
            print(f"    {v['symbol'][:18]:19s}{v['strategy'].replace('_v1',''):17s}"
                  f"1:{rr[:3]:4s}{fmt(m['pnl'],2,True):>11s}  DD {m['maxdd_pct']:5.2f}%"
                  f"  n={m['trades']:3d}  WR {m['wr']:5.1%}  PF {m['pf']:.2f}")

        # per-strategy rollup
        print(f"\n  PER-STRATEGY ROLLUP ({broker})")
        bys = {}
        for v, rr, m in rows:
            d = bys.setdefault(v["strategy"], {"pnl": 0, "n": 0, "cells": 0, "win": 0})
            d["pnl"] += m["pnl"]; d["n"] += m["trades"]; d["cells"] += 1
            d["win"] += 1 if m["pnl"] > 0 else 0
        print(f"    {'strategy':20s}{'cells':>7s}{'trades':>8s}{'P&L':>13s}{'profitable':>12s}")
        for s, d in sorted(bys.items(), key=lambda kv: -kv[1]["pnl"]):
            print(f"    {s.replace('_v1',''):20s}{d['cells']:7d}{d['n']:8d}"
                  f"{fmt(d['pnl'],2,True):>13s}{d['win']:>8d}/{d['cells']}")
        print()


if __name__ == "__main__":
    main()
