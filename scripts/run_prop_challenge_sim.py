#!/usr/bin/env python
"""
scripts/run_prop_challenge_sim.py

What does it actually take to pass a prop evaluation?

A challenge is not "is the strategy profitable". It is a race between a profit
target and two loss barriers, under a clock, and the barriers are checked on the
worst point of the path rather than on the final balance. A strategy with a fine
expectancy can still fail most of the time if its variance is large relative to
the drawdown floor.

RULES MODELLED (FundedNext Stellar 2-step, as published 2026-09)
  phase 1 target      +10%          phase 2 +5%
  max daily loss       -5%   of starting balance, reset 00:00 ET
  max overall drawdown -10%  STATIC floor at 90% of start (never trails)
  minimum trading days   5
  time limit            45 days (phase 1) / 90 (phase 2)
  consistency        no single day may be more than 40% of total profit

METHOD
  Bootstrap real R-multiple sequences (or a parametric win/loss profile),
  lay them onto trading days at the observed trades-per-day, size each trade at
  `risk_pct` of the STARTING balance, and walk the path barrier by barrier.

    python scripts/run_prop_challenge_sim.py --profile zone_eurusd
    python scripts/run_prop_challenge_sim.py --win-rate 0.45 --payoff 2.0 --per-day 3
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

TARGET_P1 = 0.10
DAILY_LOSS = 0.05
MAX_DD = 0.10
MIN_DAYS = 5
LIMIT_DAYS = 45
CONSISTENCY = 0.40


def simulate(r_pool: np.ndarray, risk_pct: float, per_day: float, rng: np.random.Generator,
             days: int = LIMIT_DAYS) -> dict:
    """One challenge attempt. Balance in units of the starting balance.

    `days` is the horizon, not a rule: pass a long one to see how long a pass
    actually takes, instead of scoring everything slower than 45 days a failure.
    """
    bal = 1.0
    floor = 1.0 - MAX_DD
    day_profits: list[float] = []
    traded_days = 0

    for day_i in range(days):
        n = rng.poisson(per_day)
        start_of_day = bal
        day_floor = start_of_day - DAILY_LOSS      # daily cap is on the day's own start
        if n:
            traded_days += 1
        for _ in range(n):
            r = float(rng.choice(r_pool))
            bal += r * risk_pct
            if bal <= floor:
                return {"result": "dd_breach", "days": traded_days,
                    "calendar_days": day_i + 1, "balance": bal}
            if bal <= day_floor:
                break                               # day's loss cap hit: stop trading today
        day_profits.append(bal - start_of_day)

        if bal - 1.0 >= TARGET_P1 and traded_days >= MIN_DAYS:
            best = max(day_profits) if day_profits else 0.0
            total = bal - 1.0
            if total > 0 and best / total > CONSISTENCY:
                continue                            # target hit but too lumpy: keep trading
            return {"result": "pass", "days": traded_days,
                    "calendar_days": day_i + 1, "balance": bal}

    return {"result": "timeout", "days": traded_days, "calendar_days": days, "balance": bal}


def run(r_pool: np.ndarray, risk_pct: float, per_day: float, n: int, seed: int = 7,
        horizon: int = LIMIT_DAYS) -> dict:
    rng = np.random.default_rng(seed)
    out = [simulate(r_pool, risk_pct, per_day, rng, days=horizon) for _ in range(n)]
    res = [o["result"] for o in out]
    passed = [o for o in out if o["result"] == "pass"]
    cal = np.array([o["calendar_days"] for o in passed]) if passed else np.array([])
    # cumulative pass rate by calendar day: the number the 45-day limit hides
    marks = [30, 45, 60, 90, 120, 180, 365]
    by_day = {m: float((cal <= m).sum()) / n for m in marks if m <= horizon}
    breach = [o for o in out if o["result"] == "dd_breach"]
    bcal = np.array([o["calendar_days"] for o in breach]) if breach else np.array([])
    return {
        "risk_pct": risk_pct,
        "p_pass": res.count("pass") / n,
        "p_dd_breach": res.count("dd_breach") / n,
        "p_timeout": res.count("timeout") / n,
        "pass_by_day": by_day,
        "breach_by_day": {m: float((bcal <= m).sum()) / n for m in marks if m <= horizon},
        "median_days_to_pass": float(np.median(cal)) if passed else float("nan"),
        "p25_days_to_pass": float(np.percentile(cal, 25)) if passed else float("nan"),
        "p75_days_to_pass": float(np.percentile(cal, 75)) if passed else float("nan"),
        "median_end_balance": float(np.median([o["balance"] for o in out])),
    }


def pool_from_zone(path: Path, symbol: str, step: float) -> np.ndarray:
    d = json.loads(path.read_text())
    for r in d["results"]:
        if r["symbol"] == symbol and abs(r["step"] - step) < 1e-9:
            raise SystemExit("zone_robust json stores summaries, not the R sequence")
    raise SystemExit(f"{symbol} {step} not in {path}")


def parametric(win_rate: float, payoff: float, n: int = 20000,
               seed: int = 3) -> np.ndarray:
    rng = np.random.default_rng(seed)
    wins = rng.random(n) < win_rate
    return np.where(wins, payoff, -1.0)


PROFILES = {
    # measured in this study (round-number continuation, 1:1, costs charged)
    "zone_1to1": dict(win_rate=0.528, payoff=1.0, per_day=1.3,
                      note="EURUSD 0.01 grid: 52.8% at 1:1, ~1.3 trades/day"),
    # the app's shipped ORB edge, from Implementation/STRATEGY-EDGE-LAB-2026-09-13
    "orb_1to3": dict(win_rate=0.35, payoff=3.0, per_day=0.6,
                     note="ORB 60m + H1 trend: ~35% at 1:3, ~0.6 trades/day"),
    # what a prop-viable profile has to look like
    "target_1to2": dict(win_rate=0.45, payoff=2.0, per_day=3.0,
                        note="45% at 1:2, 3 trades/day"),
    "coinflip_1to1": dict(win_rate=0.50, payoff=1.0, per_day=3.0,
                          note="no edge at all — the control"),
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default=None, choices=list(PROFILES))
    ap.add_argument("--win-rate", type=float, default=None)
    ap.add_argument("--payoff", type=float, default=2.0)
    ap.add_argument("--per-day", type=float, default=3.0)
    ap.add_argument("--trials", type=int, default=4000)
    ap.add_argument("--horizon", type=int, default=LIMIT_DAYS,
                    help="calendar days to run; 365 removes the 45-day limit")
    ap.add_argument("--risks", nargs="*", type=float,
                    default=[0.0025, 0.005, 0.0075, 0.01, 0.015, 0.02, 0.03])
    args = ap.parse_args()

    profiles = {args.profile: PROFILES[args.profile]} if args.profile else PROFILES
    if args.win_rate is not None:
        profiles = {"custom": dict(win_rate=args.win_rate, payoff=args.payoff,
                                   per_day=args.per_day, note="command line")}

    report = {}
    for name, p in profiles.items():
        pool = parametric(p["win_rate"], p["payoff"])
        exp_r = float(pool.mean())
        print(f"\n{name}: {p['note']}")
        print(f"  expectancy {exp_r:+.3f}R per trade, {p['per_day']} trades/day "
              f"-> {exp_r * p['per_day'] * 21:+.2f}R per month")
        hdr = f"  {'risk/trade':>10s} {'P(pass)':>8s} {'P(DD out)':>10s} {'P(timeout)':>11s} {'days':>6s} {'median end':>11s}"
        print(hdr)
        print("  " + "-" * (len(hdr) - 2))
        rows = []
        for risk in args.risks:
            s = run(pool, risk, p["per_day"], args.trials, horizon=args.horizon)
            rows.append(s)
            print(f"  {risk * 100:>9.2f}% {s['p_pass'] * 100:>7.1f}% {s['p_dd_breach'] * 100:>9.1f}% "
                  f"{s['p_timeout'] * 100:>10.1f}% {s['median_days_to_pass']:>6.0f} "
                  f"{(s['median_end_balance'] - 1) * 100:>+10.1f}%")
        if args.horizon > LIMIT_DAYS:
            marks = sorted(rows[0]["pass_by_day"])
            print("")
            print("  cumulative PASS rate by calendar day (no 45-day limit)")
            print("  " + f"{'risk':>7s} " + " ".join(f"{('d' + str(m)):>8s}" for m in marks)
                  + f" {'breached':>9s} {'still in':>9s}")
            for s_ in rows:
                cells = " ".join(f"{s_['pass_by_day'][m] * 100:>7.1f}%" for m in marks)
                print(f"  {s_['risk_pct'] * 100:>6.2f}% {cells} "
                      f"{s_['p_dd_breach'] * 100:>8.1f}% {s_['p_timeout'] * 100:>8.1f}%")
        report[name] = {"profile": p, "expectancy_r": exp_r, "rows": rows}

    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / "prop_challenge_sim.json"
    p.write_text(json.dumps(report, indent=1))
    print(f"\n-> {p}")


if __name__ == "__main__":
    main()
