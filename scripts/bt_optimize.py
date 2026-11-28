"""
scripts/bt_optimize.py - what to keep, what target, and what risk, from one saved portfolio run.

Works in R from the run's own trades (each slot trades independently of the others since the
28 Nov fixes, so a subset's trades are exactly that subset's trades in the full run):

  1. per slot: what a CLOSER fixed target would have given, from each trade's MFE (a target at X R
     is hit if the trade got X R in its favour before it ended; otherwise the trade ends as it did).
     Only targets at or below the run's own can be judged this way.
  2. which slots to keep: every subset of the slots, ranked by return / max drawdown (R),
     chosen on January-June and judged on July-November so the pick isn't fitted to the year.
  3. risk per trade: the kept slots compounded at several risk levels - final balance and max
     drawdown in money.

    cd /opt/algoedge/<inst>/app && PYTHONPATH=. ../venv/bin/python scripts/bt_optimize.py <run id>
"""
from __future__ import annotations

import argparse
import asyncio
import itertools
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))


def dd(rs):
    eq = np.r_[0.0, np.cumsum(rs)]
    return float((np.maximum.accumulate(eq) - eq).max())


def line(rs):
    rs = np.asarray(rs, float)
    if not len(rs):
        return "0 tr"
    gp, gl = rs[rs > 0].sum(), -rs[rs < 0].sum()
    return (f"{len(rs):4d} tr win {np.mean(rs > 0):5.1%} sumR {rs.sum():+6.1f} avg {rs.mean():+.3f} "
            f"PF {gp / gl if gl else float('inf'):.2f} DD {dd(rs):4.1f}R")


def compound(rs, risk_pct, bal=3000.0):
    peak, mdd = bal, 0.0
    for r in rs:
        bal *= 1 + risk_pct / 100.0 * r
        peak = max(peak, bal)
        mdd = max(mdd, 1 - bal / peak)
    return bal, mdd


async def main() -> int:
    import bt_runs as B
    ap = argparse.ArgumentParser()
    ap.add_argument("id")
    ap.add_argument("--split", default="2026-07-01")
    ap.add_argument("--balance", type=float, default=3000.0)
    a = ap.parse_args()
    runs = await B.load(days=30)
    r0 = next(r for r in runs if r.id.startswith(a.id))
    run = (await B.load(ids=[r0.id]))[0]
    ts = sorted(run.trades, key=lambda t: (t.exit_time, t.id))
    g = defaultdict(list)
    for t in ts:
        g[f"{t.strategy_id.replace('_v1', '')}@{t.symbol}"].append(t)

    print("== 1. closer fixed targets, from each trade's MFE (win rate vs R per trade)")
    for k, v in sorted(g.items(), key=lambda kv: -sum(t.pnl_r or 0 for t in kv[1])):
        act = [t.pnl_r or 0 for t in v]
        cur_t = max((t.pnl_r or 0) for t in v)
        out = [f"actual {line(act)}"]
        for x in (0.5, 1.0, 1.5, 2.0):
            if x >= cur_t * 0.98:
                continue
            rs = [x if (t.mfe_r or 0) >= x else (t.pnl_r or 0) for t in v]
            out.append(f"@{x}R win {np.mean(np.array(rs) > 0):.0%} avg {np.mean(rs):+.3f} DD {dd(rs):.1f}R")
        print(f"  {k[:36]:36s} " + " | ".join(out))

    print("\n== 2. which slots to keep (chosen on H1, judged on H2; score = sumR / maxDD)")
    keys = list(g)
    cut = np.datetime64(a.split)
    rows = []
    for n in range(1, len(keys) + 1):
        for sub in itertools.combinations(keys, n):
            sel = [t for k in sub for t in g[k]]
            sel.sort(key=lambda t: t.exit_time)
            h1 = [t.pnl_r or 0 for t in sel if np.datetime64(t.exit_time) < cut]
            h2 = [t.pnl_r or 0 for t in sel if np.datetime64(t.exit_time) >= cut]
            if len(h1) < 60:
                continue
            s1 = sum(h1) / max(dd(h1), 1.0)
            rows.append((s1, sub, h1, h2))
    rows.sort(key=lambda x: -x[0])
    for s1, sub, h1, h2 in rows[:8]:
        s2 = sum(h2) / max(dd(h2), 1.0)
        print(f"  H1 score {s1:5.2f} -> H2 {s2:5.2f} | H1 {line(h1)} | H2 {line(h2)} | {len(sub)} slots: "
              + ", ".join(sub))
    allr = [t.pnl_r or 0 for t in ts]
    h1a = [t.pnl_r or 0 for t in ts if np.datetime64(t.exit_time) < cut]
    h2a = [t.pnl_r or 0 for t in ts if np.datetime64(t.exit_time) >= cut]
    print(f"  ALL {len(keys)} slots: H1 {sum(h1a) / max(dd(h1a), 1):.2f} -> H2 {sum(h2a) / max(dd(h2a), 1):.2f} | full {line(allr)}")

    best = rows[0][1] if rows else tuple(keys)
    sel = sorted([t for k in best for t in g[k]], key=lambda t: t.exit_time)
    rs = [t.pnl_r or 0 for t in sel]
    print(f"\n== 3. risk per trade, compounding the H1 pick ({len(best)} slots) from ${a.balance:,.0f} (no lot caps, no costs beyond the run's)")
    for rp in (0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 5.0):
        b, m = compound(rs, rp, a.balance)
        print(f"  {rp:4.2f}% -> ${b:>14,.0f}  ({b / a.balance - 1:+,.0%})  max drawdown {m:5.1%}")
    b, m = compound(allr, 1.0, a.balance)
    print(f"  (all {len(keys)} slots at 1.00% -> ${b:,.0f}, max drawdown {m:.1%})")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
