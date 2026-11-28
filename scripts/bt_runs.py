"""
scripts/bt_runs.py - read saved backtest runs: list the recent ones, or compare a portfolio run
with the single runs of the same strategy x symbol, trade by trade. Read-only.

    cd /opt/algoedge/<inst>/app && PYTHONPATH=. ../venv/bin/python scripts/bt_runs.py list --days 2
    ... scripts/bt_runs.py show <run id>                 # params + per strategy x symbol summary
    ... scripts/bt_runs.py compare <portfolio id> [<single id> ...]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _p(run) -> dict:
    try:
        return json.loads(run.params_snapshot or "{}")
    except Exception:  # noqa: BLE001
        return {}


def _kind(run) -> str:
    p = _p(run)
    if p.get("portfolio") or p.get("is_portfolio") or p.get("slots") or "," in (run.symbol or "") \
            or not (run.symbol or "").strip():
        return "PORT"
    return "single"


def _flat(d, pre=""):
    out = {}
    if isinstance(d, dict):
        for k, v in d.items():
            out.update(_flat(v, f"{pre}{k}."))
    elif isinstance(d, list) and d and all(isinstance(x, dict) for x in d):
        for i, v in enumerate(d):
            out.update(_flat(v, f"{pre}{i}."))
    else:
        out[pre[:-1]] = d
    return out


async def load(ids=None, days=None):
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from backend.data.database import async_session
    from backend.data.models import BacktestRun
    async with async_session() as s:
        q = select(BacktestRun)
        if ids:
            q = q.where(BacktestRun.id.in_(ids)).options(selectinload(BacktestRun.trades))
        if days:
            q = q.where(BacktestRun.created_at >= datetime.utcnow() - timedelta(days=days))
        q = q.order_by(BacktestRun.created_at.desc())
        return list((await s.execute(q)).scalars().all())


def trades_by_key(run):
    g = defaultdict(list)
    for t in run.trades:
        g[(t.strategy_id or run.strategy_id, t.symbol)].append(t)
    for v in g.values():
        v.sort(key=lambda t: (t.entry_time, t.id))
    return g


def summ(ts):
    n = len(ts)
    pnl = sum(t.pnl or 0 for t in ts)
    w = sum(1 for t in ts if (t.pnl or 0) > 0)
    gp = sum(t.pnl for t in ts if (t.pnl or 0) > 0)
    gl = -sum(t.pnl for t in ts if (t.pnl or 0) < 0)
    return f"{n} tr win {w / n:.0%} pnl {pnl:+.2f} pf {gp / gl if gl else float('inf'):.2f}" if n else "0 tr"


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["list", "show", "compare"])
    ap.add_argument("ids", nargs="*")
    ap.add_argument("--days", type=float, default=2)
    ap.add_argument("--diff-params", action="store_true")
    a = ap.parse_args()
    if a.cmd == "list":
        for r in await load(days=a.days):
            print(f"{r.id[:8]} {r.created_at:%m-%d %H:%M} {_kind(r):6s} {r.strategy_id[:26]:26s} {str(r.symbol)[:28]:28s} "
                  f"{r.start_date:%Y-%m-%d}..{r.end_date:%Y-%m-%d} tr {r.total_trades} pnl {r.total_pnl} pf {r.profit_factor} "
                  f"| {(r.title or '')[:30]}")
        return 0
    runs = await load(ids=None, days=30)
    full = {r.id: r for r in runs}
    pick = [next((r for r in runs if r.id.startswith(i)), None) for i in a.ids]
    if any(p is None for p in pick):
        print("unknown id(s):", [i for i, p in zip(a.ids, pick) if p is None])
        return 1
    order = [p.id for p in pick]
    pick = await load(ids=order)
    pick.sort(key=lambda r: order.index(r.id))
    if a.cmd == "show":
        for r in pick:
            p = _p(r)
            print(f"== {r.id} {_kind(r)} {r.strategy_id} {r.symbol} {r.start_date}..{r.end_date}")
            print("   top-level params:", sorted(p.keys())[:60])
            for k, v in sorted(trades_by_key(r).items()):
                print(f"   {k[0]:26s} {k[1]:16s} {summ(v)}")
        return 0
    port, singles = pick[0], pick[1:]
    pt = trades_by_key(port)
    if not singles:   # same-dates single runs of the strategy x symbol pairs in the portfolio
        cands = [r for r in runs if _kind(r) == "single" and r.id != port.id
                 and r.start_date == port.start_date and r.end_date == port.end_date
                 and (r.strategy_id, r.symbol) in pt]
        latest = {}
        for r in sorted(cands, key=lambda r: r.created_at):
            latest[(r.strategy_id, r.symbol)] = r
        singles = await load(ids=[r.id for r in latest.values()])
    print(f"PORTFOLIO {port.id[:8]} {port.start_date:%Y-%m-%d}..{port.end_date:%Y-%m-%d} {summ(port.trades)}")
    for s in singles:
        st = trades_by_key(s).get((s.strategy_id, s.symbol), [])
        pp = pt.get((s.strategy_id, s.symbol), [])
        print(f"\n## {s.strategy_id} {s.symbol}: single {s.id[:8]} {summ(st)} | in portfolio {summ(pp)}")
        se = {(t.entry_time, t.direction): t for t in st}
        pe = {(t.entry_time, t.direction): t for t in pp}
        both = sorted(set(se) & set(pe))
        print(f"   same entries {len(both)}, single-only {len(set(se) - set(pe))}, portfolio-only {len(set(pe) - set(se))}")
        diff_exit = [k for k in both if (se[k].exit_time, round(se[k].exit_price or 0, 5)) !=
                     (pe[k].exit_time, round(pe[k].exit_price or 0, 5))]
        diff_sl = [k for k in both if round(se[k].stop_loss or 0, 5) != round(pe[k].stop_loss or 0, 5)]
        print(f"   of the same entries: different exit {len(diff_exit)}, different stop {len(diff_sl)}")
        for lab, keys in (("single-only", sorted(set(se) - set(pe))[:3]), ("portfolio-only", sorted(set(pe) - set(se))[:3]),
                          ("diff-exit", diff_exit[:3])):
            for k in keys:
                x = se.get(k) or pe.get(k)
                y = pe.get(k) if lab == "diff-exit" else None
                print(f"   {lab:14s} {k[0]} {k[1]} entry {x.entry_price} sl {x.stop_loss} exit {x.exit_time} {x.exit_reason} "
                      f"pnl {x.pnl}" + (f" | port exit {y.exit_time} {y.exit_reason} sl {y.stop_loss} pnl {y.pnl}" if y else ""))
        if a.diff_params:
            fs, fp = _flat(_p(s)), _flat(_p(port))
            d = [(k, fs.get(k), fp.get(k)) for k in sorted(set(fs) | set(fp)) if fs.get(k) != fp.get(k)]
            print(f"   params that differ ({len(d)}):")
            for k, x, y in d[:40]:
                print(f"     {k}: single={str(x)[:50]} port={str(y)[:50]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
