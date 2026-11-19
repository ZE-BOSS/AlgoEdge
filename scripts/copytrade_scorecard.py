"""Per-channel scorecard for the Telegram copy trader (Phase 1). READ-ONLY.

Joins copytrade_channels / _signals / _orders from an instance's SQLite DB with
that terminal's MT5 deal history (orders store the ticket, not the P&L), and
prints one line per channel plus a JSON dump.

    cd /opt/algoedge/<inst>/app && PYTHONPATH=. ../venv/bin/python scripts/copytrade_scorecard.py <inst>

A "trade" is one signal: all its legs together. Its R is net P&L divided by the
money its legs would have lost at their original stops.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import sqlite3
import sys
from collections import Counter, defaultdict

DB = "algoedge.db"
BE_USD = 0.50            # |net| at or under this is a breakeven, not a win or loss


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    m = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - m) / d, (c + m) / d)


def max_drawdown(pnls: list[float]) -> float:
    peak = eq = dd = 0.0
    for p in pnls:
        eq += p
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    return dd


def max_losing_streak(pnls: list[float]) -> int:
    best = cur = 0
    for p in pnls:
        cur = cur + 1 if p < -BE_USD else 0
        best = max(best, cur)
    return best


def load(db_path: str):
    c = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    have = {r[0] for r in c.execute("select name from sqlite_master where type='table'")}
    if "copytrade_channels" not in have:
        return None
    chans = {r["id"]: dict(r) for r in c.execute("select id,title,username,enabled,created_at from copytrade_channels")}
    sigs = [dict(r) for r in c.execute(
        "select id,channel_id,kind,status,detail,symbol,created_at from copytrade_signals")]
    orders = [dict(r) for r in c.execute(
        "select signal_id,channel_id,leg,ticket,kind,symbol,side,volume,entry,sl,tp,status,created_at "
        "from copytrade_orders")]
    settings = [dict(r) for r in c.execute("select enabled,dry_run from copytrade_settings")]
    return chans, sigs, orders, settings


class deal_index(dict):
    """position_id -> its deals, fetched per position the way position_manager
    does it. A date-range history call through the Linux bridge came back empty."""

    def __init__(self, mt5, since=None):
        super().__init__()
        self.mt5 = mt5

    def get(self, ticket, default=None):
        if ticket not in self:
            self[ticket] = list(self.mt5.history_deals_get(position=int(ticket)) or [])
        return self[ticket] or (default if default is not None else [])


def leg_result(mt5, o: dict, deals: list):
    """(net_pnl, risk_usd, close_time) for a closed leg, else None."""
    outs = [d for d in deals if d.entry in (mt5.DEAL_ENTRY_OUT, mt5.DEAL_ENTRY_INOUT)]
    ins = [d for d in deals if d.entry == mt5.DEAL_ENTRY_IN]
    if not outs:
        return None
    net = sum(d.profit + d.commission + d.swap + getattr(d, "fee", 0.0) for d in deals)
    entry = ins[0].price if ins else (o["entry"] or 0.0)
    risk = None
    if o["sl"]:
        typ = mt5.ORDER_TYPE_BUY if o["side"] == "BUY" else mt5.ORDER_TYPE_SELL
        loss = mt5.order_calc_profit(typ, o["symbol"], float(o["volume"]), float(entry), float(o["sl"]))
        if loss is not None:
            risk = abs(loss)
    return net, risk, max(d.time for d in outs)


def scorecard(inst: str, mt5) -> dict:
    data = load(DB)
    if data is None:
        return {"instance": inst, "note": "no copytrade tables"}
    chans, sigs, orders, settings = data
    if not orders:
        since = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    else:
        since = min(dt.datetime.fromisoformat(str(o["created_at"])) for o in orders).replace(
            tzinfo=dt.timezone.utc) - dt.timedelta(days=1)
    deals = deal_index(mt5, since)

    by_signal: dict[int, list] = defaultdict(list)
    for o in orders:
        by_signal[o["signal_id"]].append(o)

    rows = []
    for cid, ch in chans.items():
        csigs = [s for s in sigs if s["channel_id"] == cid]
        opens = [s for s in csigs if s["kind"] == "OPEN"]
        status = Counter(s["status"] for s in opens)
        skip_reasons = Counter((s["detail"] or "?").split(":")[0][:60] for s in opens if s["status"] == "SKIPPED")
        trades = []                                   # (close_time, net, R or None, signal_id)
        part_filled = 0                               # some limit legs cancelled unfilled
        never_filled = 0
        for s in opens:
            legs = [o for o in by_signal.get(s["id"], []) if o["ticket"] and o["status"] != "PAPER"]
            if not legs:
                never_filled += 1
                continue
            res = [leg_result(mt5, o, deals.get(int(o["ticket"]), [])) for o in legs]
            if all(r is None for r in res):
                # Every leg unfilled (cancelled limits) or still open.
                never_filled += 1
                continue
            if any(r is None for r in res):
                part_filled += 1
            done = [r for r in res if r is not None]
            net = sum(r[0] for r in done)
            risks = [r[1] for r in done if r[1]]
            R = net / sum(risks) if risks and len(risks) == len(done) else None
            trades.append((max(r[2] for r in done), net, R, s["id"]))
        trades.sort()
        pnls = [t[1] for t in trades]
        Rs = [t[2] for t in trades if t[2] is not None]
        wins = sum(1 for p in pnls if p > BE_USD)
        losses = sum(1 for p in pnls if p < -BE_USD)
        decided = wins + losses
        gw = sum(p for p in pnls if p > 0)
        gl = -sum(p for p in pnls if p < 0)
        lo, hi = wilson(wins, decided)
        best = max(trades, key=lambda t: t[1]) if trades else None
        worst = min(trades, key=lambda t: t[1]) if trades else None
        rows.append({
            "channel": ch["title"] or ch["username"] or str(cid), "enabled": bool(ch["enabled"]),
            "added": str(ch["created_at"])[:10],
            "signals_open": len(opens), "status": dict(status), "skip_reasons": dict(skip_reasons.most_common(6)),
            "closed_trades": len(trades), "partly_filled": part_filled, "no_fill_or_open": never_filled,
            "wins": wins, "losses": losses, "breakeven": len(trades) - decided,
            "win_rate": round(wins / decided, 3) if decided else None,
            "win_rate_95ci": [round(lo, 3), round(hi, 3)] if decided else None,
            "net_usd": round(sum(pnls), 2), "profit_factor": round(gw / gl, 2) if gl else None,
            "expectancy_R": round(sum(Rs) / len(Rs), 3) if Rs else None,
            "avg_win_usd": round(gw / wins, 2) if wins else None,
            "avg_loss_usd": round(gl / losses, 2) if losses else None,
            "best_usd": round(best[1], 2) if best else None,
            "best_date": dt.datetime.utcfromtimestamp(best[0]).strftime("%Y-%m-%d") if best else None,
            "worst_usd": round(worst[1], 2) if worst else None,
            "worst_date": dt.datetime.utcfromtimestamp(worst[0]).strftime("%Y-%m-%d") if worst else None,
            "max_dd_usd": round(max_drawdown(pnls), 2),
            "max_losing_streak": max_losing_streak(pnls),
            "enough_data": len(trades) >= 30,
        })
    rows.sort(key=lambda r: (r["closed_trades"] < 30, -(r["expectancy_R"] or -9), -r["net_usd"]))
    return {"instance": inst, "settings": settings, "since": since.isoformat(), "channels": rows}


def main() -> None:
    inst = sys.argv[1] if len(sys.argv) > 1 else "?"
    import backend  # noqa: F401  installs the MT5 bridge stand-in on Linux
    import MetaTrader5 as mt5
    out = scorecard(inst, mt5)
    with open(f"/tmp/scorecard_{inst}.json", "w") as f:
        json.dump(out, f, indent=1, default=str)
    print("SC", inst, "settings", out.get("settings"), out.get("note", ""))
    for r in out.get("channels", []):
        print("SC|" + "|".join(str(r[k]) for k in (
            "channel", "enabled", "added", "signals_open", "closed_trades", "partly_filled", "no_fill_or_open", "wins", "losses",
            "breakeven", "win_rate", "win_rate_95ci", "net_usd", "profit_factor", "expectancy_R",
            "avg_win_usd", "avg_loss_usd", "best_usd", "best_date", "worst_usd", "worst_date",
            "max_dd_usd", "max_losing_streak")) + "|" + json.dumps(r["status"]) + "|" + json.dumps(r["skip_reasons"]))


if __name__ == "__main__":
    main()
