"""Every AlgoEdge strategy on every Deriv instrument AlgoEdge knows, through the
same code the Backtester page uses (scripts/run_app_backtest.py).

    cd /opt/algoedge/<inst>/app && <env> python <research>/scripts/sweep_all.py \
        --start 2025-11-20 --end 2026-11-20 --mid 2026-05-20 --shard 0/3 --out /opt/algoedge/research_out/sweep

Read-only: price history only, no orders. One JSON file per strategy x
instrument (resumable: a finished pair is skipped). Each result has the full
year and both halves, so a pair that only worked in one half shows it.
Targets: the measured per-symbol default (SLOT_TP1_RR), else the strategy's
default, else 3R - what the Backtester page would use.
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import statistics as st
import sys
import time
import traceback
from argparse import Namespace
from datetime import datetime, timezone
from pathlib import Path

APP = Path(os.getcwd())
sys.path.insert(0, str(APP))

SKIP_DEFAULT = {"KRONOS_v1"}          # ML model inference over a year x 76 symbols: not a sweep job


def load_runner():
    spec = importlib.util.spec_from_file_location("run_app_backtest", APP / "scripts" / "run_app_backtest.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def instruments() -> list[tuple[str, str, str]]:
    """(canonical, broker symbol, asset class) for every instrument this Deriv account lists."""
    import backend  # noqa: F401 - MT5 bridge on Linux
    import MetaTrader5 as mt5

    from backend.core.instruments import broker_id_from_account, discover_broker_symbols, get_instrument
    if not mt5.initialize():
        raise SystemExit(f"MT5 initialize failed: {mt5.last_error()}")
    ai = mt5.account_info()
    bid = broker_id_from_account(getattr(ai, "company", None), getattr(ai, "server", None))
    res = discover_broker_symbols(bid, mt5.symbols_get())
    out = []
    for canonical, r in sorted(res.items()):
        if r.available and r.broker_symbol:
            inst = get_instrument(canonical)
            out.append((canonical, r.broker_symbol, getattr(inst, "asset_class", "?")))
    return out


def target_for(strategy: str, symbol: str) -> float:
    from backend.strategies.strategy_defaults import SLOT_TP1_RR, get_strategy_defaults
    v = SLOT_TP1_RR.get(f"{symbol.upper()}|{strategy}")
    if v is None:
        v = get_strategy_defaults(strategy).get("tp1_rr")
    return float(v or 3.0)


def trade_stats(trades: list[dict], symbol: str, balance: float) -> dict:
    from backend.risk.position_sizer import calculate_risk_dollars
    rows = []
    for t in trades:
        stop = t.get("initial_stop_loss") or t.get("original_sl") or t.get("stop_loss")
        try:
            risk = abs(calculate_risk_dollars(float(t.get("volume") or 0), float(t.get("entry_price") or 0),
                                              float(stop or 0), t.get("symbol") or symbol))
        except Exception:
            risk = 0.0
        pnl = float(t.get("pnl") or 0.0)
        rows.append({"t": float(t.get("entry_time") or 0), "x": float(t.get("exit_time") or t.get("entry_time") or 0),
                     "pnl": pnl, "r": pnl / risk if risk > 0 else None, "reason": str(t.get("exit_reason"))})
    return rows


def summarise(rows: list[dict], balance: float) -> dict:
    if not rows:
        return {"trades": 0}
    pnl = [x["pnl"] for x in rows]
    rs = [x["r"] for x in rows if x["r"] is not None]
    bal = peak = balance
    mdd = 0.0
    for x in sorted(rows, key=lambda x: x["x"]):
        bal += x["pnl"]
        peak = max(peak, bal)
        mdd = max(mdd, (peak - bal) / peak if peak > 0 else 0)
    gw, gl = sum(p for p in pnl if p > 0), -sum(p for p in pnl if p < 0)
    span_d = max(1.0, (max(x["t"] for x in rows) - min(x["t"] for x in rows)) / 86400)
    reasons: dict[str, int] = {}
    for x in rows:
        reasons[x["reason"]] = reasons.get(x["reason"], 0) + 1
    return {"trades": len(rows), "per_month": round(len(rows) / span_d * 30.4, 1),
            "win_rate": round(sum(p > 0 for p in pnl) / len(pnl), 3),
            "net": round(sum(pnl), 2), "pf": round(gw / gl, 3) if gl > 0 else None,
            "avg_r": round(st.mean(rs), 3) if rs else None, "max_dd_pct": round(100 * mdd, 2),
            "return_over_dd": round(sum(pnl) / balance * 100 / (100 * mdd), 2) if mdd > 0 else None,
            "exits": reasons}


async def one(m, strategy: str, symbol: str, a) -> dict:
    tp1 = target_for(strategy, symbol)
    dump = Path(a.out) / ".tmp" / f"{strategy}__{symbol.replace(' ', '_')}.json"
    dump.parent.mkdir(parents=True, exist_ok=True)
    ns = Namespace(terminal=None, strategy=strategy, symbol=symbol, start=a.start, end=a.end, balance=a.balance,
                   risk=a.risk, tp1_rr=tp1, min_rr=min(tp1, 3.0), daily_dd=10.0, weekly_dd=30.0, max_positions=10,
                   max_per_symbol=1, max_daily_trades=10, sizing_basis="STATIC", pyramiding=False,
                   fill_model="CONSERVATIVE", strategy_params="{}", dump=str(dump), spread_pips=None,
                   commission=None, slippage=None, max_margin_pct=None)
    summary = await asyncio.wait_for(m.run(ns), timeout=a.timeout)
    trades = json.loads(dump.read_text()) if dump.exists() else []
    dump.unlink(missing_ok=True)
    rows = trade_stats(trades, symbol, a.balance)
    mid = datetime.fromisoformat(a.mid).replace(tzinfo=timezone.utc).timestamp()
    return {"strategy": strategy, "symbol": symbol, "tp1_rr": tp1, "signals": summary.get("signals"),
            "year": summarise(rows, a.balance),
            "h1": summarise([x for x in rows if x["t"] < mid], a.balance),
            "h2": summarise([x for x in rows if x["t"] >= mid], a.balance),
            "app_summary": {k: summary.get(k) for k in ("net_pnl", "win_rate", "profit_factor", "expectancy_r",
                                                         "max_dd_pct", "trades")}}


async def main_async(a) -> None:
    m = load_runner()
    from backend.strategies.registry import list_strategies
    strategies = [s for s in list_strategies() if s not in set(a.skip.split(",")) | SKIP_DEFAULT]
    if a.strategies:
        strategies = [s for s in strategies if s in a.strategies.split(",")]
    insts = instruments()
    if a.symbols:
        keep = set(a.symbols.split(","))
        insts = [x for x in insts if x[1] in keep or x[0] in keep]
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "instruments.json").write_text(json.dumps(insts, indent=1))
    k, n = (int(x) for x in a.shard.split("/"))
    # shard by INSTRUMENT: a worker runs every strategy on its instruments, so the
    # bars it fetched for one strategy are reused (in-process cache) by the next
    mine = [i for idx, i in enumerate(insts) if idx % n == k]
    jobs = [(s, i) for i in mine for s in strategies]
    print(f"SWEEP shard {a.shard}: {len(jobs)} jobs ({len(strategies)} strategies x {len(insts)} instruments total)",
          flush=True)
    for idx, (s, (canonical, sym, cls)) in enumerate(jobs):
        f = out / f"{s}__{sym.replace(' ', '_').replace('/', '_')}.json"
        if f.exists():
            continue
        t0 = time.time()
        try:
            r = await one(m, s, sym, a)
            r.update({"canonical": canonical, "asset_class": cls, "seconds": round(time.time() - t0, 1)})
        except asyncio.TimeoutError:
            r = {"strategy": s, "symbol": sym, "canonical": canonical, "asset_class": cls, "error": "timeout"}
        except Exception as e:  # noqa: BLE001 - one broken pair must not stop the sweep
            r = {"strategy": s, "symbol": sym, "canonical": canonical, "asset_class": cls,
                 "error": f"{type(e).__name__}: {e}"[:300], "trace": traceback.format_exc()[-600:]}
        f.write_text(json.dumps(r, default=str))
        y = r.get("year", {})
        print(f"[{idx + 1}/{len(jobs)}] {s} {sym}: {r.get('error') or (str(y.get('trades')) + ' trades net ' + str(y.get('net')))}"
              f" ({round(time.time() - t0)}s)", flush=True)
    print("SWEEP_DONE", a.shard, flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2025-11-20")
    ap.add_argument("--end", default="2026-11-20")
    ap.add_argument("--mid", default="2026-05-20")
    ap.add_argument("--balance", type=float, default=10000.0)
    ap.add_argument("--risk", type=float, default=1.0)
    ap.add_argument("--shard", default="0/1")
    ap.add_argument("--out", default="/opt/algoedge/research_out/sweep")
    ap.add_argument("--skip", default="")
    ap.add_argument("--strategies", default="")
    ap.add_argument("--symbols", default="")
    ap.add_argument("--timeout", type=float, default=1800)
    asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    main()
