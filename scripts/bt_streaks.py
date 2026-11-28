"""
scripts/bt_streaks.py - the longest losing streak and the max drawdown of a saved run, trade by trade.

    cd /opt/algoedge/<inst>/app && PYTHONPATH=. ../venv/bin/python scripts/bt_streaks.py [<run id>]
    (no id: the newest run)
"""
from __future__ import annotations

import asyncio
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))


def longest(ts, sign):
    best, cur = [], []
    for t in ts:
        p = t.pnl or 0
        if (p < 0 and sign < 0) or (p > 0 and sign > 0):
            cur.append(t)
            if len(cur) > len(best):
                best = list(cur)
        else:
            cur = []
    return best


def show(title, run_ts):
    if not run_ts:
        print(f"{title}: none")
        return
    b0 = run_ts[0].balance_before or 0
    b1 = run_ts[-1].balance_after or 0
    print(f"{title}: {len(run_ts)} trades, {run_ts[0].exit_time:%Y-%m-%d %H:%M} -> {run_ts[-1].exit_time:%Y-%m-%d %H:%M}, "
          f"total {sum(t.pnl_r or 0 for t in run_ts):+.2f}R, account {b0:,.0f} -> {b1:,.0f} ({(b1 / b0 - 1) * 100 if b0 else 0:+.1f}%)")
    for t in run_ts:
        print(f"    {t.exit_time:%m-%d %H:%M} {t.strategy_id[:24]:24s} {t.symbol[:12]:12s} {t.direction:4s} "
              f"{t.exit_reason or '':16s} {t.pnl_r or 0:+6.2f}R  {t.pnl or 0:+11,.2f}  balance {t.balance_after or 0:,.0f}")


async def main() -> int:
    import bt_runs as B
    runs = await B.load(days=30)
    if len(sys.argv) > 1 and sys.argv[1] == "list":
        for r in runs[:12]:
            q = B._p(r)
            print(f"{r.id[:8]} {r.created_at:%m-%d %H:%M} risk {q.get('risk_per_trade_pct')}% maxDD {r.max_drawdown_pct}% "
                  f"trades {len(r.trades)} | {(r.title or '')[:40]}")
        return 0
    r0 = next((r for r in runs if r.id.startswith(sys.argv[1])), None) if len(sys.argv) > 1 else runs[0]
    run = (await B.load(ids=[r0.id]))[0]
    p = B._p(run)
    ts = sorted(run.trades, key=lambda t: (t.exit_time, t.id))
    print(f"run {run.id[:8]} {run.title} | risk {p.get('risk_per_trade_pct')}% | {len(ts)} trades")
    # max drawdown on the balance after each close
    peak_b, peak_i, mdd, span = (ts[0].balance_before or 0), -1, 0.0, (0, 0)
    for i, t in enumerate(ts):
        b = t.balance_after or 0
        if b > peak_b:
            peak_b, peak_i = b, i
        d = 1 - b / peak_b if peak_b else 0
        if d > mdd:
            mdd, span = d, (peak_i + 1, i)
    dd = ts[span[0]:span[1] + 1]
    losses = sum(1 for t in dd if (t.pnl or 0) < 0)
    print(f"\nMAX DRAWDOWN {mdd:.1%}: {len(dd)} trades from {dd[0].exit_time:%Y-%m-%d} to {dd[-1].exit_time:%Y-%m-%d}, "
          f"{losses} losses / {len(dd) - losses} wins in between, {sum(t.pnl_r or 0 for t in dd):+.2f}R")
    show("\nLONGEST LOSING STREAK (all trades together, in the order they closed)", longest(ts, -1))
    small = [t for t in ts if (t.pnl or 0) < 0 and (t.pnl_r or 0) > -0.5]
    print(f"\nlosing trades: {sum(1 for t in ts if (t.pnl or 0) < 0)}, of which {len(small)} lost less than 0.5R "
          f"(break-even, session/time exits, trailing stops)")
    g = defaultdict(list)
    for t in ts:
        g[(t.strategy_id, t.symbol)].append(t)
    print("\nper strategy x symbol: longest losing streak (R lost in it) | longest winning streak")
    for k, v in sorted(g.items(), key=lambda kv: -len(longest(kv[1], -1))):
        lo, wi = longest(v, -1), longest(v, 1)
        print(f"   {k[0][:26]:26s} {k[1][:12]:12s} L{len(lo):2d} ({sum(t.pnl_r or 0 for t in lo):+6.2f}R) | W{len(wi):2d}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
