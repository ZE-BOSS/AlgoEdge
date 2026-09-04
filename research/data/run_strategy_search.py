"""Search every template x geometry per symbol, 1 Jan 2026 -> now, full metrics.

Prints the complete grid for the record, then the best configuration per symbol
by total P&L. Every row also carries the INDEPENDENT (non-overlapping) t-stat, so
a configuration that only looks good because its trades overlap is visible as
such rather than hidden.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from synth_strategy_lab import MT5_NAME, backtest                # noqa: E402

SYMBOLS = ["BOOM1000", "BOOM500", "CRASH1000", "CRASH500",
           "R_75", "R_25", "R_100", "stpRNG", "JD25", "JD100", "RB100", "RB200"]

GRID = []
for stop in (1.0, 2.5, 5.0):
    for rr in (1.5, 3.0, 5.0, 8.0):
        GRID.append(("drift", {"stop_atr": stop, "tp_rr": rr}))
        GRID.append(("revert", {"stop_atr": stop, "tp_rr": rr, "k": 2.0}))
        GRID.append(("breakout", {"stop_atr": stop, "tp_rr": rr}))
        GRID.append(("jump_fade", {"stop_atr": stop, "tp_rr": rr, "k": 3.0}))
        GRID.append(("jump_follow", {"stop_atr": stop, "tp_rr": rr, "k": 3.0}))

HDR = (f"{'template':13s}{'stop':>6s}{'tp':>6s}{'n':>6s}{'win%':>7s}"
       f"{'exp R':>8s}{'naive':>8s}{'PnL $':>10s}{'ret%':>8s}{'PF':>7s}"
       f"{'maxDD%':>8s}{'Shrp':>7s}{'cLoss':>6s}{'t_ind':>7s}{'  blown'}")


def fmt(t, kw, m):
    if m.get("n", 0) < 5:
        return None
    return (f"{t:13s}{kw['stop_atr']:6.1f}{kw['tp_rr']:6.1f}{m['n']:6d}"
            f"{m['win_rate']*100:7.1f}{m['expectancy_r']:+8.3f}{m['naive_r']:+8.3f}"
            f"{m['pnl']:10,.0f}{m['return_pct']:+8.1f}{m['profit_factor']:7.2f}"
            f"{m['max_dd_pct']:8.1f}{m['sharpe']:7.2f}{m['max_consec_losses']:6d}"
            f"{m['t_ind']:+7.2f}" + ('   BLOWN' if m.get('blown') else ''))


def main() -> None:
    want = sys.argv[1:] or SYMBOLS
    best_all = {}
    for code in want:
        print("\n" + "=" * 118)
        print(f"{code}  ({MT5_NAME[code]})   risk 1% STATIC, max_concurrent=1 "
              f"(no pyramiding), max 6 trades/day")
        print("=" * 118)
        print(HDR)
        rows = []
        for tmpl, kw in GRID:
            try:
                m = backtest(code, tmpl, max_concurrent=1, risk_pct=1.0,
                             sizing="STATIC", **kw)
            except Exception as e:                      # noqa: BLE001
                print(f"  {tmpl} {kw}: ERROR {type(e).__name__}: {e}")
                continue
            line = fmt(tmpl, kw, m)
            if line:
                print(line, flush=True)
                rows.append((m["pnl"], tmpl, kw, m))
        if rows:
            rows.sort(reverse=True, key=lambda x: x[0])
            best_all[code] = rows[0]
            _, tmpl, kw, m = rows[0]
            print(f"  -> BEST: {tmpl} stop {kw['stop_atr']}xATR tp {kw['tp_rr']}R  "
                  f"PnL ${m['pnl']:,.0f} ({m['return_pct']:+.1f}%)  "
                  f"WR {m['win_rate']:.1%}  PF {m['profit_factor']:.2f}  "
                  f"maxDD {m['max_dd_pct']:.1f}%  t_ind {m['t_ind']:+.2f}")

    print("\n" + "=" * 118)
    print("BEST PER SYMBOL")
    print("=" * 118)
    print(f"{'symbol':12s}{'template':13s}{'stop':>6s}{'tp':>6s}{'n':>6s}"
          f"{'win%':>7s}{'PnL $':>10s}{'ret%':>8s}{'PF':>7s}{'maxDD%':>8s}"
          f"{'Shrp':>7s}{'t_ind':>7s}")
    for code, (_, tmpl, kw, m) in sorted(best_all.items(),
                                         key=lambda x: -x[1][0]):
        print(f"{code:12s}{tmpl:13s}{kw['stop_atr']:6.1f}{kw['tp_rr']:6.1f}"
              f"{m['n']:6d}{m['win_rate']*100:7.1f}{m['pnl']:10,.0f}"
              f"{m['return_pct']:+8.1f}{m['profit_factor']:7.2f}"
              f"{m['max_dd_pct']:8.1f}{m['sharpe']:7.2f}{m['t_ind']:+7.2f}")


if __name__ == "__main__":
    main()
