"""
scripts/copytrade_api_bt.py

Runs the Copy Trading page's channel backtest from the server shell: signs a
5-minute token for the channel's owner and calls the instance's own API
(POST /api/copytrade/backtest, then polls), so the Telegram session the copier
already holds is reused, exactly as scripts/copytrade_export.py does.

    cd /opt/algoedge/demo/app && sudo -u algoedge ../venv/bin/python scripts/copytrade_api_bt.py \
        --channel "Easy Forex" --days 90 --compare-chase --optimise --out /tmp/easy.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import urllib.request
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
os.chdir(ROOT)
try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    load_dotenv(ROOT.parent / "instance.env")
except ImportError:
    pass

from copytrade_export import token  # noqa: E402


async def find(channel: str):
    from sqlalchemy import select

    from backend.copytrade.models import CopyTradeChannel
    from backend.data.database import async_session

    async with async_session() as s:
        rows = (await s.execute(select(CopyTradeChannel))).scalars().all()
    want = channel.lower().lstrip("@")
    hit = [r for r in rows if want in (r.title or "").lower() or want == (r.username or "").lower()]
    if not want and rows:
        return str(rows[0].user_id), rows[0]
    if not hit:
        raise SystemExit("channel not found; have: " + ", ".join(r.title for r in rows))
    return str(hit[0].user_id), hit[0]


def call(port, method, path, uid, body=None, timeout=600):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method=method,
                                 headers={"Authorization": "Bearer " + token(uid), "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", default="")
    ap.add_argument("--dialogs", action="store_true", help="list the Telegram account's groups/channels, newest joined first")
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--balance", type=float, default=10000.0)
    ap.add_argument("--risk", type=float, default=1.0)
    ap.add_argument("--compare-chase", action="store_true")
    ap.add_argument("--optimise", action="store_true")
    ap.add_argument("--range", action="store_true", help="only show how far back messages/prices go")
    ap.add_argument("--port", type=int, default=int(os.getenv("API_PORT", "8001")))
    ap.add_argument("--out")
    a = ap.parse_args()

    if a.dialogs:
        uid, _ = asyncio.run(find(""))
        ds = call(a.port, "GET", "/api/copytrade/dialogs", uid)
        ds = ds.get("dialogs", ds) if isinstance(ds, dict) else ds
        for d in sorted(ds, key=lambda d: str(d.get("joined") or ""), reverse=True)[:40]:
            print(f"{str(d.get('joined') or '')[:16]:16s} {'ADDED ' if d.get('added') or d.get('watched') else '      '}"
                  f"{d.get('chat_id')}  {(d.get('title') or '')[:40]:40s} @{d.get('username') or '-'}")
        return 0
    uid, ch = asyncio.run(find(a.channel))
    print(f"channel: {ch.title}  id {ch.id}  @{ch.username or 'private'}  copying={'on' if ch.enabled else 'off'}  rules={ch.rules or {}}")
    if a.range:
        r = call(a.port, "GET", f"/api/copytrade/backtest/range?channel_ids={ch.id}", uid)
        print(json.dumps(r, indent=1)[:3000])
        return 0
    body = {"channel_ids": [ch.id], "days": a.days, "balance": a.balance, "risk_pct": a.risk, "compound": True,
            "compare_chase": a.compare_chase, "optimise_exits": a.optimise}
    job = call(a.port, "POST", "/api/copytrade/backtest", uid, body)["job_id"]
    while True:
        time.sleep(3)
        j = call(a.port, "GET", f"/api/copytrade/backtest/{job}", uid)
        if j["status"] != "running":
            break
    if j["status"] != "done":
        print("ERROR", j.get("error"))
        return 1
    res = j["result"]
    if a.out:
        Path(a.out).write_text(json.dumps(res, default=str))
    pf = res["portfolio"]
    print("portfolio:", {k: pf.get(k) for k in ("end_balance", "net", "return_pct", "max_dd_pct", "trades",
                                                 "losing_days", "trading_days", "worst_day", "max_concurrent_trades")})
    for c in res["channels"]:
        keep = ("signals", "filled", "tp1_hit_rate", "split_win_rate", "net_r_split", "avg_r_split", "best_r", "worst_r",
                "max_dd_r", "max_losing_streak", "history_capped", "settings")
        print("channel:", {k: c.get(k) for k in keep})
        for x in c.get("chase_compare") or []:
            print("  late:", {k: x.get(k) for k in ("chase_rule", "tol_pct", "current", "filled", "skipped_late", "no_fill",
                                                     "split_win_rate", "avg_r_split", "net_r_split", "max_dd_r")})
        if c.get("optimise") is not None:
            o = c["optimise"]
            print("  tune:", None if not o else {"baseline_unseen": (o.get("baseline") or {}).get("test_R"), "best": o.get("best"),
                                                 "neighbours_unseen": o.get("neighbours_test_R"), "unseen_from": o.get("first_test_day")})
    tr = res.get("trades", [])
    print("trade status:", dict(Counter(t.get("status") for t in tr)))
    print("symbols:", dict(Counter(f"{t.get('symbol')}" for t in tr)))
    print("exits:", dict(Counter(t.get("exit_reason") for t in tr if t.get("status") == "FILLED")))
    by_month = Counter()
    for t in tr:
        if t.get("portfolio_pnl") is not None:
            by_month[(t.get("time") or "")[:7]] += t["portfolio_pnl"]
    print("monthly $:", {k: round(v, 2) for k, v in sorted(by_month.items())})
    print("first/last signal:", tr[0]["time"] if tr else None, tr[-1]["time"] if tr else None)
    print("method:", res.get("method"))
    print("BT_DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
