"""Choose the configuration to SHIP per symbol, under a drawdown constraint.

Picking purely on P&L produced Crash 1000 at +176.9% with an **89.5% max
drawdown** — an account that survives that on paper would not survive it in
practice, and no risk policy would authorise it. So the shipping rule is:

    among configurations with max drawdown <= MAX_DD and n >= MIN_N,
    take the highest return

and the top three are printed per symbol so the choice can be seen as a ridge
rather than a spike. A parameter that only works at one exact setting is a fitted
parameter; one that works across neighbours is at least a plausible one.

Both sizing modes are then reported for the winner, because STATIC and BALANCE
differ enormously on strategies with long losing runs (research/20).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from synth_strategy_lab import MT5_NAME, backtest               # noqa: E402

MAX_DD = 35.0
MIN_N = 100

SYMBOLS = ["BOOM1000", "BOOM500", "CRASH1000", "CRASH500",
           "R_75", "R_25", "R_100", "stpRNG", "JD25", "JD100", "RB100", "RB200"]

GRID = []
for stop in (1.0, 2.5, 5.0):
    for rr in (1.5, 3.0, 5.0, 8.0):
        for tmpl in ("drift", "revert", "breakout", "jump_fade", "jump_follow"):
            kw = {"stop_atr": stop, "tp_rr": rr}
            if tmpl in ("jump_fade", "jump_follow"):
                kw["k"] = 3.0
            if tmpl == "revert":
                kw["k"] = 2.0
            GRID.append((tmpl, kw))


def main() -> None:
    print(f"Shipping rule: max drawdown <= {MAX_DD}%, n >= {MIN_N}, then best return.")
    print("Window 2026-01-01 -> now | risk 1% | max_concurrent=1 | 6 trades/day max\n")
    chosen = {}
    for code in SYMBOLS:
        rows = []
        for tmpl, kw in GRID:
            try:
                m = backtest(code, tmpl, max_concurrent=1, risk_pct=1.0,
                             sizing="STATIC", **kw)
            except Exception:                                   # noqa: BLE001
                continue
            if m.get("n", 0) < MIN_N or m.get("blown"):
                continue
            rows.append((m["return_pct"], tmpl, kw, m))
        ok = [r for r in rows if r[3]["max_dd_pct"] <= MAX_DD]
        if not ok:
            print(f"{code:11s} NO configuration under {MAX_DD}% drawdown")
            continue
        ok.sort(reverse=True, key=lambda x: x[0])
        chosen[code] = ok[0]
        print(f"{code}  ({MT5_NAME[code]})")
        for rank, (_, tmpl, kw, m) in enumerate(ok[:3], 1):
            mark = "  <= SHIP" if rank == 1 else ""
            print(f"   {rank}. {tmpl:12s} stop {kw['stop_atr']:>3}xATR tp {kw['tp_rr']:>3}R"
                  f"  n={m['n']:4d}  WR {m['win_rate']*100:4.1f}%  "
                  f"ret {m['return_pct']:+6.1f}%  PF {m['profit_factor']:.2f}  "
                  f"DD {m['max_dd_pct']:4.1f}%  Sharpe {m['sharpe']:5.2f}  "
                  f"t_ind {m['t_ind']:+5.2f}{mark}")

    print("\n" + "=" * 118)
    print("SHIPPING TABLE — both sizing modes")
    print("=" * 118)
    print(f"{'symbol':11s}{'strategy':16s}{'stop':>6s}{'tp':>5s}{'n':>6s}{'WR%':>6s}"
          f"{'PF':>6s}{'STATIC ret%':>13s}{'STATIC DD%':>12s}"
          f"{'BAL ret%':>11s}{'BAL DD%':>9s}{'maxLoss':>8s}")
    TEMPLATE_TO_STRATEGY = {
        "drift": "DriftJumpAlpha_v1/BoomDriftJump_v1",
        "jump_fade": "SpikeFade_v1", "revert": "RangeRevert_v1",
        "breakout": "RangeBreakout_v1", "jump_follow": "SpikeFade_v1(inv)",
    }
    for code, (_, tmpl, kw, m) in chosen.items():
        b = backtest(code, tmpl, max_concurrent=1, risk_pct=1.0,
                     sizing="BALANCE", **kw)
        name = TEMPLATE_TO_STRATEGY[tmpl].split("/")[0]
        print(f"{code:11s}{name:16s}{kw['stop_atr']:6.1f}{kw['tp_rr']:5.1f}"
              f"{m['n']:6d}{m['win_rate']*100:6.1f}{m['profit_factor']:6.2f}"
              f"{m['return_pct']:+13.1f}{m['max_dd_pct']:12.1f}"
              f"{b['return_pct']:+11.1f}{b['max_dd_pct']:9.1f}"
              f"{m['max_consec_losses']:8d}")


if __name__ == "__main__":
    main()
