#!/usr/bin/env python
"""
scripts/run_trend_challenge.py

The trend system against prop-firm rules, on its real R sequence.

This is the first strategy measured on real CFDs this session with genuine
statistical significance: 937 trades over five years, expectancy +0.067R,
t +2.50, win rate 40.6%, payoff 1.84, and the tail shape a trend system should
have (top five trades are a third of the profit).

Its weakness is speed, not quality: +1.1 R/month. So the question is not "can it
pass" but "how long does it take, and how often does it die first" — which is
exactly what the earlier no-time-limit analysis was built to answer.

Bootstrapped from the actual trade sequence rather than a fitted distribution,
because the trade-size distribution IS the strategy.

    python scripts/run_trend_challenge.py
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

OUT = ROOT / "data" / "zone_study"

FIRMS = {
    "fundednext": dict(target=0.10, daily=0.05, maxdd=0.10, min_days=5, label="FundedNext (10/5/10)"),
    "bloomfunded": dict(target=0.08, daily=0.04, maxdd=0.06, min_days=4, label="BloomFunded (8/4/6)"),
}


def simulate(pool: np.ndarray, risk: float, per_day: float, rules: dict,
             horizon_days: int, rng: np.random.Generator) -> dict:
    bal, floor = 1.0, 1.0 - rules["maxdd"]
    traded = 0
    for day in range(horizon_days):
        n = rng.poisson(per_day)
        start = bal
        if n:
            traded += 1
        for _ in range(n):
            bal += float(rng.choice(pool)) * risk
            if bal <= floor:
                return {"r": "breach", "d": day + 1}
            if bal <= start - rules["daily"]:
                break
        if bal - 1.0 >= rules["target"] and traded >= rules["min_days"]:
            return {"r": "pass", "d": day + 1}
    return {"r": "timeout", "d": horizon_days}


def run(pool, risk, per_day, rules, trials, horizon, seed=3):
    rng = np.random.default_rng(seed)
    out = [simulate(pool, risk, per_day, rules, horizon, rng) for _ in range(trials)]
    passed = np.array([o["d"] for o in out if o["r"] == "pass"])
    marks = [30, 60, 90, 120, 180, 365]
    return {
        "p_pass": sum(1 for o in out if o["r"] == "pass") / trials,
        "p_breach": sum(1 for o in out if o["r"] == "breach") / trials,
        "by_day": {m: float((passed <= m).sum()) / trials for m in marks if m <= horizon},
        "median": float(np.median(passed)) if len(passed) else float("nan"),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="trend_deriv.json")
    ap.add_argument("--trials", type=int, default=4000)
    ap.add_argument("--horizon", type=int, default=365)
    args = ap.parse_args()

    d = json.loads((OUT / args.source).read_text())
    pool = np.array(d["r_sequence"])
    s = d["summary"]
    per_day = s["trades_per_month"] / 21.0

    print(f"\n{args.source}: {len(pool)} trades | expectancy {pool.mean():+.4f}R "
          f"| win {(pool > 0).mean() * 100:.1f}% | {per_day:.2f} trades/day "
          f"| {s['r_per_month']:+.2f} R/month")

    for key, rules in FIRMS.items():
        print(f"\n{rules['label']} — cumulative PASS% by calendar day, no time limit")
        marks = [30, 60, 90, 120, 180, 365]
        print(f"  {'risk':>6s} " + " ".join(f"{('d' + str(m)):>7s}" for m in marks)
              + f" {'breach':>8s}")
        for risk in (0.005, 0.0075, 0.01, 0.015, 0.02, 0.03):
            r = run(pool, risk, per_day, rules, args.trials, args.horizon)
            cells = " ".join(f"{r['by_day'].get(m, 0) * 100:>6.1f}%" for m in marks)
            print(f"  {risk * 100:>5.2f}% {cells} {r['p_breach'] * 100:>7.1f}%")

    print("\n  (the strategy is slow, not fragile — read the breach column)")
    (OUT / "trend_challenge.json").write_text(json.dumps({"source": args.source}, indent=1))


if __name__ == "__main__":
    main()
