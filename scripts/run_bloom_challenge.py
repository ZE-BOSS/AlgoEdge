#!/usr/bin/env python
"""
scripts/run_bloom_challenge.py

The Crash/Boom drift book against a prop firm that actually allows those
instruments.

WHY THIS AND NOT ANOTHER STRATEGY SEARCH
----------------------------------------
`run_required_edge.py` settles what a one-month pass needs. Reaching +10% in 20
trading days forces high volatility, and high volatility against a static
drawdown floor breaches often no matter how good the edge is. Measured:

    no edge at all, 50% vol   40.6% pass / 38.1% breach
    Sharpe 2.0, best sizing   69.6% pass / 27.8% breach
    Sharpe 5.0, best sizing   85.9% pass / 13.2% breach

So the only way to pass a one-month challenge *reliably* is a Sharpe most
strategies never reach. Of everything measured this session, exactly one book
has it: the Crash/Boom drift, at **Sharpe 5.62 on 86% annualised volatility**.
FundedNext does not offer those instruments — but BloomFunded and
RealTraderFund fund precisely them.

BLOOMFUNDED RULES (published, 2026-09)
    phase 1 target        8%          phase 2  5%
    daily drawdown        4%
    overall drawdown      6%  STATIC   <- tighter than FundedNext's 10%
    minimum trading days  4
    time limit            none
    instruments           Boom / Crash / Jump / Step / Volatility only
    EAs                   allowed

The 6% floor is the binding constraint: this book runs a 10.8% drawdown at 0.5%
risk, so it has to be sized down, and the whole question is whether it still
reaches 8% before it loses 6%.

    python scripts/run_bloom_challenge.py
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import zone_money_engine as eng  # noqa: E402
from scripts.run_synth_spike_check import BOOK, trades  # noqa: E402

OUT = ROOT / "data" / "zone_study"

TARGET_P1, TARGET_P2 = 0.08, 0.05
DAILY_DD, MAX_DD = 0.04, 0.06
MIN_DAYS = 4


def walk(seq: list[dict], balance: float, risk_pct: float, target: float) -> dict:
    """The real trade sequence against the real rules. No time limit."""
    bal = balance
    floor = balance * (1 - MAX_DD)
    day_start: dict[str, float] = {}
    blocked: str | None = None
    days_traded: set[str] = set()
    start_day = None
    peak = balance
    max_dd = 0.0

    for tr in seq:
        d = datetime.fromtimestamp(tr["t"], timezone.utc).date()
        if start_day is None:
            start_day = d
        key = d.isoformat()
        if key not in day_start:
            day_start[key] = bal
            blocked = None
        if blocked == key:
            continue

        want = bal * risk_pct / 100.0
        lots, risked = eng.size_lots(tr["symbol"], want, tr["stop_distance"])
        if lots <= 0 or risked > want * 3:
            continue
        bal += tr["r"] * risked
        days_traded.add(key)
        peak = max(peak, bal)
        max_dd = max(max_dd, (peak - bal) / peak)

        if bal <= floor:
            return {"result": "breach", "balance": bal, "days": len(days_traded),
                    "elapsed": (d - start_day).days, "max_dd_pct": max_dd * 100}
        if bal <= day_start[key] - balance * DAILY_DD:
            blocked = key
        if bal - balance >= balance * target and len(days_traded) >= MIN_DAYS:
            return {"result": "pass", "balance": bal, "days": len(days_traded),
                    "elapsed": (d - start_day).days, "max_dd_pct": max_dd * 100}

    return {"result": "no_target", "balance": bal, "days": len(days_traded),
            "elapsed": (d - start_day).days if start_day else 0,
            "max_dd_pct": max_dd * 100}


def bootstrap(seq: list[dict], balance: float, risk_pct: float, target: float,
              trials: int = 2000, seed: int = 5) -> dict:
    """Same trades, order reshuffled — the single historical path is one draw."""
    rng = np.random.default_rng(seed)
    res = []
    n = len(seq)
    for _ in range(trials):
        idx = rng.permutation(n)
        shuffled = [{**seq[i], "t": seq[k]["t"]} for k, i in enumerate(idx)]
        res.append(walk(shuffled, balance, risk_pct, target))
    passed = [r for r in res if r["result"] == "pass"]
    return {"p_pass": len(passed) / trials,
            "p_breach": sum(1 for r in res if r["result"] == "breach") / trials,
            "median_days": float(np.median([r["elapsed"] for r in passed])) if passed else float("nan"),
            "p90_days": float(np.percentile([r["elapsed"] for r in passed], 90)) if passed else float("nan")}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-01-01")
    ap.add_argument("--balance", type=float, default=10_000.0)
    ap.add_argument("--trials", type=int, default=2000)
    args = ap.parse_args()

    eng.SPECS = json.loads((ROOT / "data" / "zone_study" / "specs.json").read_text())

    seq: list[dict] = []
    for sym, step in BOOK:
        seq += trades(sym, step, args.since, spike_fill=True)
    seq.sort(key=lambda x: x["t"])
    print(f"Crash/Boom drift book: {len(seq):,} trades, {args.since} -> today, "
          f"measured spike fills")

    print(f"\nBLOOMFUNDED PHASE 1 — target {TARGET_P1:.0%}, daily {DAILY_DD:.0%}, "
          f"static DD {MAX_DD:.0%}, min {MIN_DAYS} days, no time limit")
    print(f"  {'risk':>6s} {'HISTORICAL':>12s} {'end bal':>11s} {'days':>6s} {'maxDD':>7s} "
          f"| {'P(pass)':>8s} {'P(breach)':>10s} {'median d':>9s} {'p90 d':>7s}")
    out = {}
    for risk in (0.10, 0.15, 0.20, 0.25, 0.35, 0.50):
        hist = walk(seq, args.balance, risk, TARGET_P1)
        bs = bootstrap(seq, args.balance, risk, TARGET_P1, args.trials)
        print(f"  {risk:>5.2f}% {hist['result']:>12s} {hist['balance']:>11,.0f} "
              f"{hist['elapsed']:>6d} {hist['max_dd_pct']:>6.1f}% | "
              f"{bs['p_pass'] * 100:>7.1f}% {bs['p_breach'] * 100:>9.1f}% "
              f"{bs['median_days']:>9.0f} {bs['p90_days']:>7.0f}")
        out[risk] = {"historical": hist, "bootstrap": bs}

    best = max(out.items(), key=lambda kv: kv[1]["bootstrap"]["p_pass"]
               - kv[1]["bootstrap"]["p_breach"])
    r, v = best
    print(f"\n  best risk/reward: {r:.2f}% -> {v['bootstrap']['p_pass'] * 100:.1f}% pass, "
          f"{v['bootstrap']['p_breach'] * 100:.1f}% breach, "
          f"median {v['bootstrap']['median_days']:.0f} days "
          f"(90th percentile {v['bootstrap']['p90_days']:.0f})")

    print(f"\nPHASE 2 at that risk — target {TARGET_P2:.0%}, same barriers")
    bs2 = bootstrap(seq, args.balance, r, TARGET_P2, args.trials)
    print(f"  {bs2['p_pass'] * 100:.1f}% pass, {bs2['p_breach'] * 100:.1f}% breach, "
          f"median {bs2['median_days']:.0f} days")
    both = v["bootstrap"]["p_pass"] * bs2["p_pass"]
    print(f"\n  BOTH PHASES: {both * 100:.1f}% "
          f"(median {v['bootstrap']['median_days'] + bs2['median_days']:.0f} days)")

    out["phase2"] = bs2
    (OUT / "bloom_challenge.json").write_text(json.dumps(out, indent=1, default=float))
    print(f"\n-> {OUT / 'bloom_challenge.json'}")


if __name__ == "__main__":
    main()
