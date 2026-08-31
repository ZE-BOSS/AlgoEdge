"""Analysis of the full-window run: per asset, cumulative, fixed-RR, correlation.

Reads results_full/*.json (285 cells, 238-242 day window on every asset, so
cross-asset comparison is finally like-for-like).
"""
from __future__ import annotations
import json, glob, math
from pathlib import Path
from collections import defaultdict
import numpy as np

HERE = Path(__file__).parent
RRS = ("2.0", "3.0", "4.0", "5.0")
MIN_N = 30          # sample floor for a verdict (plan rule 0.5-5)


def load():
    out = []
    for f in glob.glob(str(HERE / "results_full" / "*.json")):
        j = json.load(open(f))
        if "rr" in j:
            out.append(j)
    return out


def d(v):
    return f"${v:+,.0f}"


def main():
    cells = load()
    print(f"{len(cells)} cells | window {min(c['days'] for c in cells)}-"
          f"{max(c['days'] for c in cells)} days\n")

    for broker in ("Deriv", "FundedNext"):
        bc = [c for c in cells if c["broker"] == broker]
        print("=" * 104)
        print(f"{broker}  —  FIXED RR ACROSS THE BOOK (no per-cell selection)")
        print("=" * 104)
        print(f"{'RR':>6s}{'cells':>7s}{'trades':>8s}{'total P&L':>14s}{'expR':>9s}"
              f"{'WR':>8s}{'profitable':>12s}{'meanDD%':>9s}{'worstDD%':>10s}{'dayDD%':>8s}")
        for rr in RRS:
            ms = [c["rr"][rr] for c in bc if rr in c["rr"]]
            if not ms:
                continue
            tr = sum(m["trades"] for m in ms)
            pnl = sum(m["pnl"] for m in ms)
            wr = sum(m["wr"] * m["trades"] for m in ms) / tr if tr else 0
            print(f"  1:{rr[0]:>3s}{len(ms):7d}{tr:8d}{d(pnl):>14s}"
                  f"{np.mean([m['exp_r'] for m in ms]):+9.4f}{wr:8.1%}"
                  f"{sum(1 for m in ms if m['pnl']>0):>8d}/{len(ms):<4d}"
                  f"{np.mean([m['maxdd_pct'] for m in ms]):9.2f}"
                  f"{max(m['maxdd_pct'] for m in ms):10.2f}"
                  f"{np.mean([m['max_daily_dd_pct'] for m in ms]):8.2f}")

        # per strategy
        print(f"\n  PER-STRATEGY (fixed RR, total P&L)")
        print(f"    {'strategy':18s}" + "".join(f"{'1:'+r[0]:>13s}" for r in RRS)
              + f"{'trades':>9s}{'best':>7s}")
        for s in sorted({c["strategy"] for c in bc}):
            sub = [c for c in bc if c["strategy"] == s]
            vals, line = {}, f"    {s.replace('_v1',''):18s}"
            for rr in RRS:
                ms = [c["rr"][rr] for c in sub if rr in c["rr"]]
                vals[rr] = sum(m["pnl"] for m in ms) if ms else float("nan")
                line += f"{d(vals[rr]):>13s}"
            n = sum(c["rr"]["3.0"]["trades"] for c in sub if "3.0" in c["rr"])
            best = max(vals, key=lambda k: vals[k] if vals[k] == vals[k] else -9e18)
            print(line + f"{n:9d}{'1:'+best[0]:>7s}")

        # defensible winners at each RR
        print(f"\n  CELLS WITH n>={MIN_N} AND PROFITABLE — by RR")
        for rr in RRS:
            w = [(c, c["rr"][rr]) for c in bc
                 if rr in c["rr"] and c["rr"][rr]["trades"] >= MIN_N
                 and c["rr"][rr]["pnl"] > 0]
            tot = sum(m["pnl"] for _, m in w)
            print(f"    1:{rr[0]}  {len(w):3d} cells, total {d(tot)}")
        print()

    # ---- best cells overall, fixed 1:3 and 1:5 -------------------------
    for rr in ("3.0", "5.0"):
        print("=" * 104)
        print(f"TOP CELLS AT FIXED 1:{rr[0]}  (n>={MIN_N})")
        print("=" * 104)
        rows = [(c, c["rr"][rr]) for c in cells
                if rr in c["rr"] and c["rr"][rr]["trades"] >= MIN_N]
        rows.sort(key=lambda x: -x[1]["pnl"])
        print(f"{'broker':11s}{'symbol':20s}{'strategy':16s}{'P&L':>11s}{'ret%':>8s}"
              f"{'n':>5s}{'WR':>7s}{'PF':>6s}{'DD%':>7s}{'dayDD%':>8s}{'Sharpe':>8s}")
        for c, m in rows[:15]:
            sh = m["sharpe"]
            print(f"{c['broker']:11s}{c['symbol'][:19]:20s}"
                  f"{c['strategy'].replace('_v1','')[:15]:16s}{d(m['pnl']):>11s}"
                  f"{m['ret_pct']:8.1f}{m['trades']:5d}{m['wr']:7.1%}{m['pf']:6.2f}"
                  f"{m['maxdd_pct']:7.2f}{m['max_daily_dd_pct']:8.2f}"
                  f"{(f'{sh:.2f}' if sh==sh else 'n/a'):>8s}")
        print(f"\n  WORST 5")
        for c, m in rows[-5:]:
            print(f"{c['broker']:11s}{c['symbol'][:19]:20s}"
                  f"{c['strategy'].replace('_v1','')[:15]:16s}{d(m['pnl']):>11s}"
                  f"{m['ret_pct']:8.1f}{m['trades']:5d}{m['wr']:7.1%}{m['pf']:6.2f}"
                  f"{m['maxdd_pct']:7.2f}")
        print()

    # ---- shared-instrument broker comparison ---------------------------
    MAP = {"US Tech 100": "NDX100", "US SP 500": "SPX500", "Germany 40": "GER30",
           "Netherlands 25": "NTH25", "Hong Kong 50": "HK50"}
    print("=" * 104)
    print("SHARED INSTRUMENTS — Deriv vs FundedNext, fixed 1:3")
    print("=" * 104)
    dv = defaultdict(float); fn = defaultdict(float)
    for c in cells:
        if "3.0" not in c["rr"]:
            continue
        key = MAP.get(c["symbol"], c["symbol"])
        (dv if c["broker"] == "Deriv" else fn)[key] += c["rr"]["3.0"]["pnl"]
    shared = sorted(set(dv) & set(fn))
    print(f"{'instrument':16s}{'Deriv':>12s}{'FundedNext':>13s}{'winner':>13s}")
    td = tf = 0.0
    for k in shared:
        td += dv[k]; tf += fn[k]
        print(f"{k:16s}{d(dv[k]):>12s}{d(fn[k]):>13s}"
              f"{('Deriv' if dv[k]>fn[k] else 'FundedNext'):>13s}")
    print(f"{'TOTAL':16s}{d(td):>12s}{d(tf):>13s}")
    print(f"  Deriv wins {sum(1 for k in shared if dv[k]>fn[k])}/{len(shared)}")


if __name__ == "__main__":
    main()
