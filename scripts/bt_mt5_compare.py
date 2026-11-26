"""
scripts/bt_mt5_compare.py - the futures shortlist, run again on the broker's own MT5 prices.

For each (market, strategy) it runs the Backtester page's engine on MT5 history (Deriv costs,
via scripts/run_app_backtest.py), converts the trades to R (STATIC sizing: 1 R = 1% of the
start balance), applies the order-flow filter of_optimise.py chose on futures (features from
the Databento flow at each MT5 entry time), and sets the result next to the futures run.

    cd /opt/algoedge/demo/app && set -a && . ../instance.env && set +a && \
    PYTHONPATH=. venv/bin/python /opt/algoedge/research_data/bt_mt5_compare.py \
        --data /opt/algoedge/research_data --out /opt/algoedge/research_data/compare.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import traceback
from argparse import Namespace
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
ROOT = Path.cwd()
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

# (market, strategy, MT5 symbol)
SHORTLIST = [("gold", "VWAP_v1", "XAUUSD"), ("nasdaq", "VWAP_v1", "US Tech 100"),
             ("nasdaq", "RQ_SigmaBreakout_v1", "US Tech 100"), ("gold", "RQ_NQPrecision_v1", "XAUUSD"),
             ("nasdaq", "RQ_M30ORB_v1", "US Tech 100"), ("gold", "RQ_CrazyORB_v1", "XAUUSD")]
BALANCE, RISK = 10000.0, 1.0


def epoch(v) -> int | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return int(v)
    t = pd.Timestamp(v)
    return int((t.tz_convert(None) if t.tzinfo else t).value // 1_000_000_000)


async def mt5_trades(strategy: str, symbol: str, start: str, end: str, dump: Path) -> list[dict]:
    import run_app_backtest as rab
    args = Namespace(terminal=None, strategy=strategy, symbol=symbol, start=start, end=end, balance=BALANCE,
                     risk=RISK, tp1_rr=3.0, min_rr=3.0, daily_dd=100.0, weekly_dd=100.0, max_positions=10,
                     max_per_symbol=1, max_daily_trades=20, sizing_basis="STATIC", pyramiding=False,
                     fill_model="CONSERVATIVE", strategy_params="{}", dump=str(dump), spread_pips=None,
                     commission=None, slippage=None, max_margin_pct=None)
    summary = await rab.run(args)
    raw = json.loads(dump.read_text())
    risk_usd = BALANCE * RISK / 100.0
    out = []
    for t in raw:
        e, x = float(t.get("entry_price") or 0), float(t.get("exit_price") or 0)
        stop = float(t.get("initial_stop_loss") or t.get("original_sl") or t.get("stop_loss") or 0)
        side = 1 if str(t.get("direction")).upper() in ("BUY", "LONG") else -1
        risk = abs(e - stop)
        if risk <= 0:
            continue
        r = float(t.get("pnl") or 0) / risk_usd
        out.append({"entry_time": epoch(t.get("entry_time")), "exit_time": epoch(t.get("exit_time")), "side": side,
                    "entry": e, "exit": x, "stop": stop, "risk_pts": risk, "exit_reason": t.get("exit_reason"),
                    "r_gross": (x - e) * side / risk, "r": r, "mfe_r": t.get("mfe_r")})
    return out, summary


def matched(fut: list[dict], mt: list[dict], window_s: int = 600) -> int:
    """MT5 trades with a futures trade on the same side within window_s of entry."""
    ft = sorted((epoch(t["entry_time"]), t["side"]) for t in fut)
    times = np.array([a for a, _ in ft]) if ft else np.array([])
    n = 0
    for t in mt:
        if not len(times):
            break
        i = np.searchsorted(times, t["entry_time"])
        for j in (i - 1, i, i + 1):
            if 0 <= j < len(ft) and abs(ft[j][0] - t["entry_time"]) <= window_s and ft[j][1] == t["side"]:
                n += 1
                break
    return n


def main() -> int:
    import of_optimise as oo

    import backend  # noqa: F401  (installs the MT5 bridge as MetaTrader5 on Linux)
    import MetaTrader5 as mt5
    mt5.initialize()
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="/opt/algoedge/research_data")
    ap.add_argument("--out", default="/opt/algoedge/research_data/compare.json")
    ap.add_argument("--start", default="2026-01-01")
    ap.add_argument("--end", default="2026-11-26")
    ap.add_argument("--only", default="", help="comma list of market:strategy")
    a = ap.parse_args()
    data = Path(a.data)
    report = {(r["market"], r["strategy"]): r for r in json.loads((data / "report.json").read_text())}
    flows: dict[str, pd.DataFrame] = {}
    rows = []
    only = {x for x in a.only.split(",") if x}
    for market, strat, symbol in SHORTLIST:
        if only and f"{market}:{strat}" not in only:
            continue
        t0 = time.time()
        try:
            fut = json.loads((data / "bt" / f"{market}__{strat}.json").read_text())["trades"]
            choice = (report.get((market, strat)) or {}).get("choice") or {"tp": None, "filter": None}
            if market not in flows:
                flows[market] = oo.flow_frame(data, market)
            mt, summ = asyncio.run(mt5_trades(strat, symbol, a.start, a.end, data / "bt" / f"mt5__{market}__{strat}.json"))
            row = {"market": market, "strategy": strat, "symbol": symbol, "choice": choice,
                   "mt5_first": min((t["entry_time"] for t in mt), default=None),
                   "mt5_signals": summ.get("signals") if isinstance(summ, dict) else None,
                   "matched_10min": matched(fut, mt)}
            for name, tr in (("futures", fut), ("mt5", mt)):
                df = oo.featurise(tr, flows[market])
                if df.empty:
                    row[name] = {"trades": 0}
                    continue
                df = df.sort_values("t").reset_index(drop=True)
                te = df[df["t"] >= oo.SPLIT]
                row[name] = {"base": oo.stats(df["r"].values, df["t"]),
                             "base_test": oo.stats(te["r"].values, te["t"]),
                             "flow_test": oo.stats(*oo.apply(te, choice)),
                             "flow_full": oo.stats(*oo.apply(df, choice))}
            rows.append(row)
            f, m = row["futures"].get("base", {}), row["mt5"].get("base", {})
            print(f"{market:7s} {strat:22s} fut {f.get('trades', 0):4d}tr {f.get('net_r', 0):7.1f}R pf {f.get('pf')} | "
                  f"mt5 {m.get('trades', 0):4d}tr {m.get('net_r', 0):7.1f}R pf {m.get('pf')} | "
                  f"matched {row['matched_10min']}  {time.time() - t0:.0f}s", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"{market:7s} {strat:22s} ERROR {type(e).__name__}: {e}", flush=True)
            traceback.print_exc(limit=4)
            rows.append({"market": market, "strategy": strat, "error": f"{type(e).__name__}: {e}"})
        Path(a.out).write_text(json.dumps(rows, default=str))
    print("COMPARE_DONE", len(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
