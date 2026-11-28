"""
scripts/bt_risk.py - which risk % a run asked for, which one each trade actually used, and why.

    cd /opt/algoedge/<inst>/app && PYTHONPATH=. ../venv/bin/python scripts/bt_risk.py [<run id>]
"""
from __future__ import annotations

import asyncio
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

KEYS = ("risk_per_trade_pct", "max_risk_hard_cap_pct", "max_risk_per_trade_pct", "max_lot", "max_lot_size",
        "sizing_basis", "max_account_leverage", "max_open_risk_pct", "max_total_risk_pct")


def pick(d):
    return {k: d[k] for k in KEYS if isinstance(d, dict) and k in d}


async def main() -> int:
    from sqlalchemy import select

    import bt_runs as B
    from backend.data.database import async_session
    from backend.data.models import UserConfigModel as UC

    print("== SAVED SETTINGS (what Settings shows)")
    async with async_session() as db:
        for c in (await db.execute(select(UC))).scalars():
            cfg = json.loads(c.config_json or "{}")
            print(f" user {c.user_id[:8]} account risk: {pick(cfg.get('risk') or {})} prop_firm: {pick(cfg.get('prop_firm') or {})}")
            for s in cfg.get("instrument_slots") or []:
                own = pick(s.get("risk") or {})
                print(f"   slot {s.get('strategy_id', '')[:26]:26s} {str(s.get('symbol'))[:12]:12s} enabled={s.get('enabled')} "
                      f"risk_per_trade_pct={s.get('risk_per_trade_pct')} risk-block={own}")

    runs = await B.load(days=30)
    r0 = next((r for r in runs if r.id.startswith(sys.argv[1])), None) if len(sys.argv) > 1 else runs[0]
    run = (await B.load(ids=[r0.id]))[0]
    p = B._p(run)
    print(f"\n== RUN {run.id[:8]} {run.title} ({run.created_at:%m-%d %H:%M})")
    print(f" page values sent: {pick(p)}")
    print(f" risk_config: {pick(p.get('risk_config') or {})}")
    for row in p.get("symbols") or []:
        print(f"   row {row.get('strategy_id', '')[:26]:26s} {str(row.get('symbol'))[:12]:12s} row risk: {pick(row.get('risk') or {})}")
    per = defaultdict(list)
    for t in run.trades:
        if t.pnl_r and t.balance_before and abs(t.pnl_r) > 0.05:
            per[(t.strategy_id, t.symbol)].append(abs(t.pnl / t.pnl_r) / t.balance_before * 100)
    print("\n risk actually used per trade (cash risked / balance at entry):")
    for k, v in sorted(per.items()):
        v.sort()
        print(f"   {k[0][:26]:26s} {k[1][:12]:12s} median {statistics.median(v):5.2f}%  min {v[0]:5.2f}%  max {v[-1]:5.2f}%  n={len(v)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
