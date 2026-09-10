#!/usr/bin/env python
"""
scripts/measure_opposite_sides.py — [P2]

"Can we catch sells on Crash and buys on Boom, and is either side worth trading
at $350?"

Runs the six relevant slot configurations through the portfolio backtester and
prints a single comparison table. Must be run on the machine with MT5 attached —
it drives the running backend's own `/api/portfolio_backtest`, so the numbers
come out of exactly the code path the UI uses, not a parallel implementation.

    python scripts/measure_opposite_sides.py --url http://127.0.0.1:8000 \
        --token "$ALGOEDGE_TOKEN" --balance 350 \
        --start 2026-01-01 --end 2026-09-10

WHAT IT MEASURES, AND THE ONE THING TO WATCH
--------------------------------------------
Each configuration is run TWICE: once with `stop_fill_model=OFF` (the old
harness, which books every stop at exactly the stop price) and once with
`CONSERVATIVE` (charging the measured per-symbol overshoot — research/27 §1.2:
0.403 R on Crash 1000, 0.353 R on Boom 1000).

The interesting result is not either column on its own, it is the SPREAD between
them per side:

    SpikeFade  (Crash BUY / Boom SELL)  — the spike runs into the STOP. This side
                                          should lose a lot when the fill model
                                          is switched on.
    SpikeRide  (Crash SELL / Boom BUY)  — the spike runs into the TARGET, and a
                                          limit fills at its price or better. This
                                          side should barely move.

If the ranking between the two sides inverts once fills are priced correctly,
that is the finding — and it means every previous comparison of these two sides
was measuring the harness rather than the market.

HONEST CAVEAT
-------------
research/24 measured all of these instruments to be fair martingales with
memoryless jump arrival. Neither side has a demonstrated edge, and a positive
result here should be treated as a candidate for falsification, not a green
light. Read the deflated t-statistic, not the profit factor.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

try:
    import httpx
except ImportError:
    print("pip install httpx", file=sys.stderr)
    raise SystemExit(1)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

CRASH = "Crash 1000 Index"
BOOM = "Boom 1000 Index"

# Each entry: label, [(symbol, strategy_id, strategy_params), ...]
CONFIGS: list[tuple[str, list[tuple[str, str, dict]]]] = [
    ("SpikeFade  (Crash BUY + Boom SELL)   — the shipped side", [
        (CRASH, "SpikeFade_v1", {}),
        (BOOM, "SpikeFade_v1", {}),
    ]),
    ("SpikeRide  (Crash SELL + Boom BUY)   — the un-traded side", [
        (CRASH, "SpikeRide_v1", {}),
        (BOOM, "SpikeRide_v1", {}),
    ]),
    ("Both sides, one basket (4 slots)", [
        (CRASH, "SpikeFade_v1", {}), (CRASH, "SpikeRide_v1", {}),
        (BOOM, "SpikeFade_v1", {}), (BOOM, "SpikeRide_v1", {}),
    ]),
    ("DriftJumpAlpha  Setup A only (Crash BUY)", [
        (CRASH, "DriftJumpAlpha_v1", {"trade_jumps_enabled": False}),
    ]),
    ("DriftJumpAlpha  + Setup B (adds Crash SELL)", [
        (CRASH, "DriftJumpAlpha_v1",
         {"trade_jumps_enabled": True, "control_test_passed": True}),
    ]),
    ("BoomDriftJump   Setup A only (Boom SELL)", [
        (BOOM, "BoomDriftJump_v1", {"trade_jumps_enabled": False}),
    ]),
    ("BoomDriftJump   + Setup B (adds Boom BUY)", [
        (BOOM, "BoomDriftJump_v1", {"trade_jumps_enabled": True}),
    ]),
]


def build_payload(slots, args, fill_mode: str) -> dict:
    return {
        "symbols": [
            {"symbol": sym, "strategy_id": sid, "strategy_params": params}
            for sym, sid, params in slots
        ],
        "start_date": args.start,
        "end_date": args.end,
        "initial_balance": args.balance,
        # The user's own live risk settings, so the comparison is against what
        # is actually being traded rather than a tuned strawman.
        "risk_per_trade_pct": args.risk,
        "min_rr": 1.0,
        "max_daily_drawdown_pct": 20.0,
        "max_weekly_drawdown_pct": 40.0,
        "max_concurrent_positions": 15,
        "max_positions_per_symbol": 15,
        "max_daily_trades": 20,
        "max_risk_hard_cap_pct": 2.2,
        "tp_count": 1,
        "tp1_rr": 5.0,
        "tp_splits": "100",
        "be_trigger_rr": 1.0,
        "be_buffer_pips": 6.0,
        "trail_method_tp1": "ATR_TRAIL",
        "atr_trail_multiplier": 3.0,
        "trail_pips": 15.0,
        "sizing_basis": "EQUITY",
        "simulate_wicks": True,
        # [P1.2] the whole point of this comparison
        "stop_fill_model": fill_mode,
        "stop_fill_seed": "opposite-sides",
    }


def run_one(client: httpx.Client, payload: dict, poll: int) -> dict | None:
    r = client.post("/api/portfolio_backtest", json=payload)
    r.raise_for_status()
    body = r.json()
    bt_id = body.get("backtest_id") or body.get("id")
    if body.get("trades") is not None or body.get("report"):
        return body
    if not bt_id:
        print(f"    unexpected response: {json.dumps(body)[:200]}")
        return None
    for _ in range(poll):
        time.sleep(2)
        s = client.get(f"/api/backtests/{bt_id}")
        if s.status_code == 200:
            d = s.json()
            if d.get("report") or d.get("trades"):
                return d
    print("    timed out waiting for the run")
    return None


def summarise(d: dict) -> dict:
    g = d.get("grouped_trades") or d.get("trades") or []
    rep = d.get("report") or {}
    fm = d.get("fill_model") or {}
    total_r = 0.0
    for t in g:
        risk = abs(t["entry_price"] - t.get("initial_stop_loss", t["stop_loss"]))
        if risk <= 0:
            continue
        move = (t["exit_price"] - t["entry_price"]) if t["direction"] == "BUY" \
            else (t["entry_price"] - t["exit_price"])
        total_r += move / risk
    n = len(g) or 1
    return {
        "n": len(g),
        "ret_pct": (d.get("final_balance", 0) - d.get("initial_balance", 1))
        / max(d.get("initial_balance", 1), 1e-9) * 100.0,
        "total_r": total_r,
        "exp_r": total_r / n,
        "pf": rep.get("profit_factor", 0.0),
        "wr": rep.get("win_rate", 0.0) * 100.0,
        "dd": rep.get("max_drawdown_pct", 0.0) * 100.0,
        "gapped_pct": fm.get("gapped_pct", 0.0),
        "mean_ov": fm.get("mean_overshoot_r", 0.0),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    ap.add_argument("--token", default="", help="bearer token; or set ALGOEDGE_TOKEN")
    ap.add_argument("--balance", type=float, default=350.0)
    ap.add_argument("--risk", type=float, default=1.8)
    ap.add_argument("--start", default="2026-01-01")
    ap.add_argument("--end", default="2026-09-10")
    ap.add_argument("--poll", type=int, default=600)
    args = ap.parse_args()

    import os
    token = args.token or os.environ.get("ALGOEDGE_TOKEN", "")
    headers = {"Authorization": f"Bearer {token}"} if token else {}

    print(f"\nOpposite-side comparison — ${args.balance:,.0f}, "
          f"{args.risk}% risk, {args.start} .. {args.end}\n")
    hdr = (f"{'configuration':<46} {'fill':<5} {'n':>5} {'ret%':>8} {'totalR':>8} "
           f"{'R/trade':>8} {'PF':>5} {'WR%':>5} {'DD%':>6} {'gap%':>6}")
    print(hdr)
    print("-" * len(hdr))

    results: dict[str, dict] = {}
    with httpx.Client(base_url=args.url, headers=headers, timeout=120) as client:
        for label, slots in CONFIGS:
            for mode in ("OFF", "CONSERVATIVE"):
                d = run_one(client, build_payload(slots, args, mode), args.poll)
                if not d:
                    continue
                s = summarise(d)
                results[f"{label}|{mode}"] = s
                print(f"{label:<46} {mode[:4]:<5} {s['n']:>5} {s['ret_pct']:>8.2f} "
                      f"{s['total_r']:>+8.2f} {s['exp_r']:>+8.4f} {s['pf']:>5.2f} "
                      f"{s['wr']:>5.1f} {s['dd']:>6.2f} {s['gapped_pct']:>6.1f}")
            print()

    # The comparison that actually matters.
    print("\nWhat the realistic fill model costs each side")
    print(f"{'configuration':<46} {'ΔtotalR':>10} {'Δ R/trade':>11}")
    print("-" * 69)
    for label, _ in CONFIGS:
        a = results.get(f"{label}|OFF")
        b = results.get(f"{label}|CONSERVATIVE")
        if not a or not b:
            continue
        print(f"{label:<46} {b['total_r'] - a['total_r']:>+10.2f} "
              f"{b['exp_r'] - a['exp_r']:>+11.4f}")
    print("\nA side whose Δ is near zero is one the old harness was not flattering.")
    print("A side that only worked with fills OFF never worked.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
