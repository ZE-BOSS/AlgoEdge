"""
scripts/run_roboquant_replication.py

Backtest the five roboquant.dev templates (backend/strategies/strategy_roboquant)
through the app's own BacktestEngine, risk layer and exit hooks, on MT5 bars.

    py -3.12 scripts/run_roboquant_replication.py [--terminal "C:/Program Files/MetaTrader 5/terminal64.exe"]

For each strategy x symbol it reports trades, win rate, mean R, profit factor,
max drawdown and the year-by-year split, at the real cost model AND with
costs at zero (futures pay ~nothing for spread; a CFD does not), next to the
template's own claim. Writes research/roboquant/results/*.json.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CLAIMS = {
    "RQ_NQPrecision_v1": "+1,377% | 94.0% win | DD 32.8% | PF 2.48",
    "RQ_NYORB_v1": "+1,909% | 74.0% win | DD 42.1% | PF 1.22",
    "RQ_CrazyORB_v1": "+594% | 81.3% win | DD 19.0% | PF 1.57",
    "RQ_TMeanReversion_v1": "+708% | 63.4% win | DD 27.7% | PF 2.58",
    "RQ_CharlesMonger_v1": "+1,116% | 24.9% win | DD 55.5% | PF 1.32",
    "RQ_FlashTriggerORB_v1": "+195.1% | 61.6% win | DD 11.1% | PF 1.36",
    "RQ_ScarfaceORB_v1": "+48.4% | 41.5% win | DD 7.7% | PF 1.35",
    "RQ_IVB_v1": "+309.8% | 52.0% win | DD 20.1% | PF 1.37",
    "RQ_GigaORB_v1": "+477.1% | 55.9% win | DD 21.5% | PF 1.22",
    "RQ_SigmaBreakout_v1": "+440.9% | 55.3% win | DD 19.7% | PF 1.39",
    "RQ_DailyVWAP_v1": "+299.9% | 56.6% win | DD 22.0% | PF 1.84",
    "RQ_FridayRevisit_v1": "+573.3% | 22.6% win | DD 15.2% | PF 2.96",
    "RQ_M30ORB_v1": "+345.7% | 47.6% win | DD 33.7% | PF 1.22",
    "RQ_SRSweep_v1": "+57.0% | 52.1% win | DD 11.0% | PF 1.25",
}
# Each template's own published backtest window (research/roboquant/source).
TEMPLATE_START = {
    "RQ_NQPrecision_v1": "2023-10-02", "RQ_NYORB_v1": "2021-03-25", "RQ_CrazyORB_v1": "2025-09-12",
    "RQ_TMeanReversion_v1": "2020-01-01", "RQ_CharlesMonger_v1": "2020-01-01",
    "RQ_FlashTriggerORB_v1": "2023-09-15", "RQ_ScarfaceORB_v1": "2023-01-01", "RQ_IVB_v1": "2023-01-01",
    "RQ_GigaORB_v1": "2020-01-01", "RQ_SigmaBreakout_v1": "2020-01-01", "RQ_DailyVWAP_v1": "2023-01-01",
    "RQ_FridayRevisit_v1": "2021-09-01", "RQ_M30ORB_v1": "2023-11-07", "RQ_SRSweep_v1": "2023-01-01",
}
RUNS = [  # (strategy, symbol, timeframe) - NQ/MNQ -> NDX100, ES -> SPX500
    ("RQ_TMeanReversion_v1", "NDX100", "H1"),
    ("RQ_CharlesMonger_v1", "SPX500", "H1"),
    ("RQ_CharlesMonger_v1", "NDX100", "H1"),
    ("RQ_NYORB_v1", "NDX100", "M15"),
    ("RQ_CrazyORB_v1", "NDX100", "M15"),
    ("RQ_NQPrecision_v1", "NDX100", "M1"),
    ("RQ_FlashTriggerORB_v1", "NDX100", "M15"),
    ("RQ_ScarfaceORB_v1", "NDX100", "M1"),
    ("RQ_IVB_v1", "NDX100", "M5"),
    ("RQ_GigaORB_v1", "NDX100", "M15"),
    ("RQ_SigmaBreakout_v1", "NDX100", "M30"),
    ("RQ_DailyVWAP_v1", "SPX500", "M1"),
    ("RQ_FridayRevisit_v1", "NDX100", "M5"),
    ("RQ_M30ORB_v1", "NDX100", "M5"),
    ("RQ_SRSweep_v1", "NDX100", "M5"),
]


def summarise(rs, balance_curve=None) -> dict:
    r = np.asarray([x for x in rs if x is not None and np.isfinite(x)], float)
    if not len(r):
        return {"trades": 0}
    eq = np.cumsum(r)
    dd = float((np.maximum.accumulate(np.r_[0, eq]) - np.r_[0, eq]).max())
    wins, losses = r[r > 0].sum(), -r[r < 0].sum()
    out = {"trades": int(len(r)), "win_rate": round(float((r > 0).mean()), 4),
           "mean_r": round(float(r.mean()), 4), "total_r": round(float(r.sum()), 2),
           "profit_factor": round(float(wins / losses), 3) if losses > 0 else None,
           "max_dd_r": round(dd, 2),
           "t": round(float(r.mean() / (r.std(ddof=1) / np.sqrt(len(r)))), 2) if len(r) > 2 else None}
    # Compounded at 1% risk per trade.
    bal = np.cumprod(1 + 0.01 * r)
    peak = np.maximum.accumulate(np.r_[1.0, bal])
    out["return_pct_at_1pct"] = round(float((bal[-1] - 1) * 100), 1)
    out["max_dd_pct_at_1pct"] = round(float(((peak - np.r_[1.0, bal]) / peak).max() * 100), 1)
    return out


def run_one(sid, sym, tf, start, end, zero_cost=False):
    from backend.analytics.metrics import compute_trade_metrics
    from backend.api.routes.backtest import BacktestRequest, build_merged_risk_config
    from backend.backtester.engine import BacktestEngine
    from backend.core.config_schema import UserConfigV2
    from backend.kronos_service.research import load_bars
    from backend.rl import base_signals as bs
    from backend.rl import env as E
    from backend.strategies.registry import get_strategy

    f = load_bars(sym, tf, start, end)
    idx = f.set_index(pd.to_datetime(f["time"], unit="s"))
    if "volume" in idx.columns and "tick_volume" not in idx.columns:
        idx["tick_volume"] = idx["volume"]
    cfg = UserConfigV2()
    frames = {tf: idx}
    if "D1" in get_strategy(sid)(cfg).get_required_timeframes():
        d1 = load_bars(sym, "D1", "2019-01-01", end)       # the BROKER's daily bars, as live
        frames["D1"] = d1.set_index(pd.to_datetime(d1["time"], unit="s"))
    sigs = bs.generate(sym, tf, bs.index_frames(frames), sid, cfg, start_index=60)
    rc = build_merged_risk_config(BacktestRequest(symbol=sym, strategy_id=sid))
    rc["risk_per_trade_pct"] = 1.0
    if zero_cost:
        # 1e-9, not 0: stress_costs reads 0 as "unset" (multiplier or 1.0).
        rc = E.stress_costs(rc, sym, 1e-9)
    eng = BacktestEngine(dict(rc))
    eng.group_chart_data = False
    # $100k: big enough that the broker's minimum lot never decides which
    # trades happen (a 5% stop on NDX100 at 0.01 lot is ~$125 - over 1% of
    # $10k). Results are in R and %, so the balance does not change them.
    strat = get_strategy(sid)(cfg)
    strat.is_backtesting = True
    if hasattr(strat, "prepare_backtest"):          # the route's BarFeed.bind does this
        strat.prepare_backtest(sym, frames)
    if tf != "M1":
        # Settle a bar that reaches the stop AND the target from its minutes
        # (backtester/intrabar.py) - the route does this for small targets.
        from backend.backtester.intrabar import IntrabarResolver
        eng.intrabar = {sym: IntrabarResolver(load_bars(sym, "M1", start, end))}
    res = eng.run(idx, [dict(s) for s in sigs], 100_000.0, None, None, strat, None, None)
    rows = []
    for g in res.get("grouped_trades") or []:
        try:
            r = compute_trade_metrics(g).get("realized_rr")
        except Exception:                               # noqa: BLE001
            r = None
        et = g.get("entry_time")
        year = (str(pd.Timestamp(int(et), unit="s").year) if isinstance(et, (int, float))
                else str(pd.Timestamp(et).year) if et is not None else "?")
        rows.append((year, r, g.get("exit_reason")))
    by_year = {}
    for y, r, _ in rows:
        by_year.setdefault(y, []).append(r)
    blocked = {}
    for b in res.get("blocked_signals") or []:
        k = (b.get("reason") or b.get("gate") or "")[:80]
        blocked[k] = blocked.get(k, 0) + 1
    exits = {}
    for *_, e in rows:
        exits[str(e)] = exits.get(str(e), 0) + 1
    return {"bars": len(f), "first": str(idx.index[0]), "last": str(idx.index[-1]),
            "signals": len(sigs), "all": summarise([r for _, r, _ in rows]),
            "by_year": {y: summarise(v) for y, v in sorted(by_year.items())},
            "exit_reasons": exits, "top_blocks": dict(sorted(blocked.items(), key=lambda x: -x[1])[:5]),
            "final_balance": res.get("final_balance")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--terminal", default="C:/Program Files/MetaTrader 5/terminal64.exe")
    ap.add_argument("--start", default="")
    ap.add_argument("--template-window", action="store_true",
                    help="start each run at its template's own published-backtest start")
    ap.add_argument("--end", default="2026-10-20")
    ap.add_argument("--only", default="")
    a = ap.parse_args()
    import MetaTrader5 as mt5
    if not mt5.initialize(path=a.terminal):
        raise SystemExit(f"MT5 terminal not available: {mt5.last_error()}")
    print("terminal", mt5.account_info().server, flush=True)
    out_dir = ROOT / "research" / "roboquant" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    for sid, sym, tf in RUNS:
        if a.only and sid not in a.only.split(","):
            continue
        t0 = time.time()
        res = {"strategy": sid, "symbol": sym, "timeframe": tf, "claim": CLAIMS[sid],
               "server": mt5.account_info().server}
        try:
            start = TEMPLATE_START[sid] if (a.template_window or not a.start) else a.start
            res["start_requested"] = start
            res["real_costs"] = run_one(sid, sym, tf, start, a.end)
            res["zero_costs"] = run_one(sid, sym, tf, start, a.end, zero_cost=True)
        except Exception as exc:                        # noqa: BLE001
            res["error"] = f"{type(exc).__name__}: {exc}"
        res["seconds"] = round(time.time() - t0)
        (out_dir / f"{sid}_{sym}_{tf}.json").write_text(json.dumps(res, indent=1, default=str))
        rc = res.get("real_costs", {}).get("all", {})
        zc = res.get("zero_costs", {}).get("all", {})
        print(json.dumps({"run": f"{sid} {sym} {tf}", "error": res.get("error"),
                          "period": [res.get("real_costs", {}).get("first"), res.get("real_costs", {}).get("last")],
                          "real": rc, "zero_cost": zc, "claim": CLAIMS[sid], "s": res["seconds"]}), flush=True)


if __name__ == "__main__":
    main()
