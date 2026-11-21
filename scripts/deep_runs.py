"""Deep runs: re-run chosen strategy x instrument pairs (optionally with
strategy params) through the Backtester page's code and KEEP every trade, so
monthly returns and portfolio combinations can be built afterwards.

    cd /opt/algoedge/<inst>/app && <env> python <research>/scripts/deep_runs.py \
        --jobs jobs.json --shard 0/3 --out /opt/algoedge/research_out/deep

jobs.json: [{"strategy": ..., "symbol": ..., "params": {...}, "tag": "..."}, ...]
One JSON per job (resumable). Read-only: price history only, no orders.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import traceback
from argparse import Namespace
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sweep_all as sw  # noqa: E402  (also puts the app on sys.path)


def monthly(rows: list[dict]) -> dict[str, float]:
    out: dict[str, float] = {}
    for x in rows:
        k = datetime.fromtimestamp(x["x"], tz=timezone.utc).strftime("%Y-%m")
        out[k] = round(out.get(k, 0.0) + x["pnl"], 2)
    return dict(sorted(out.items()))


async def one(m, job: dict, a) -> dict:
    strategy, symbol = job["strategy"], job["symbol"]
    tp1 = float(job.get("tp1_rr") or sw.target_for(strategy, symbol))
    dump = Path(a.out) / ".tmp" / f"{job['tag']}.json"
    dump.parent.mkdir(parents=True, exist_ok=True)
    ns = Namespace(terminal=None, strategy=strategy, symbol=symbol, start=a.start, end=a.end, balance=a.balance,
                   risk=a.risk, tp1_rr=tp1, min_rr=min(tp1, 3.0), daily_dd=10.0, weekly_dd=30.0, max_positions=10,
                   max_per_symbol=1, max_daily_trades=10, sizing_basis="STATIC", pyramiding=False,
                   fill_model="CONSERVATIVE", strategy_params=json.dumps(job.get("params") or {}), dump=str(dump),
                   spread_pips=None, commission=None, slippage=None, max_margin_pct=None)
    summary = await asyncio.wait_for(m.run(ns), timeout=a.timeout)
    trades = json.loads(dump.read_text()) if dump.exists() else []
    dump.unlink(missing_ok=True)
    rows = sw.trade_stats(trades, symbol, a.balance)
    mid = datetime.fromisoformat(a.mid).replace(tzinfo=timezone.utc).timestamp()
    return {**job, "tp1_rr": tp1, "rows": rows, "monthly": monthly(rows),
            "year": sw.summarise(rows, a.balance),
            "h1": sw.summarise([x for x in rows if x["t"] < mid], a.balance),
            "h2": sw.summarise([x for x in rows if x["t"] >= mid], a.balance),
            "app_summary": {k: summary.get(k) for k in ("net_pnl", "win_rate", "profit_factor", "max_dd_pct",
                                                         "trades")}}


async def main_async(a) -> None:
    m = sw.load_runner()
    jobs = json.loads(Path(a.jobs).read_text())
    k, n = (int(x) for x in a.shard.split("/"))
    # keep one symbol's jobs on one worker so its bars are fetched once
    syms = sorted({j["symbol"] for j in jobs})
    mine_syms = {s for i, s in enumerate(syms) if i % n == k}
    mine = [j for j in jobs if j["symbol"] in mine_syms]
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    print(f"DEEP shard {a.shard}: {len(mine)} of {len(jobs)} jobs", flush=True)
    for idx, job in enumerate(mine):
        f = out / f"{job['tag']}.json"
        if f.exists():
            continue
        t0 = time.time()
        try:
            r = await one(m, job, a)
        except asyncio.TimeoutError:
            r = {**job, "error": "timeout"}
        except Exception as e:  # noqa: BLE001
            r = {**job, "error": f"{type(e).__name__}: {e}"[:300], "trace": traceback.format_exc()[-600:]}
        r["seconds"] = round(time.time() - t0, 1)
        f.write_text(json.dumps(r, default=str))
        y = r.get("year", {})
        print(f"[{idx + 1}/{len(mine)}] {job['tag']}: {r.get('error') or (str(y.get('trades')) + ' trades net ' + str(y.get('net')))}"
              f" ({round(time.time() - t0)}s)", flush=True)
    print("DEEP_DONE", a.shard, flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", required=True)
    ap.add_argument("--start", default="2025-11-20")
    ap.add_argument("--end", default="2026-11-20")
    ap.add_argument("--mid", default="2026-05-20")
    ap.add_argument("--balance", type=float, default=10000.0)
    ap.add_argument("--risk", type=float, default=1.0)
    ap.add_argument("--shard", default="0/1")
    ap.add_argument("--out", default="/opt/algoedge/research_out/deep")
    ap.add_argument("--timeout", type=float, default=1800)
    asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    main()
