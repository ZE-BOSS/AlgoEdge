#!/usr/bin/env python
"""
scripts/run_vol_over_matrix.py

The Vol over Crash/Boom table, run through the app's own backtest path with
YOUR SAVED SETTINGS rather than dataclass defaults.

WHY THE SAVED SETTINGS AND NOT DEFAULTS
---------------------------------------
The Backtester route seeds every run from the saved Settings config
(`load_saved_strategy_blocks`). A headless run does not, and the two differ
sharply here -- `drift_jump_alpha.spike_threshold_pips` is 0 in the saved
config against a non-zero default, which switches the spike filter off
entirely. With defaults DriftJumpAlpha emits ZERO signals on these instruments;
with the saved block it emits over a thousand. A table built on defaults would
therefore describe a strategy nobody is running.

So this passes the saved blocks explicitly, and prints them, so the table says
what it was actually measuring.

EVERY ROW IS REPORTED FLAT AND COMPOUNDED
-----------------------------------------
Compounded is what the app shows. Flat (fixed dollar risk off the opening
balance) is what compares strategies, because a compounded headline over a few
hundred trades is dominated by its last few trades. Drawdown is reported
against PEAK equity when compounding -- against opening capital it reads in the
hundreds of percent and means nothing.

    py -3.12 scripts/run_vol_over_matrix.py
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SYMBOLS = ["Vol over Crash 400", "Vol over Crash 550", "Vol over Crash 750",
           "Vol over Boom 400", "Vol over Boom 550", "Vol over Boom 750"]

# Read from the user's saved config on 2026-10-02. Printed in the output so a
# reader always knows which configuration produced the numbers.
SAVED = {
    "DriftJumpAlpha_v1": {
        "spike_lookback_bars": 50, "drift_ema_fast": 20, "drift_ema_slow": 50,
        "min_adx_to_trade": 20, "jump_entry_percentile_threshold": 95,
        "trade_jumps_enabled": True, "aggregate_max_lots_per_symbol": 10,
        "spike_threshold_pips": 0, "recovery_target_pips": 0,
        "max_trades_per_day": 20, "max_daily_risk_pct": 20,
        "max_consecutive_losses": 20, "cooldown_after_max_losses_hours": 0,
        "min_rrr_to_accept_trade": 1.5,
    },
    "BoomDriftJump_v1": {
        "drift_ema_fast": 20, "drift_ema_slow": 50, "min_adx_to_trade": 20,
        "jump_entry_percentile_threshold": 95, "trade_jumps_enabled": False,
        "min_rrr_to_accept_trade": 1.5, "max_trades_per_day": 20,
        "max_daily_risk_pct": 20, "adx_gate_mode": "REDUCED_SIZE",
        "adx_gate_min_size_modifier": 0.1, "tp1_rr": 5,
    },
    "TrendDrift_v1": {
        "stop_atr_multiple": 5, "tp1_rr": 5, "spike_k_atr": 3, "revert_k_atr": 2,
        "breakout_lookback": 20, "ema_fast": 20, "ema_slow": 50,
        "require_adx": True, "min_adx_to_trade": 20, "max_trades_per_day": 6,
        "max_daily_risk_pct": 4,
    },
}


def one(symbol: str, strategy: str, *, balance: float, risk: float,
        basis: str, start: str, end: str) -> dict | None:
    cmd = [sys.executable, str(ROOT / "scripts" / "run_app_backtest.py"),
           "--strategy", strategy, "--symbol", symbol,
           "--start", start, "--end", end,
           "--balance", str(balance), "--risk", str(risk),
           "--min-rr", "1.0", "--daily-dd", "20", "--weekly-dd", "40",
           "--max-positions", "15", "--max-per-symbol", "15",
           "--max-daily-trades", "20", "--sizing-basis", basis,
           "--pyramiding", "--strategy-params", json.dumps(SAVED[strategy])]
    env = {**__import__("os").environ,
           "PYTHONPATH": f"{ROOT / 'venv' / 'Lib' / 'site-packages'};{ROOT}"}
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, env=env,
                             cwd=str(ROOT), timeout=3600)
        return json.loads(out.stdout)
    except Exception as exc:
        print(f"    {symbol} / {strategy}: FAILED {exc}", file=sys.stderr)
        return None


def table(rows: list[dict], basis: str) -> str:
    head = ("| Symbol | Strategy | Trades | WR | P&L $ | Return % | "
            "Max DD % (peak) | PF | Exp R | Ret/DD |")
    out = [head, "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for r in sorted(rows, key=lambda x: -(x.get(basis, {}).get("net_pnl") or -1e18)):
        m = r.get(basis) or {}
        if not m.get("trades"):
            out.append(f"| {r['symbol']} | {r['strategy']} | 0 | — | — | — | — | — | — | — |")
            continue
        pnl = m.get("net_pnl") or 0.0
        dd = (m.get("max_dd_pct") or 0.0)
        ret = pnl / m["balance"] * 100
        out.append(
            f"| {r['symbol']} | {r['strategy']} | {m['trades']} | "
            f"{(m.get('win_rate') or 0) * 100:.0f}% | ${pnl:,.0f} | {ret:+.1f}% | "
            f"{dd:.1f}% | {(m.get('profit_factor') or 0):.2f} | "
            f"{(m.get('expectancy_r') or 0):+.3f} | "
            f"{(ret / dd if dd else float('nan')):.2f} |")
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--balance", type=float, default=10_000.0)
    ap.add_argument("--risk", type=float, default=1.0)
    ap.add_argument("--start", default="2026-01-01")
    ap.add_argument("--end", default="2026-09-29")
    ap.add_argument("--symbols", nargs="*", default=SYMBOLS)
    ap.add_argument("--strategies", nargs="*", default=list(SAVED))
    ap.add_argument("--out", default="data/vol_over_matrix")
    args = ap.parse_args()

    print(f"\n${args.balance:,.0f} at {args.risk}% risk, {args.start} -> {args.end}, "
          f"SAVED settings (not defaults)\n")
    rows = []
    for sym in args.symbols:
        for strat in args.strategies:
            line = f"  {sym:<20} {strat:<20}"
            got = {"symbol": sym, "strategy": strat}
            for basis, key in (("BALANCE", "compounded"), ("STATIC", "flat")):
                res = one(sym, strat, balance=args.balance, risk=args.risk,
                          basis=basis, start=args.start, end=args.end)
                if res:
                    res["balance"] = args.balance
                    got[key] = res
            c = got.get("compounded") or {}
            print(f"{line} {c.get('trades', 0):>5} trades | "
                  f"${(c.get('net_pnl') or 0):>12,.0f} | DD {(c.get('max_dd_pct') or 0):>5.1f}%",
                  flush=True)
            rows.append(got)

    out = ROOT / args.out
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps(rows, indent=1, default=str))
    (out / "RESULTS.md").write_text(
        f"# Vol over Crash/Boom — {args.start} to {args.end}\n\n"
        f"${args.balance:,.0f} opening balance, {args.risk}% risk per trade, run through the "
        f"app's own backtest path with the **saved** strategy settings.\n\n"
        f"```json\n{json.dumps(SAVED, indent=1)}\n```\n\n"
        f"## Flat risk — compare strategies on this\n\n{table(rows, 'flat')}\n\n"
        f"## Compounded — what the app shows\n\n{table(rows, 'compounded')}\n",
        encoding="utf-8")
    print(f"\n-> {out / 'RESULTS.md'}")


if __name__ == "__main__":
    main()
