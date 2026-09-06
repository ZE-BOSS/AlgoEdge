"""Which symbol/strategy pairs actually diversify each other?

Two different correlations matter and they answer different questions:

1. **Instrument correlation** — do the underlying price series move together?
   Deriv generates each synthetic independently, so this should be ~0 and any
   diversification benefit is real rather than assumed.
2. **Strategy P&L correlation** — do the STRATEGIES lose on the same days? This is
   the one that decides portfolio risk, and it can be high even when the
   instruments are independent, because a long-the-grind rule on Crash 300 and the
   same rule on Crash 1000 are the same bet expressed twice: both lose exactly
   when a spike arrives.

The live screenshots show 11 simultaneous positions, all long, almost all on Crash
indices. If (2) is high across those, the account is running one concentrated bet
at 11x the nominal per-trade risk, not a diversified book.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
DBG = HERE.parents[1] / "debug" / "backtest"
TICKS = HERE / "ticks"

CODES = {
    "BOOM1000": "Boom 1000", "BOOM500": "Boom 500",
    "CRASH1000": "Crash 1000", "CRASH500": "Crash 500",
    "R_75": "Vol 75", "R_25": "Vol 25", "R_100": "Vol 100",
    "stpRNG": "Step", "RB100": "RangeBrk 100", "RB200": "RangeBrk 200",
    "JD25": "Jump 25", "JD100": "Jump 100",
}


def instrument_corr() -> None:
    print("=" * 100)
    print("1. INSTRUMENT CORRELATION — daily log returns from ticks")
    print("=" * 100)
    series = {}
    for code, name in CODES.items():
        f = sorted(TICKS.glob(f"{code}_*d.npz"), key=lambda x: x.stat().st_size)
        if not f:
            continue
        z = np.load(f[-1], allow_pickle=True)
        t, p = z["t"].astype(np.int64), z["p"].astype(float)
        day = t // 86400
        edges = np.flatnonzero(np.diff(day)) + 1
        starts = np.concatenate(([0], edges))
        ends = np.concatenate((edges, [len(day)]))
        closes = p[ends - 1]
        series[name] = dict(zip(day[starts].tolist(), np.concatenate(
            ([np.nan], np.diff(np.log(closes)))).tolist()))
    names = list(series)
    common = sorted(set.intersection(*[set(v) for v in series.values()]))
    M = np.array([[series[n].get(d, np.nan) for d in common] for n in names])
    ok = ~np.isnan(M).any(axis=0)
    M = M[:, ok]
    C = np.corrcoef(M)
    print(f"  {len(common)} common days, {M.shape[1]} usable\n")
    print("        " + "".join(f"{n[:8]:>10s}" for n in names))
    for i, n in enumerate(names):
        print(f"{n[:8]:8s}" + "".join(f"{C[i, j]:10.2f}" for j in range(len(names))))
    off = C[np.triu_indices_from(C, 1)]
    print(f"\n  mean |pairwise correlation| = {np.abs(off).mean():.3f}"
          f"   max = {np.abs(off).max():.3f}")


def strategy_corr() -> None:
    print("\n" + "=" * 100)
    print("2. STRATEGY DAILY-P&L CORRELATION — from the user's own backtests")
    print("=" * 100)
    daily = {}
    for f in sorted(DBG.glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        tr = d.get("trades") or []
        if not tr:
            continue
        label = f"{tr[0]['symbol'].replace(' Index','')}/{tr[0]['strategy_id'].replace('_v1','')}"
        acc = defaultdict(float)
        for t in tr:
            xt = t.get("exit_time")
            if xt:
                # normalise by balance so a compounding run does not dominate
                bb = float(t.get("balance_before") or 10000) or 10000
                acc[int(xt) // 86400] += float(t.get("pnl") or 0) / bb
        daily[label] = acc
    labels = list(daily)
    common = sorted(set.intersection(*[set(v) for v in daily.values()]))
    if len(common) < 30:
        print(f"  only {len(common)} common days — too few")
        return
    M = np.array([[daily[l].get(d, 0.0) for d in common] for l in labels])
    C = np.corrcoef(M)
    print(f"  {len(common)} common trading days, {len(labels)} cells\n")
    print(f"{'cell':30s}" + "".join(f"{i:>5d}" for i in range(len(labels))))
    for i, l in enumerate(labels):
        print(f"{i:>2d} {l[:27]:28s}" + "".join(f"{C[i, j]:5.2f}" for j in range(len(labels))))

    print("\n  MOST CORRELATED PAIRS (same bet twice):")
    pairs = [(C[i, j], labels[i], labels[j])
             for i in range(len(labels)) for j in range(i + 1, len(labels))]
    for c, a, b in sorted(pairs, reverse=True)[:10]:
        print(f"    {c:+.2f}  {a}  <->  {b}")
    print("\n  LEAST CORRELATED PAIRS (genuine diversification):")
    for c, a, b in sorted(pairs)[:10]:
        print(f"    {c:+.2f}  {a}  <->  {b}")

    off = np.array([p[0] for p in pairs])
    print(f"\n  mean pairwise correlation = {off.mean():+.3f}")
    n_eff = len(labels) / (1 + (len(labels) - 1) * max(off.mean(), 0))
    print(f"  effective independent bets across {len(labels)} cells = {n_eff:.1f}")


if __name__ == "__main__":
    instrument_corr()
    strategy_corr()
