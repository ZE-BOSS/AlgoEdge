#!/usr/bin/env python
"""
scripts/audit_saved_backtest.py

Can a saved backtest's dollar figures be trusted? Reads runs straight from the
app's database (read-only) and checks the arithmetic trade by trade.

    python scripts/audit_saved_backtest.py --list
    python scripts/audit_saved_backtest.py "Vol over Crash 750" "Vol over Boom 400"
    python scripts/audit_saved_backtest.py --id <backtest uuid>

For each run it answers:

  1. The ledger adds up: sum of trade P&L == the run's total, and each trade's
     balance_after == balance_before + pnl.
  2. Dollars per point are CONSISTENT across trades: every leg's
     pnl / (price move x lots) should give the same number (the symbol's
     tick_value / tick_size). A spread across trades means the P&L conversion
     changed mid-run (a DEFAULT fallback, a cache flip).
     With MT5 connected, the same number is read from symbol_info and compared.
  3. The risk actually taken per trade: |entry - stop| x $/point x lots, against
     what the settings asked for (balance x risk %). Trades risking much more
     than asked are the broker's MINIMUM LOT forcing a bigger position — the
     usual way a synthetic-index backtest inflates.
  4. Where the profit came from: average and median R, the share of P&L from
     the five biggest trades, winners over 10R.
  5. Compounding: the same trades re-priced at a FIXED risk (the opening
     balance x risk %), so a compounded headline can be compared with a flat one.

It changes nothing. Point it at a copy of the database if you prefer:
    --db path\\to\\algoedge.db
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _db_path(arg: str | None) -> Path:
    if arg:
        return Path(arg)
    env = ROOT / ".env"
    url = ""
    if env.exists():
        for line in env.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.startswith("DATABASE_URL="):
                url = line.split("=", 1)[1].strip()
    if url.startswith("sqlite"):
        return (ROOT / url.split(":///", 1)[1]).resolve()
    return ROOT / "algoedge.db"


def _mt5_info(symbol: str) -> dict | None:
    try:
        import MetaTrader5 as mt5
    except ImportError:
        return None
    if not mt5.initialize():
        return None
    info = mt5.symbol_info(symbol)
    if info is None:
        return None
    return {"tick_value": info.trade_tick_value, "tick_size": info.trade_tick_size,
            "volume_min": info.volume_min, "volume_step": info.volume_step,
            "contract_size": info.trade_contract_size, "point": info.point, "digits": info.digits}


def _legs(trade: sqlite3.Row) -> list[dict]:
    try:
        legs = json.loads(trade["sub_trades"] or "[]")
    except (TypeError, ValueError):
        legs = []
    return [leg for leg in legs if isinstance(leg, dict)]


def _f(x, default=0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def audit(con: sqlite3.Connection, run: sqlite3.Row) -> None:
    params = json.loads(run["params_snapshot"] or "{}")
    trades = con.execute("SELECT * FROM backtest_trades WHERE backtest_id=? ORDER BY entry_time",
                         (run["id"],)).fetchall()
    title = run["title"] or run["symbol"]
    print("=" * 78)
    print(f"{title}  [{run['strategy_id']}]  {run['start_date']} -> {run['end_date']}")
    print(f"  saved: {len(trades)} trades, total P&L ${_f(run['total_pnl']):,.2f}, "
          f"win rate {_f(run['win_rate']):.1f}%, max DD {_f(run['max_drawdown_pct']):.1f}%")
    if not trades:
        print("  no trades saved")
        return

    risk_pct = _f(params.get("risk_per_trade_pct") or params.get("risk_pct"), 1.0)
    basis = params.get("sizing_basis", "STATIC")
    opening = _f(trades[0]["balance_before"]) or _f(params.get("initial_balance"), 10000.0)
    static_base = _f(params.get("sizing_static_balance")) or opening
    print(f"  settings: risk {risk_pct}% per trade, sizing {basis}, opening balance ${opening:,.2f}")

    # 1. ledger
    total = sum(_f(t["pnl"]) for t in trades)
    chain_breaks = sum(1 for t in trades
                       if t["balance_before"] is not None and t["balance_after"] is not None
                       and abs(_f(t["balance_before"]) + _f(t["pnl"]) - _f(t["balance_after"])) > 0.05)
    print(f"\n  1. Ledger: sum of trades ${total:,.2f} vs saved ${_f(run['total_pnl']):,.2f} "
          f"({'OK' if abs(total - _f(run['total_pnl'])) < 1 else 'MISMATCH'}); "
          f"balance chain breaks: {chain_breaks}")

    # 2. dollars per point
    vpus, lots_seen = [], []
    for t in trades:
        for leg in _legs(t):
            vol = _f(leg.get("volume"))
            move = _f(leg.get("exit_price")) - _f(leg.get("entry_price"))
            pnl = _f(leg.get("pnl"))
            costs = _f(leg.get("commission")) + _f(leg.get("spread_cost")) + _f(leg.get("swap"))
            if vol > 0 and abs(move) > 0:
                vpus.append(abs((pnl + abs(costs)) / (move * vol)))
                lots_seen.append(vol)
    mt5 = _mt5_info(run["symbol"])
    if vpus:
        med = statistics.median(vpus)
        spread = (max(vpus) - min(vpus)) / med * 100 if med else 0
        print(f"  2. $ per 1.0 price move per lot: median {med:,.4f} "
              f"(spread across legs {spread:.1f}% — costs and slippage add a little)")
        if mt5:
            live = mt5["tick_value"] / mt5["tick_size"] if mt5["tick_size"] else 0
            print(f"     MT5 says {live:,.4f}  -> {'consistent' if live and abs(med - live) / live < 0.05 else 'DIFFERENT: P&L used other values'}")
            print(f"     min lot {mt5['volume_min']}, step {mt5['volume_step']}, contract {mt5['contract_size']}, "
                  f"point {mt5['point']}")
        else:
            print("     (MT5 not available here; run on the trading server to cross-check)")
        print(f"     lots used: min {min(lots_seen)}, median {statistics.median(lots_seen)}, max {max(lots_seen)}")
    else:
        med = 0.0
        print("  2. no leg volumes saved; cannot check $/point")

    # 3. risk actually taken
    rows = []
    for t in trades:
        legs = _legs(t)
        vol = sum(_f(leg.get("volume")) for leg in legs)
        dist = abs(_f(t["entry_price"]) - _f(t["stop_loss"]))
        base = _f(t["balance_before"]) if basis in ("BALANCE", "EQUITY") else static_base
        asked = base * risk_pct / 100
        taken = dist * med * vol if med and vol else None
        rows.append((t, asked, taken))
    over = [(t, a, k) for t, a, k in rows if k and a and k > 1.5 * a]
    takens = [k / a for _, a, k in rows if k and a]
    if takens:
        print(f"  3. Risk taken / risk asked: median {statistics.median(takens):.2f}x, max {max(takens):.2f}x; "
              f"{len(over)} of {len(rows)} trades risked >1.5x what was asked"
              + ("  <- minimum lot forcing larger positions" if over else ""))

    # 4. where the profit came from
    rs = [_f(t["pnl_r"]) for t in trades if t["pnl_r"] is not None]
    pnls = sorted((_f(t["pnl"]) for t in trades), reverse=True)
    if rs:
        print(f"  4. R per trade: mean {statistics.mean(rs):+.2f}R, median {statistics.median(rs):+.2f}R, "
              f"best {max(rs):+.1f}R, worst {min(rs):+.1f}R; winners over 10R: {sum(1 for r in rs if r > 10)}")
    if total:
        print(f"     top 5 trades = {sum(pnls[:5]) / total * 100:.0f}% of total P&L")

    # 5. compounding
    if rs:
        flat = sum(rs) * opening * risk_pct / 100
        print(f"  5. Same trades at a FIXED ${opening * risk_pct / 100:,.2f} risk each: ${flat:,.2f} "
              f"({flat / opening * 100:+.1f}%)  vs saved ${_f(run['total_pnl']):,.2f}")
        if basis in ("BALANCE", "EQUITY"):
            print("     (the saved figure compounds: each trade risked a % of a growing balance)")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("titles", nargs="*", help="run titles or symbols to match (case-insensitive substring)")
    ap.add_argument("--id", action="append", default=[])
    ap.add_argument("--db")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    db = _db_path(args.db)
    if not db.exists():
        sys.exit(f"database not found: {db}")
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    runs = con.execute("SELECT * FROM backtest_runs ORDER BY created_at DESC").fetchall()
    if args.list or (not args.titles and not args.id):
        for r in runs:
            print(f"{r['id']}  {str(r['created_at'])[:16]}  {(r['title'] or r['symbol'])[:28]:28}  "
                  f"{r['strategy_id']:20} {r['total_trades'] or 0:5} trades  ${_f(r['total_pnl']):>12,.2f}")
        return
    picked = [r for r in runs if r["id"] in args.id or any(
        q.lower() in (r["title"] or "").lower() or q.lower() in (r["symbol"] or "").lower() for q in args.titles)]
    if not picked:
        sys.exit("no matching runs; use --list")
    for r in picked:
        audit(con, r)


if __name__ == "__main__":
    main()
