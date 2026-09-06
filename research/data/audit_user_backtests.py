"""Audit the 22 backtests in debug/backtest/ — metrics, parameters, and red flags.

The question is not "what do these say" but "would they be reproduced live".
So alongside the headline numbers this pulls the settings that decide whether a
result is reproducible at all:

  * sizing_basis      — BALANCE compounds; a good run inflates enormously
  * allow_pyramiding  — concurrency changes both P&L and drawdown
  * be_mode / trailing— measured harmful on these instruments (research/25 4.1)
  * exit_reason mix   — where the money actually comes from
  * gap exits         — the app books intrabar spikes at the stop price, which on
                        Boom/Crash understates the loss (research/24 4.1)
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

DBG = Path(__file__).resolve().parents[2] / "debug" / "backtest"

KEYS = ["sizing_basis", "risk_per_trade_pct", "allow_pyramiding",
        "max_concurrent_positions", "max_positions_per_symbol",
        "be_mode", "be_trigger_rr", "trail_method_tp1", "trail_mode",
        "tp_count", "tp1_rr", "max_daily_trades", "strategy_id",
        "start_date", "end_date", "simulate_wicks"]


def summarise(f: Path) -> dict:
    d = json.loads(f.read_text(encoding="utf-8"))
    rep = d.get("report") or {}
    ps = d.get("params_snapshot") or {}
    trades = d.get("trades") or []

    r = [t.get("pnl_r") for t in trades if t.get("pnl_r") is not None]
    r = np.asarray(r, dtype=float) if r else np.array([])
    reasons: dict[str, int] = {}
    reason_pnl: dict[str, float] = {}
    gap_fills = 0
    for t in trades:
        er = t.get("exit_reason") or "?"
        reasons[er] = reasons.get(er, 0) + 1
        reason_pnl[er] = reason_pnl.get(er, 0.0) + float(t.get("pnl") or 0.0)
        if t.get("gap_fill"):
            gap_fills += 1

    eq = d.get("equity_curve") or []
    dd = 0.0
    if eq:
        vals = [e.get("balance", e) if isinstance(e, dict) else e for e in eq]
        vals = np.asarray([v for v in vals if isinstance(v, (int, float))], dtype=float)
        if vals.size:
            peak = np.maximum.accumulate(vals)
            dd = float(((peak - vals) / np.maximum(peak, 1e-9)).max() * 100)

    return {
        "file": f.name,
        "symbol": rep.get("symbol") or f.name.split("backtest_")[1].rsplit("_", 1)[0],
        "strategy": ps.get("strategy_id") or rep.get("strategy_id") or "?",
        "init": d.get("initial_balance"), "final": d.get("final_balance"),
        "n": d.get("total_trades"), "signals": d.get("total_signals"),
        "ret_pct": ((d.get("final_balance", 0) / d.get("initial_balance", 1)) - 1) * 100,
        "wr": float((r > 0).mean() * 100) if r.size else float("nan"),
        "exp_r": float(r.mean()) if r.size else float("nan"),
        "maxdd": dd,
        "reasons": reasons, "reason_pnl": reason_pnl,
        "gap_fills": gap_fills,
        "params": {k: ps.get(k) for k in KEYS},
        "r": r,
    }


def main() -> None:
    files = sorted(DBG.glob("*.json"))
    rows = [summarise(f) for f in files]
    rows.sort(key=lambda x: -(x["ret_pct"] or 0))

    print(f"{len(rows)} backtests in debug/backtest/\n")
    print(f"{'symbol':22s}{'strategy':18s}{'n':>6s}{'WR%':>6s}{'expR':>7s}"
          f"{'final $':>12s}{'ret%':>10s}{'maxDD%':>8s}{'gapFill':>8s}")
    print("-" * 110)
    for x in rows:
        print(f"{x['symbol'][:21]:22s}{str(x['strategy'])[:17]:18s}{x['n'] or 0:6d}"
              f"{x['wr']:6.1f}{x['exp_r']:+7.3f}{x['final'] or 0:12,.0f}"
              f"{x['ret_pct']:+10.1f}{x['maxdd']:8.1f}{x['gap_fills']:8d}")

    print("\n" + "=" * 110)
    print("PARAMETERS — the settings that decide reproducibility")
    print("=" * 110)
    seen = {}
    for x in rows:
        key = tuple(str(x["params"].get(k)) for k in KEYS)
        seen.setdefault(key, []).append(x["symbol"])
    for key, syms in seen.items():
        print(f"\n  used by {len(syms)} run(s): {', '.join(s[:18] for s in syms[:6])}"
              + (" ..." if len(syms) > 6 else ""))
        for k, v in zip(KEYS, key):
            print(f"     {k:26s} {v}")

    print("\n" + "=" * 110)
    print("WHERE THE MONEY COMES FROM (exit reason mix)")
    print("=" * 110)
    for x in rows[:8]:
        tot = sum(x["reason_pnl"].values()) or 1.0
        parts = sorted(x["reason_pnl"].items(), key=lambda kv: -kv[1])
        s = "  ".join(f"{k}:{x['reasons'][k]}({v/tot*100:+.0f}%)" for k, v in parts)
        print(f"  {x['symbol'][:20]:21s} {s}")


if __name__ == "__main__":
    main()
