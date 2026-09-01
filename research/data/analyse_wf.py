"""Walk-forward analysis: do report 16's conclusions survive out of sample?

Every cell carries metrics for the full window plus the in-sample (Jan-Apr) and
out-of-sample (May-Aug) halves. Rule 0.5-6: a rule chosen by looking at results
must be re-tested on a period it was not chosen on.

Three questions:
  1. Does the R:R choice made on IS hold up on OOS?
  2. Does "DriftJumpAlpha is the entire book" survive?
  3. Do the per-slot defaults now shipped in strategy_defaults.py survive?
"""
from __future__ import annotations
import json, glob
from pathlib import Path
from collections import defaultdict
import numpy as np

HERE = Path(__file__).parent
RRS = ("2.0", "3.0", "4.0", "5.0")
MIN_N = 30


def load():
    out = []
    for f in glob.glob(str(HERE / "results_wf" / "*.json")):
        j = json.load(open(f))
        if "rr" in j and "wf" in j:
            out.append(j)
    return out


def d(v):
    return f"${v:+,.0f}"


def main():
    cells = load()
    print(f"{len(cells)} cells with an IS/OOS split\n")

    # ── 1. does the IS-chosen R:R hold on OOS? ───────────────────────────
    print("=" * 88)
    print("1. R:R CHOSEN ON JAN-APR, SCORED ON MAY-AUG")
    print("=" * 88)
    print(f"{'broker':11s}{'rule':34s}{'OOS P&L':>13s}{'OOS expR':>10s}")
    for broker in ("Deriv", "FundedNext"):
        bc = [c for c in cells if c["broker"] == broker]
        # fixed RR, whole book
        for rr in RRS:
            oos = [c["wf"][rr]["oos"] for c in bc
                   if rr in c.get("wf", {}) and c["wf"][rr].get("oos")]
            if oos:
                print(f"{broker:11s}{'fixed 1:' + rr[0]:34s}"
                      f"{d(sum(m['pnl'] for m in oos)):>13s}"
                      f"{np.mean([m['exp_r'] for m in oos]):+10.4f}")
        # per-cell best chosen on IS only
        tot, exps = 0.0, []
        for c in bc:
            cand = {rr: c["wf"][rr] for rr in RRS
                    if rr in c.get("wf", {}) and c["wf"][rr].get("is")
                    and c["wf"][rr].get("oos")}
            if not cand:
                continue
            best = max(cand, key=lambda r: cand[r]["is"]["pnl"])
            tot += cand[best]["oos"]["pnl"]
            exps.append(cand[best]["oos"]["exp_r"])
        if exps:
            print(f"{broker:11s}{'per-cell best R:R chosen on IS':34s}"
                  f"{d(tot):>13s}{np.mean(exps):+10.4f}   <-- honest selection")
        print()

    # ── 2. per-strategy IS vs OOS ────────────────────────────────────────
    print("=" * 88)
    print("2. PER STRATEGY — does the edge persist?  (at each strategy's IS-best R:R)")
    print("=" * 88)
    print(f"{'strategy':18s}{'IS-best':>9s}{'IS P&L':>12s}{'OOS P&L':>12s}"
          f"{'IS expR':>10s}{'OOS expR':>10s}{'holds?':>9s}")
    per = defaultdict(lambda: defaultdict(lambda: {"is": 0.0, "oos": 0.0,
                                                   "ise": [], "oose": []}))
    for c in cells:
        for rr in RRS:
            w = c.get("wf", {}).get(rr) or {}
            if w.get("is") and w.get("oos"):
                p = per[c["strategy"]][rr]
                p["is"] += w["is"]["pnl"]; p["oos"] += w["oos"]["pnl"]
                p["ise"].append(w["is"]["exp_r"]); p["oose"].append(w["oos"]["exp_r"])
    for s in sorted(per):
        best = max(per[s], key=lambda r: per[s][r]["is"])
        p = per[s][best]
        holds = "YES" if p["oos"] > 0 else ("weaker" if p["oos"] > p["is"] else "no")
        print(f"{s.replace('_v1',''):18s}{'1:' + best[0]:>9s}{d(p['is']):>12s}"
              f"{d(p['oos']):>12s}{np.mean(p['ise']):+10.4f}"
              f"{np.mean(p['oose']):+10.4f}{holds:>9s}")

    # ── 3. the shipped per-slot defaults ─────────────────────────────────
    import sys
    sys.path.insert(0, str(HERE.parents[1]))
    from backend.strategies.strategy_defaults import SLOT_TP1_RR

    print("\n" + "=" * 88)
    print("3. THE PER-SLOT DEFAULTS NOW SHIPPING — do they hold out of sample?")
    print("=" * 88)
    print(f"{'symbol':22s}{'strategy':16s}{'ship':>6s}{'IS P&L':>11s}"
          f"{'OOS P&L':>11s}{'OOS n':>7s}{'verdict':>10s}")
    idx = {(c["symbol"].upper(), c["strategy"]): c for c in cells}
    kept = dropped = 0
    for key, rr_ship in sorted(SLOT_TP1_RR.items()):
        sym, sid = key.split("|", 1)
        c = idx.get((sym, sid))
        if not c:
            print(f"{sym[:21]:22s}{sid.replace('_v1',''):16s}{rr_ship:6.1f}"
                  f"{'—':>11s}{'—':>11s}{'—':>7s}{'no data':>10s}")
            continue
        w = c.get("wf", {}).get(f"{rr_ship:.1f}") or {}
        i, o = w.get("is"), w.get("oos")
        if not (i and o):
            print(f"{sym[:21]:22s}{sid.replace('_v1',''):16s}{rr_ship:6.1f}"
                  f"{'—':>11s}{'—':>11s}{'—':>7s}{'thin':>10s}")
            continue
        ok = o["pnl"] > 0
        kept += ok; dropped += (not ok)
        print(f"{sym[:21]:22s}{sid.replace('_v1',''):16s}{rr_ship:6.1f}"
              f"{d(i['pnl']):>11s}{d(o['pnl']):>11s}{o['trades']:>7d}"
              f"{('HOLDS' if ok else 'FAILS'):>10s}")
    print(f"\n  {kept} hold out of sample, {dropped} fail")


if __name__ == "__main__":
    main()
