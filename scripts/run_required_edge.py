#!/usr/bin/env python
"""
scripts/run_required_edge.py

What quality of edge does "pass in one month" actually require?

Everything so far has asked "does this strategy work". This asks the question
underneath it: given the rules, what Sharpe ratio would a strategy need for a
one-month pass to be *likely* rather than lucky? The answer does not depend on
which strategy you pick, so it settles whether to keep searching.

The barriers are the real ones:
    target        +10% of starting balance
    daily loss     -5%
    total drawdown -10% STATIC, checked on the running equity, not the close
    minimum days    5
    horizon        20 or 40 trading days

A strategy is described by only two numbers here — annualised Sharpe and
annualised volatility — because that is all that matters to a barrier problem.
Volatility is set by position sizing, so it is the dial you actually control;
Sharpe is the edge, and it is the thing you cannot conjure.

    python scripts/run_required_edge.py
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "zone_study"

TARGET, DAILY, MAXDD, MIN_DAYS = 0.10, 0.05, 0.10, 5
TRADING_DAYS = 252


def run(sharpe: float, ann_vol: float, horizon: int, trials: int,
        steps_per_day: int = 4, seed: int = 11) -> dict:
    """Daily path with intraday granularity, so a drawdown breach is caught
    when it happens rather than at the day's close."""
    rng = np.random.default_rng(seed)
    dt = 1.0 / (TRADING_DAYS * steps_per_day)
    mu = sharpe * ann_vol
    drift = mu * dt
    shock = ann_vol * np.sqrt(dt)

    n = trials
    bal = np.ones(n)
    floor = 1.0 - MAXDD
    alive = np.ones(n, bool)
    passed = np.zeros(n, bool)
    breached = np.zeros(n, bool)
    day_start = np.ones(n)

    for day in range(horizon):
        day_start[alive] = bal[alive]
        for _ in range(steps_per_day):
            z = rng.standard_normal(n)
            bal = np.where(alive, bal + drift + shock * z, bal)
            hit_dd = alive & (bal <= floor)
            breached |= hit_dd
            alive &= ~hit_dd
            # daily loss cap: flat for the rest of the day (approximated by
            # freezing the path), which is what a firm's rule actually does
            hit_daily = alive & (bal <= day_start - DAILY)
            alive &= ~hit_daily | alive          # keep alive, stop trading today
            bal = np.where(hit_daily, day_start - DAILY, bal)
        if day + 1 >= MIN_DAYS:
            win = alive & (bal >= 1.0 + TARGET) & ~passed
            passed |= win
            alive &= ~win

    return {"sharpe": sharpe, "ann_vol": ann_vol, "horizon": horizon,
            "p_pass": float(passed.mean()), "p_breach": float(breached.mean()),
            "p_neither": float(1 - passed.mean() - breached.mean())}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=20000)
    ap.add_argument("--horizons", nargs="*", type=int, default=[20, 40, 60])
    args = ap.parse_args()

    sharpes = [0.0, 0.5, 1.0, 1.5, 2.0, 3.0, 5.0]
    vols = [0.15, 0.30, 0.50, 0.80, 1.20]

    report = {}
    for horizon in args.horizons:
        print(f"\n{'=' * 78}\nHORIZON {horizon} TRADING DAYS "
              f"({'about one month' if horizon == 20 else f'about {horizon // 20} months'})")
        print(f"{'ann vol':>8s} | " + " ".join(f"{('S=' + str(s)):>11s}" for s in sharpes))
        print("-" * 78)
        for vol in vols:
            cells = []
            for s in sharpes:
                r = run(s, vol, horizon, args.trials)
                cells.append(f"{r['p_pass'] * 100:>5.1f}/{r['p_breach'] * 100:<5.1f}")
                report[f"h{horizon}_v{vol}_s{s}"] = r
            print(f"{vol * 100:>7.0f}% | " + " ".join(f"{c:>11s}" for c in cells))
        print("  cells are  PASS% / BREACH%")

    print(f"\n{'=' * 78}\nWHAT THE TABLE SAYS")
    r0 = run(0.0, 0.50, 20, args.trials)
    print(f"  a strategy with NO edge at 50% vol passes {r0['p_pass'] * 100:.1f}% "
          f"of one-month challenges and breaches {r0['p_breach'] * 100:.1f}%")
    for s in (1.0, 2.0, 3.0, 5.0):
        best = max((run(s, v, 20, args.trials) for v in vols), key=lambda x: x["p_pass"])
        print(f"  Sharpe {s:>3.1f}: best one-month pass rate {best['p_pass'] * 100:>5.1f}% "
              f"at {best['ann_vol'] * 100:>3.0f}% vol (breach {best['p_breach'] * 100:.1f}%)")

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "required_edge.json").write_text(json.dumps(report, indent=1))
    print(f"\n-> {OUT / 'required_edge.json'}")


if __name__ == "__main__":
    main()
